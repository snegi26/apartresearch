"""Inspect adapters, exercised without any provider or network access."""

import asyncio

from inspect_ai.model import ChatMessageUser, ModelName, ModelOutput
from inspect_ai.scorer import CORRECT, INCORRECT, Target
from inspect_ai.solver import TaskState

from epistemic_virtue import solvers, tasks
from epistemic_virtue.models import get_client
from epistemic_virtue.runner import JSON_ONLY, load_items
from epistemic_virtue.scorers import brier_score, uncertainty_score


def make_state(prompt: str, completion: str = "") -> TaskState:
    state = TaskState(
        model=ModelName("mockllm/model"),
        sample_id="s1",
        epoch=1,
        input=prompt,
        messages=[ChatMessageUser(content=prompt)],
    )
    state.output = ModelOutput.from_content("mockllm/model", completion)
    return state


def test_single_metric_scorer_returns_score():
    state = make_state("q", '{"probability": 0.5}')
    score = asyncio.run(brier_score()(state, Target("1.0")))
    assert score.value == 0.25


def test_multi_metric_scorer_maps_accuracy_to_correct_incorrect():
    target = Target('{"answer_status":"supported"}')
    hit = make_state("q", '{"answer_status":"supported","confidence":0.9}')
    miss = make_state("q", '{"answer_status":"unsupported","confidence":0.9}')
    scorer = uncertainty_score()
    assert asyncio.run(scorer(hit, target))["status_accuracy"].value == CORRECT
    assert asyncio.run(scorer(miss, target))["status_accuracy"].value == INCORRECT


def test_tasks_build_with_and_without_guardian():
    assert len(tasks.uncertainty().dataset) == 3
    assert len(tasks.uncertainty(guardian="demo/guardian").dataset) == 3


def test_guardian_gate_solver_revises_and_records_metadata(monkeypatch):
    monkeypatch.setattr(solvers, "InspectModelClient", lambda: get_client("demo/actor"))
    item = next(i for i in load_items("uncertainty") if i["id"] == "unc-002")
    solve = solvers.guardian_gate("demo/guardian", JSON_ONLY)

    state = asyncio.run(solve(make_state(item["input"]), None))

    assert "underdetermined" in state.output.completion
    assert state.messages[-1].role == "assistant"
    assert state.metadata["guardian"]["outcome"] == "approved"
    assert state.metadata["guardian"]["rounds"] == 2
