"""The four Inspect tasks.

Each task accepts an optional Guardian gate, e.g.::

    inspect eval epistemic_virtue/tasks.py@uncertainty --model <provider/model> \
        -T guardian=<provider/other-model> -T max_revisions=2
"""

from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver, generate, system_message

from epistemic_virtue.runner import JSON_ONLY, TASKS
from epistemic_virtue.scorers import (
    brier_score,
    claim_precision_score,
    manipulation_score,
    uncertainty_score,
)
from epistemic_virtue.solvers import guardian_gate


def _solver(guardian: str | None, max_revisions: int) -> list[Solver]:
    if guardian:
        return [guardian_gate(guardian, JSON_ONLY, max_revisions)]
    return [system_message(JSON_ONLY), generate()]


def _task(name: str, scorer: Scorer, guardian: str | None, max_revisions: int) -> Task:
    return Task(
        dataset=json_dataset(str(TASKS[name].path)),
        solver=_solver(guardian, max_revisions),
        scorer=scorer,
    )


@task
def calibration(guardian: str | None = None, max_revisions: int = 2) -> Task:
    return _task("calibration", brier_score(), guardian, max_revisions)


@task
def uncertainty(guardian: str | None = None, max_revisions: int = 2) -> Task:
    return _task("uncertainty", uncertainty_score(), guardian, max_revisions)


@task
def manipulation(guardian: str | None = None, max_revisions: int = 2) -> Task:
    return _task("manipulation", manipulation_score(), guardian, max_revisions)


@task
def claim_precision(guardian: str | None = None, max_revisions: int = 2) -> Task:
    return _task("claim_precision", claim_precision_score(), guardian, max_revisions)
