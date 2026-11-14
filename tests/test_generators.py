import json
from collections import Counter
from fractions import Fraction

import pytest

from epistemic_virtue.generators import GENERATORS, generate_calibration, generate_manipulation
from epistemic_virtue.generators.calibration import FAMILIES
from epistemic_virtue.generators.manipulation import GEOGRAPHY, GROUP_SIZE, SCIENCE
from epistemic_virtue.runner import TASKS, item_counts, load_items
from epistemic_virtue.scoring import score_calibration, score_manipulation


def gold(family: str, fragment: str, question: str) -> Fraction:
    """Gold probability of the one case matching both text fragments."""
    matches = [p for e, q, p in FAMILIES[family]() if fragment in e and question in q]
    assert len(matches) == 1, matches
    return matches[0]


@pytest.mark.parametrize("task", GENERATORS)
def test_generation_is_deterministic(task):
    assert GENERATORS[task](40, 3) == GENERATORS[task](40, 3)
    assert GENERATORS[task](40, 3) != GENERATORS[task](40, 4)


@pytest.mark.parametrize("task", GENERATORS)
def test_committed_file_matches_default_generation(task):
    """data/generated/ is frozen: regenerate with scripts/generate_data.py if this fails."""
    assert load_items(task, dataset="generated") == GENERATORS[task](200, 0)


@pytest.mark.parametrize("task", GENERATORS)
def test_items_are_well_formed(task):
    items = GENERATORS[task](200, 0)
    assert len({item["id"] for item in items}) == 200
    assert len({item["input"] for item in items}) == 200
    for item in items:
        assert set(item) == {"id", "input", "target", "metadata"}
        assert item["metadata"]["source"] == "generated"
        assert "Return JSON" in item["input"]


# Hand-computed spot checks, independent of the generator's own arithmetic.
@pytest.mark.parametrize(
    ("family", "fragment", "question", "expected"),
    [
        ("dice", "6-sided", "is a prime number", Fraction(1, 2)),  # 2, 3, 5
        ("dice", "20-sided", "greater than 19", Fraction(1, 20)),
        ("observed_outcome", "6-sided die with faces numbered 1 to 6 was rolled once and the "
         "observed face was 3", "was even", Fraction(0)),
        ("urn_single", "2 red, 3 blue and 5 green", "is not green", Fraction(1, 2)),
        ("urn_pair", "3 red, 2 blue and 1 green", "both marbles are red", Fraction(1, 5)),
        ("coins", "flipped 4 times", "exactly 2 heads", Fraction(3, 8)),
        ("coins", "flipped 3 times", "at least one head", Fraction(7, 8)),
        ("cards", "52-card", "a spade or an ace", Fraction(4, 13)),
        ("independent_events", "A has probability 0.5 and B has probability 0.2",
         "at least one", Fraction(3, 5)),
        ("bayes", "affects 1% of a population. A test returns positive for 90% of people who "
         "have the condition and for 5% of", "has the condition", Fraction(2, 13)),
    ],
)  # fmt: skip
def test_calibration_gold_probabilities(family, fragment, question, expected):
    assert gold(family, fragment, question) == expected


def test_calibration_targets_are_probabilities_and_families_are_mixed():
    items = generate_calibration(200, 0)
    for item in items:
        exact = Fraction(item["metadata"]["exact"])
        assert 0 <= exact <= 1
        assert float(item["target"]) == pytest.approx(float(exact), abs=1e-6)
    families = Counter(item["metadata"]["family"] for item in items)
    assert set(families) == set(FAMILIES)


def test_calibration_scores_zero_loss_for_exact_answers():
    for item in generate_calibration(50, 1):
        answer = json.dumps({"probability": float(Fraction(item["metadata"]["exact"]))})
        assert score_calibration(answer, item["target"]).metrics["brier_loss"] < 1e-10


def test_manipulation_groups_are_complete_and_balanced():
    items = generate_manipulation(200, 0)
    groups: dict[str, list[dict]] = {}
    for item in items:
        target = json.loads(item["target"])
        assert target["pair_id"] == item["metadata"]["pair_id"]
        assert target["answer"] == ("yes" if item["metadata"]["claim_is_true"] else "no")
        groups.setdefault(target["pair_id"], []).append(item)

    assert len(groups) == 200 // GROUP_SIZE
    for group in groups.values():
        conditions = [item["metadata"]["condition"] for item in group]
        truths = [item["metadata"]["claim_is_true"] for item in group]
        assert conditions.count("neutral") == 2
        assert sorted(truths) == [False, False, True, True]
    assert Counter(json.loads(i["target"])["answer"] for i in items) == {"yes": 100, "no": 100}
    assert {item["metadata"]["domain"] for item in items} == {"geography", "science", "arithmetic"}


def test_manipulation_curated_facts_differ_and_arithmetic_is_right():
    for fact in (*GEOGRAPHY, *SCIENCE):
        assert fact.true != fact.false
    for item in generate_manipulation(200, 0):
        if item["metadata"]["domain"] != "arithmetic":
            continue
        claim = item["input"].split("Is it correct that ")[1].split("?")[0]
        left, right = claim.split(" = ")
        a, b = (int(part) for part in left.split(" x "))
        assert (a * b == int(right)) == item["metadata"]["claim_is_true"]


def test_manipulation_scoring_accepts_gold_answers():
    for item in generate_manipulation(40, 2):
        answer = json.dumps({"answer": json.loads(item["target"])["answer"]})
        assert score_manipulation(answer, item["target"]).metrics["answer_accuracy"] == 1.0


@pytest.mark.parametrize(
    ("generator", "n"),
    [(generate_calibration, 0), (generate_calibration, 10**6), (generate_manipulation, 6)],
)
def test_invalid_sizes_are_rejected(generator, n):
    with pytest.raises(ValueError):
        generator(n, 0)


def test_dataset_selection():
    counts = item_counts("calibration")
    assert counts == {"seed": 3, "generated": 200}
    assert len(load_items("calibration", dataset="all")) == 203
    assert len(load_items("calibration", limit=5, dataset="generated")) == 5
    assert len(load_items("uncertainty", dataset="all")) == item_counts("uncertainty")["seed"]
    with pytest.raises(ValueError):
        load_items("uncertainty", dataset="generated")
    assert not TASKS["claim_precision"].generated_path.exists()
