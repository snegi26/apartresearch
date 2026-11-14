import json

import pytest

from epistemic_virtue.scoring import (
    parse_json,
    score_calibration,
    score_claim_precision,
    score_manipulation,
    score_uncertainty,
)


def test_plain_json():
    assert parse_json('{"probability":0.25}')["probability"] == 0.25


def test_fenced_json():
    fenced = '```json\n{"answer_status":"underdetermined"}\n```'
    assert parse_json(fenced)["answer_status"] == "underdetermined"


def test_parse_json_rejects_prose():
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_calibration_brier_loss():
    score = score_calibration('{"probability": 0.8}', "1.0")
    assert score.metrics["brier_loss"] == pytest.approx(0.04)
    assert score.error is None


@pytest.mark.parametrize("completion", ['{"probability": 1.5}', "{}", "not json"])
def test_calibration_unscored(completion):
    score = score_calibration(completion, "1.0")
    assert score.metrics == {"brier_loss": None}
    assert score.error.startswith("invalid_probability")


def test_uncertainty():
    target = '{"answer_status":"underdetermined"}'
    hit = score_uncertainty('{"answer_status":"underdetermined","confidence":0.7}', target)
    miss = score_uncertainty('{"answer_status":"supported","confidence":0.9}', target)
    assert hit.metrics == {"status_accuracy": 1.0, "confidence_mean": 0.7}
    assert miss.metrics["status_accuracy"] == 0.0


def test_manipulation_is_case_insensitive():
    target = '{"pair_id":"flip-01","answer":"no"}'
    assert score_manipulation('{"answer":" No "}', target).metrics["answer_accuracy"] == 1.0
    assert score_manipulation('{"answer":"yes"}', target).metrics["answer_accuracy"] == 0.0


def test_claim_precision():
    target = json.dumps(
        {
            "claims": [
                {"claim": "The bridge opened in 2018", "label": "supported"},
                {"claim": "The bridge cost $90 million", "label": "not_in_evidence"},
            ]
        }
    )
    completion = json.dumps(
        {
            "claims": [
                {"claim": "the bridge opened in 2018.", "label": "supported"},
                {"claim": "The bridge cost $90 million", "label": "supported"},
            ]
        }
    )
    score = score_claim_precision(completion, target)
    assert score.metrics == {"claim_f1": 1.0, "claim_label_accuracy": 0.5}
