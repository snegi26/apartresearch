"""Web console: run the benchmark and the Constitutional Guardian from a browser.

Start with ``eve-web`` (or ``python -m epistemic_virtue.web.app``). The server
binds to localhost by default: anyone who can reach it can spend the API
credits of whichever provider keys are in its environment.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from epistemic_virtue import runner
from epistemic_virtue.guardian import (
    ACTOR_SYSTEM_PROMPT,
    GUARDIAN_SYSTEM_PROMPT,
    MAX_REVISIONS_LIMIT,
    build_actor_prompt,
    guarded_generate,
    review,
)
from epistemic_virtue.models import (
    DEMO_ACTOR,
    DEMO_GUARDIAN,
    MODEL_ID_PATTERN,
    Metered,
    get_client,
)
from epistemic_virtue.store import RunNotFound, RunStore, compare, record_run

logger = logging.getLogger(__name__)

_MARKUP = re.compile(r"\[/?[a-z ]+\]")

STATIC = Path(__file__).resolve().parent / "static"
PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

ModelId = Annotated[str, StringConstraints(pattern=MODEL_ID_PATTERN.pattern)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
Context = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50_000)]
Revisions = Annotated[int, Field(ge=0, le=MAX_REVISIONS_LIMIT)]


class ReviewRequest(BaseModel):
    intent: Text
    context: Context = ""
    draft: Text
    guardian_model: ModelId = DEMO_GUARDIAN


class GuardedRunRequest(BaseModel):
    intent: Text
    context: Context = ""
    actor_model: ModelId = DEMO_ACTOR
    guardian_model: ModelId = DEMO_GUARDIAN
    max_revisions: Revisions = 2


class EvalRequest(BaseModel):
    task: str
    model: ModelId = DEMO_ACTOR
    guardian_model: ModelId | None = None
    dataset: runner.Dataset = "seed"
    limit: Annotated[int, Field(ge=1, le=1000)] | None = None
    max_revisions: Revisions = 2


def _load_run(run_id: str) -> dict[str, Any]:
    try:
        return RunStore().load(run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=f"No saved run {run_id!r}") from exc


def _client(model_id: str):
    try:
        return get_client(model_id)
    except Exception as exc:  # noqa: BLE001 - unknown provider, missing package or key
        reason = _MARKUP.sub("", str(exc))  # Inspect errors carry rich-text markup
        detail = f"Cannot load model {model_id!r}: {reason}"
        raise HTTPException(status_code=400, detail=detail) from exc


async def _ndjson(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Serialise events as newline-delimited JSON; report failures in-band."""
    try:
        async for event in events:
            yield json.dumps(event) + "\n"
    except Exception as exc:  # noqa: BLE001 - provider errors surface mid-stream
        logger.exception("Run failed")
        yield json.dumps({"type": "error", "message": f"{type(exc).__name__}: {exc}"}) + "\n"


def _stream(events: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    return StreamingResponse(_ndjson(events), media_type="application/x-ndjson")


def create_app() -> FastAPI:
    app = FastAPI(title="Epistemic Virtue Eval", version="0.2.0")

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        presets = json.loads((runner.DATA / "guardian_presets.json").read_text(encoding="utf-8"))
        return {
            "tasks": [
                {
                    "name": spec.name,
                    "title": spec.title,
                    "description": spec.description,
                    "metrics": spec.metrics,
                    "items": runner.item_counts(spec.name),
                }
                for spec in runner.TASKS.values()
            ],
            "providers": {name: bool(os.environ.get(key)) for name, key in PROVIDER_KEYS.items()},
            "provider_keys": PROVIDER_KEYS,
            "demo": {"actor": DEMO_ACTOR, "guardian": DEMO_GUARDIAN},
            "max_revisions_limit": MAX_REVISIONS_LIMIT,
            "guardian_prompt": GUARDIAN_SYSTEM_PROMPT,
            "presets": presets,
        }

    @app.post("/api/guardian/review")
    async def guardian_review(request: ReviewRequest) -> dict[str, Any]:
        guardian = _client(request.guardian_model)
        try:
            result = await review(guardian, request.intent, request.context, request.draft)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Guardian review failed")
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
        return result.to_dict()

    @app.post("/api/guardian/run")
    async def guardian_run(request: GuardedRunRequest) -> StreamingResponse:
        events = guarded_generate(
            _client(request.actor_model),
            _client(request.guardian_model),
            actor_system=ACTOR_SYSTEM_PROMPT,
            actor_prompt=build_actor_prompt(request.intent, request.context),
            intent=request.intent,
            context=request.context,
            max_revisions=request.max_revisions,
        )
        return _stream(events)

    @app.post("/api/eval/run")
    async def eval_run(request: EvalRequest) -> StreamingResponse:
        if request.task not in runner.TASKS:
            raise HTTPException(status_code=404, detail=f"Unknown task {request.task!r}")
        if request.dataset == "generated" and not runner.item_counts(request.task)["generated"]:
            detail = f"Task {request.task!r} has no generated items"
            raise HTTPException(status_code=400, detail=detail)
        actor = Metered(_client(request.model))
        guardian = Metered(_client(request.guardian_model)) if request.guardian_model else None
        events = runner.run_task(
            request.task,
            actor,
            guardian,
            limit=request.limit,
            dataset=request.dataset,
            max_revisions=request.max_revisions,
        )
        recorded = record_run(
            events, store=RunStore(), config=request.model_dump(), actor=actor, guardian=guardian
        )
        return _stream(recorded)

    @app.get("/api/runs")
    def runs_list() -> list[dict[str, Any]]:
        return RunStore().list()

    @app.get("/api/runs/compare")
    def runs_compare(a: str, b: str) -> dict[str, Any]:
        try:
            return compare(_load_run(a), _load_run(b))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def runs_get(run_id: str) -> dict[str, Any]:
        return _load_run(run_id)

    @app.delete("/api/runs/{run_id}", status_code=204)
    def runs_delete(run_id: str) -> Response:
        try:
            RunStore().delete(run_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail=f"No saved run {run_id!r}") from exc
        return Response(status_code=204)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Epistemic Virtue Eval web console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
