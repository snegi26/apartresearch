"""Inspect scorers: thin adapters over the pure functions in ``scoring.py``."""

from __future__ import annotations

from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, mean, scorer
from inspect_ai.solver import TaskState

from epistemic_virtue.scoring import (
    ItemScore,
    Scorer,
    parse_json,
    score_calibration,
    score_claim_precision,
    score_manipulation,
    score_uncertainty,
)

__all__ = [
    "brier_score",
    "claim_precision_score",
    "manipulation_score",
    "parse_json",
    "uncertainty_score",
]


def _to_scores(result: ItemScore, accuracy_metrics: frozenset[str]) -> dict[str, Score]:
    scores: dict[str, Score] = {}
    for name, value in result.metrics.items():
        if value is None:
            scores[name] = Score.unscored(reason=result.error, explanation=result.explanation)
        elif name in accuracy_metrics:
            scores[name] = Score(
                value=CORRECT if value == 1.0 else INCORRECT,
                answer=result.answer,
                explanation=result.explanation,
            )
        else:
            scores[name] = Score(value=value, answer=result.answer, explanation=result.explanation)
    return scores


def _adapt(fn: Scorer, accuracy_metrics: frozenset[str] = frozenset()):
    async def score(state: TaskState, target: Target) -> Score | dict[str, Score]:
        scores = _to_scores(fn(state.output.completion, target.text), accuracy_metrics)
        return next(iter(scores.values())) if len(scores) == 1 else scores

    return score


@scorer(metrics={"brier_loss": [mean()]})
def brier_score():
    return _adapt(score_calibration)


@scorer(metrics={"status_accuracy": [accuracy()], "confidence_mean": [mean()]})
def uncertainty_score():
    return _adapt(score_uncertainty, frozenset({"status_accuracy"}))


@scorer(metrics={"answer_accuracy": [accuracy()]})
def manipulation_score():
    return _adapt(score_manipulation, frozenset({"answer_accuracy"}))


@scorer(metrics={"claim_f1": [mean()], "claim_label_accuracy": [mean()]})
def claim_precision_score():
    return _adapt(score_claim_precision)
