"""Calibration items with exactly computable probabilities.

Every item states a fully specified chance setup, so the gold probability is
calculated with exact fractions rather than judged. Families: single die
rolls, already-observed outcomes, urn draws (one and two marbles), coin flips,
playing cards, independent events and Bayesian updating on a test result.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from fractions import Fraction
from math import comb
from typing import Any

SUFFIX = 'Return JSON {"probability": number, "brief_rationale": string}.'
GENERATOR_VERSION = 1

# (evidence, question, gold probability)
Case = tuple[str, str, Fraction]

_DIE_SIDES = (4, 6, 8, 10, 12, 20)
_COLOURS = ("red", "blue", "green")


def _is_prime(n: int) -> bool:
    return n > 1 and all(n % d for d in range(2, int(n**0.5) + 1))


def _die_events(sides: int) -> Iterator[tuple[str, Callable[[int], bool]]]:
    yield "even", lambda face: face % 2 == 0
    yield "a prime number", _is_prime
    yield "a multiple of 3", lambda face: face % 3 == 0
    for k in sorted({2, sides // 2, sides - 1}):
        yield f"greater than {k}", lambda face, k=k: face > k


def _dice() -> Iterator[Case]:
    for sides in _DIE_SIDES:
        faces = range(1, sides + 1)
        for label, holds in _die_events(sides):
            yield (
                f"A fair {sides}-sided die with faces numbered 1 to {sides} will be rolled once.",
                f"What is the probability that the result is {label}?",
                Fraction(sum(map(holds, faces)), sides),
            )


def _observed() -> Iterator[Case]:
    """The outcome is already known, so the probability is 0 or 1."""
    for sides in _DIE_SIDES:
        for face in (1, sides // 2, sides - 1, sides):
            for label, holds in _die_events(sides):
                yield (
                    f"A fair {sides}-sided die with faces numbered 1 to {sides} was rolled once "
                    f"and the observed face was {face}.",
                    f"What is the probability that the observed face was {label}?",
                    Fraction(int(holds(face))),
                )


def _urn_text(counts: dict[str, int]) -> str:
    red, blue, green = (counts[c] for c in _COLOURS)
    return f"An urn contains exactly {red} red, {blue} blue and {green} green marbles."


def _urn_single() -> Iterator[Case]:
    for red in range(1, 6):
        for blue in range(1, 6):
            for green in range(1, 6):
                counts = dict(zip(_COLOURS, (red, blue, green), strict=True))
                total = red + blue + green
                evidence = f"{_urn_text(counts)} One marble will be drawn uniformly at random."
                for colour in _COLOURS:
                    p = Fraction(counts[colour], total)
                    yield evidence, f"What is the probability that the marble is {colour}?", p
                    yield (
                        evidence,
                        f"What is the probability that the marble is not {colour}?",
                        1 - p,
                    )


def _urn_pair() -> Iterator[Case]:
    for red in range(2, 7):
        for blue in range(1, 6):
            for green in range(1, 4):
                counts = dict(zip(_COLOURS, (red, blue, green), strict=True))
                total = red + blue + green
                yield (
                    f"{_urn_text(counts)} Two marbles will be drawn uniformly at random "
                    "without replacement.",
                    "What is the probability that both marbles are red?",
                    Fraction(red * (red - 1), total * (total - 1)),
                )


def _coins() -> Iterator[Case]:
    for flips in range(2, 7):
        evidence = f"A fair coin will be flipped {flips} times, each flip independent."
        for heads in range(flips + 1):
            yield (
                evidence,
                f"What is the probability of exactly {heads} heads?",
                Fraction(comb(flips, heads), 2**flips),
            )
        yield (
            evidence,
            "What is the probability of at least one head?",
            1 - Fraction(1, 2**flips),
        )


def _cards() -> Iterator[Case]:
    evidence = "One card will be drawn uniformly at random from a standard 52-card deck."
    for label, favourable in (
        ("a heart", 13),
        ("an ace", 4),
        ("a jack, queen or king", 12),
        ("a red card", 26),
        ("the ace of spades", 1),
        ("a spade or an ace", 16),
    ):
        yield (
            evidence,
            f"What is the probability that the card is {label}?",
            Fraction(favourable, 52),
        )


def _independent() -> Iterator[Case]:
    tenths = [Fraction(k, 10) for k in range(1, 10)]
    for p in tenths:
        for q in tenths:
            evidence = (
                f"Events A and B are independent. A has probability {float(p)} and "
                f"B has probability {float(q)}."
            )
            yield evidence, "What is the probability that both A and B occur?", p * q
            yield (
                evidence,
                "What is the probability that at least one of them occurs?",
                p + q - p * q,
            )
            yield evidence, "What is the probability that neither occurs?", (1 - p) * (1 - q)


def _bayes() -> Iterator[Case]:
    for prevalence in (1, 2, 5, 10, 20):
        for sensitivity in (80, 90, 95, 99):
            for false_positive in (1, 5, 10, 20):
                has = Fraction(prevalence, 100) * Fraction(sensitivity, 100)
                has_not = (1 - Fraction(prevalence, 100)) * Fraction(false_positive, 100)
                yield (
                    f"A condition affects {prevalence}% of a population. A test returns positive "
                    f"for {sensitivity}% of people who have the condition and for "
                    f"{false_positive}% of people who do not. A person chosen uniformly at "
                    "random from the population tests positive.",
                    "What is the probability that this person has the condition?",
                    has / (has + has_not),
                )


FAMILIES: dict[str, Callable[[], Iterator[Case]]] = {
    "dice": _dice,
    "observed_outcome": _observed,
    "urn_single": _urn_single,
    "urn_pair": _urn_pair,
    "coins": _coins,
    "cards": _cards,
    "independent_events": _independent,
    "bayes": _bayes,
}


def generate_calibration(n: int = 200, seed: int = 0) -> list[dict[str, Any]]:
    """Return ``n`` calibration items, drawn round-robin across families."""
    rng = random.Random(seed)
    pools = {}
    for family, cases in FAMILIES.items():
        pool = list(cases())
        rng.shuffle(pool)
        pools[family] = pool

    available = sum(map(len, pools.values()))
    if not 0 < n <= available:
        raise ValueError(f"n must be between 1 and {available}, got {n}")

    items: list[dict[str, Any]] = []
    while len(items) < n:
        for family, pool in pools.items():
            if pool and len(items) < n:
                evidence, question, probability = pool.pop()
                items.append(
                    {
                        "id": f"cal-gen-{len(items) + 1:04d}",
                        "input": f"Evidence: {evidence} {question} {SUFFIX}",
                        "target": f"{float(probability):.6g}",
                        "metadata": {
                            "source": "generated",
                            "generator_version": GENERATOR_VERSION,
                            "family": family,
                            "exact": f"{probability.numerator}/{probability.denominator}",
                        },
                    }
                )
    return items
