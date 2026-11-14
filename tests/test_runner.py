import asyncio

import pytest

from epistemic_virtue.models import get_client
from epistemic_virtue.runner import TASKS, load_items, run_task


def run(task, gated):
    async def go():
        guardian = get_client("demo/guardian") if gated else None
        return [event async for event in run_task(task, get_client("demo/actor"), guardian)]

    return asyncio.run(go())


@pytest.mark.parametrize("task", TASKS)
def test_every_task_runs_and_scores(task):
    events = run(task, gated=False)
    assert events[0]["total"] == len(load_items(task))
    summary = events[-1]
    assert summary["type"] == "summary"
    assert summary["unscored"] == 0
    assert summary["guardian"] is None


def test_gate_improves_uncertainty_on_demo_models():
    summary = run("uncertainty", gated=True)[-1]
    assert summary["baseline"]["metrics"]["status_accuracy"]["mean"] == pytest.approx(2 / 3)
    assert summary["metrics"]["status_accuracy"]["mean"] == 1.0
    assert summary["guardian"] == {"approved": 3, "rejected": 0, "errors": 0, "revised": 1}


def test_manipulation_pair_consistency():
    summary = run("manipulation", gated=False)[-1]
    # The scripted actor is sycophantic on man-001, breaking pair flip-01 only.
    assert summary["metrics"]["answer_accuracy"]["mean"] == 0.75
    assert summary["pair_consistency"] == {"rate": 0.5, "consistent": 1, "pairs": 2}


def test_limit():
    events = run("calibration", gated=False)
    assert len([e for e in events if e["type"] == "item"]) == 3
    limited = asyncio.run(_limited())
    assert limited == 1


async def _limited():
    events = [e async for e in run_task("calibration", get_client("demo/actor"), limit=1)]
    return len([e for e in events if e["type"] == "item"])
