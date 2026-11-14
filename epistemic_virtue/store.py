"""Saved evaluation runs.

Every evaluation is written to disk as one self-contained JSON record: the
settings needed to repeat it, provenance (code commit, package versions,
prompt and item hashes), call and token counts, the aggregate summary and
every item's prompt, response, score and Guardian verdicts.

Runs that fail or are interrupted are saved too, with the items finished so
far, so a partial result is never mistaken for a complete one.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from epistemic_virtue.guardian import GUARDIAN_SYSTEM_PROMPT
from epistemic_virtue.models import Metered
from epistemic_virtue.runner import JSON_ONLY, TASKS, summarise

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR_ENV = "EVE_RUNS_DIR"
SCHEMA_VERSION = 1
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[a-z_]+-[0-9a-f]{6}$")


class RunNotFound(LookupError):
    """No saved run has this id."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def items_hash(items: list[dict[str, Any]]) -> str:
    """Hash of the exact items a run used: ids, prompts and gold targets, in order."""
    canonical = [[item["id"], item["input"], item["target"]] for item in items]
    return _sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")))


def provenance(items: list[dict[str, Any]], gated: bool) -> dict[str, Any]:
    """What a reader needs to check that two runs are comparable."""
    status = _git("status", "--porcelain")
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(status) if status is not None else None,
        "package_version": _version("epistemic-virtue-eval"),
        "inspect_ai_version": _version("inspect-ai"),
        "python_version": platform.python_version(),
        "items_sha256": items_hash(items),
        "system_prompt_sha256": _sha256(JSON_ONLY),
        "guardian_prompt_sha256": _sha256(GUARDIAN_SYSTEM_PROMPT) if gated else None,
        "generation_settings": "provider defaults",
    }


class RunStore:
    """A directory of run records, one JSON file per run."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(os.environ.get(RUNS_DIR_ENV) or ROOT / "runs")

    def _path(self, run_id: str) -> Path:
        if not RUN_ID_PATTERN.match(run_id):  # also blocks path traversal
            raise RunNotFound(run_id)
        return self.root / f"{run_id}.json"

    def new_id(self, task: str, started: datetime) -> str:
        return f"{started:%Y%m%dT%H%M%SZ}-{task}-{secrets.token_hex(3)}"

    def save(self, record: dict[str, Any]) -> Path:
        """Write atomically, so a crash never leaves a half-written record."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(record["run_id"])
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)
        return path

    def load(self, run_id: str) -> dict[str, Any]:
        path = self._path(run_id)
        if not path.exists():
            raise RunNotFound(run_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, run_id: str) -> None:
        path = self._path(run_id)
        if not path.exists():
            raise RunNotFound(run_id)
        path.unlink()

    def list(self) -> list[dict[str, Any]]:
        """Every run without its items, newest first."""
        if not self.root.exists():
            return []
        runs = []
        for path in self.root.glob("*.json"):
            if RUN_ID_PATTERN.match(path.stem):
                record = json.loads(path.read_text(encoding="utf-8"))
                record["n_items"] = len(record.pop("items"))
                runs.append(record)
        return sorted(runs, key=lambda r: (r["started_at"], r["run_id"]), reverse=True)


async def record_run(
    events: AsyncIterator[dict[str, Any]],
    *,
    store: RunStore,
    config: dict[str, Any],
    actor: Metered,
    guardian: Metered | None,
) -> AsyncIterator[dict[str, Any]]:
    """Pass a run's events through, then save the run and emit a ``saved`` event.

    ``config`` holds the settings that repeat the run: ``task``, ``dataset``,
    ``limit``, ``model``, ``guardian_model`` and ``max_revisions``.
    """
    started = _now()
    run_id = store.new_id(config["task"], started)
    items: list[dict[str, Any]] = []
    summary: dict[str, Any] | None = None
    status, error = "completed", None

    def save() -> None:
        finished = _now()
        store.save(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "status": status,
                "error": error,
                "started_at": started.isoformat(timespec="milliseconds"),
                "finished_at": finished.isoformat(timespec="milliseconds"),
                "duration_seconds": round((finished - started).total_seconds(), 3),
                "config": config,
                "provenance": provenance(items, gated=guardian is not None),
                "usage": {
                    "actor": actor.usage.to_dict(),
                    "guardian": guardian.usage.to_dict() if guardian else None,
                },
                "metric_directions": TASKS[config["task"]].metrics,
                # A partial run is summarised over the items that finished.
                "summary": summary or summarise(config["task"], items),
                "items": items,
            }
        )

    try:
        async for event in events:
            if event["type"] == "item":
                items.append({k: v for k, v in event.items() if k != "type"})
            elif event["type"] == "summary":
                summary = {k: v for k, v in event.items() if k not in ("type", "task")}
            yield event
    except Exception as exc:
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        save()
        raise
    except BaseException:  # cancelled, or the client went away mid-run
        status, error = "interrupted", "The run was stopped before it finished."
        save()
        raise

    save()
    yield {"type": "saved", "run_id": run_id}


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Compare two runs of the same task, metric by metric and item by item.

    Raises ``ValueError`` if the runs are of different tasks.
    """
    task = a["config"]["task"]
    if b["config"]["task"] != task:
        raise ValueError(f"Cannot compare a {task} run with a {b['config']['task']} run")

    metrics = {}
    for name, direction in TASKS[task].metrics.items():
        mean_a, mean_b = (r["summary"]["metrics"][name]["mean"] for r in (a, b))
        delta = mean_b - mean_a if mean_a is not None and mean_b is not None else None
        metrics[name] = {"a": mean_a, "b": mean_b, "delta": delta, "direction": direction}

    items_a = {item["id"]: item for item in a["items"]}
    items_b = {item["id"]: item for item in b["items"]}
    shared = [item_id for item_id in items_a if item_id in items_b]
    differing = [
        {
            "id": item_id,
            "a": {
                "metrics": items_a[item_id]["score"]["metrics"],
                "answer": items_a[item_id]["score"]["answer"],
            },
            "b": {
                "metrics": items_b[item_id]["score"]["metrics"],
                "answer": items_b[item_id]["score"]["answer"],
            },
        }
        for item_id in shared
        if items_a[item_id]["score"]["metrics"] != items_b[item_id]["score"]["metrics"]
    ]
    return {
        "task": task,
        "a": {k: a[k] for k in ("run_id", "status", "config")},
        "b": {k: b[k] for k in ("run_id", "status", "config")},
        "same_items": a["provenance"]["items_sha256"] == b["provenance"]["items_sha256"],
        "shared_items": len(shared),
        "only_a": len(items_a) - len(shared),
        "only_b": len(items_b) - len(shared),
        "metrics": metrics,
        "differing_items": differing,
    }
