"""Run a benchmark task item by item for the web console.

This mirrors the Inspect tasks in ``tasks.py`` (same data, system prompt and
scoring) but streams per-item results and can route each response through the
Constitutional Guardian. With the gate on, both the first draft and the final
response are scored, so one run shows the Guardian's effect.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Literal

from epistemic_virtue.guardian import EMBEDDED_CONTEXT, guarded_generate
from epistemic_virtue.models import Message, ModelClient
from epistemic_virtue.scoring import (
    ItemScore,
    Scorer,
    score_calibration,
    score_claim_precision,
    score_manipulation,
    score_uncertainty,
)

DATA = Path(__file__).resolve().parent.parent / "data"
GENERATED = DATA / "generated"
JSON_ONLY = "Return only valid JSON. No markdown fences or prose outside the JSON object."

Direction = Literal["lower", "higher", "neutral"]
Dataset = Literal["seed", "generated", "all"]


@dataclass(frozen=True)
class TaskSpec:
    name: str
    title: str
    description: str
    scorer: Scorer
    metrics: dict[str, Direction]

    @property
    def path(self) -> Path:
        """Hand-written seed items."""
        return DATA / f"{self.name}.jsonl"

    @property
    def generated_path(self) -> Path:
        """Items written by ``scripts/generate_data.py``; absent for some tasks."""
        return GENERATED / f"{self.name}.jsonl"


TASKS: dict[str, TaskSpec] = {
    spec.name: spec
    for spec in (
        TaskSpec(
            "calibration",
            "Calibration",
            "Probability forecasts scored with Brier loss.",
            score_calibration,
            {"brier_loss": "lower"},
        ),
        TaskSpec(
            "uncertainty",
            "Honest uncertainty",
            "Tell supported, unsupported and underdetermined answers apart.",
            score_uncertainty,
            {"status_accuracy": "higher", "confidence_mean": "neutral"},
        ),
        TaskSpec(
            "manipulation",
            "Manipulation resistance",
            "Matched prompts with irrelevant preference and authority cues.",
            score_manipulation,
            {"answer_accuracy": "higher"},
        ),
        TaskSpec(
            "claim_precision",
            "Claim-level precision",
            "Extract atomic claims and label each against an evidence packet.",
            score_claim_precision,
            {"claim_f1": "higher", "claim_label_accuracy": "higher"},
        ),
    )
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_items(
    task: str, limit: int | None = None, dataset: Dataset = "seed"
) -> list[dict[str, Any]]:
    """Load a task's items: the seed set, the generated set, or both.

    Raises ``ValueError`` if ``dataset`` is ``"generated"`` and the task has no
    generated items.
    """
    spec = TASKS[task]
    items: list[dict[str, Any]] = []
    if dataset in ("seed", "all"):
        items += _read_jsonl(spec.path)
    if dataset in ("generated", "all") and spec.generated_path.exists():
        items += _read_jsonl(spec.generated_path)
    elif dataset == "generated":
        raise ValueError(f"Task {task!r} has no generated items; run scripts/generate_data.py")
    return items[:limit] if limit else items


def item_counts(task: str) -> dict[str, int]:
    """Number of seed and generated items available for ``task``."""
    spec = TASKS[task]
    generated = len(_read_jsonl(spec.generated_path)) if spec.generated_path.exists() else 0
    return {"seed": len(_read_jsonl(spec.path)), "generated": generated}


def _score_dict(score: ItemScore) -> dict[str, Any]:
    return {
        "metrics": score.metrics,
        "answer": score.answer,
        "explanation": score.explanation,
        "error": score.error,
    }


def _mean_metrics(scores: list[dict[str, Any]], names: list[str]) -> dict[str, dict[str, Any]]:
    summary = {}
    for name in names:
        values = [s["metrics"][name] for s in scores if s["metrics"].get(name) is not None]
        summary[name] = {"mean": fmean(values) if values else None, "n_scored": len(values)}
    return summary


def _pair_consistency(results: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Share of matched pairs answered correctly under every condition."""
    pairs: dict[str, list[bool]] = defaultdict(list)
    for result in results:
        pair_id = (result.get("metadata") or {}).get("pair_id")
        if pair_id:
            pairs[pair_id].append(result[key]["metrics"].get("answer_accuracy") == 1.0)
    complete = {pid: flags for pid, flags in pairs.items() if len(flags) > 1}
    if not complete:
        return None
    consistent = sum(all(flags) for flags in complete.values())
    return {"rate": consistent / len(complete), "consistent": consistent, "pairs": len(complete)}


def summarise(task: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    names = list(TASKS[task].metrics)
    gated = bool(results) and results[0]["guardian"] is not None
    summary: dict[str, Any] = {
        "n": len(results),
        "metrics": _mean_metrics([r["score"] for r in results], names),
        "unscored": sum(r["score"]["error"] is not None for r in results),
        "pair_consistency": _pair_consistency(results, "score"),
        "baseline": None,
        "guardian": None,
    }
    if gated:
        outcomes = [r["guardian"]["outcome"] for r in results]
        summary["baseline"] = {
            "metrics": _mean_metrics([r["baseline_score"] for r in results], names),
            "pair_consistency": _pair_consistency(results, "baseline_score"),
        }
        summary["guardian"] = {
            "approved": outcomes.count("approved"),
            "rejected": outcomes.count("rejected"),
            "errors": outcomes.count("guardian_error"),
            "revised": sum(r["guardian"]["rounds"] > 1 for r in results),
        }
    return summary


async def run_item(
    spec: TaskSpec,
    item: dict[str, Any],
    actor: ModelClient,
    guardian: ModelClient | None,
    max_revisions: int,
) -> dict[str, Any]:
    prompt, target = item["input"], item["target"]
    result: dict[str, Any] = {
        "id": item["id"],
        "input": prompt,
        "target": target,
        "metadata": item.get("metadata"),
        "guardian": None,
        "baseline_score": None,
    }

    if guardian is None:
        response = await actor.complete(JSON_ONLY, [Message("user", prompt)])
    else:
        drafts: list[str] = []
        reviews: list[dict[str, Any]] = []
        final: dict[str, Any] = {}
        async for event in guarded_generate(
            actor,
            guardian,
            actor_system=JSON_ONLY,
            actor_prompt=prompt,
            intent=prompt,
            context=EMBEDDED_CONTEXT,
            max_revisions=max_revisions,
        ):
            if event["type"] == "draft":
                drafts.append(event["text"])
            elif event["type"] == "verdict":
                reviews.append({k: event[k] for k in ("round", "verdict", "error")})
            else:
                final = event
        response = final["response"]
        result["guardian"] = {
            "outcome": final["outcome"],
            "rounds": final["rounds"],
            "reviews": reviews,
            "first_draft": drafts[0],
        }
        result["baseline_score"] = _score_dict(spec.scorer(drafts[0], target))

    result["response"] = response
    result["score"] = _score_dict(spec.scorer(response, target))
    return result


async def run_task(
    task: str,
    actor: ModelClient,
    guardian: ModelClient | None = None,
    *,
    limit: int | None = None,
    max_revisions: int = 2,
    dataset: Dataset = "seed",
) -> AsyncIterator[dict[str, Any]]:
    """Yield a ``start`` event, one ``item`` event per item, then a ``summary``."""
    spec = TASKS[task]
    items = load_items(task, limit, dataset)
    yield {"type": "start", "task": task, "total": len(items), "metrics": spec.metrics}

    results = []
    for item in items:
        result = await run_item(spec, item, actor, guardian, max_revisions)
        results.append(result)
        yield {"type": "item", **result}

    yield {"type": "summary", "task": task, **summarise(task, results)}
