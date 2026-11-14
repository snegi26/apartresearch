"""Deterministic item generators for dimensions whose gold answers can be computed.

Each generator returns items in the same shape as the seed JSONL files
(``id``, ``input``, ``target``, ``metadata``). The same ``seed`` and ``n``
always produce the same items, so a generated set can be frozen and hashed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from epistemic_virtue.generators.calibration import generate_calibration
from epistemic_virtue.generators.manipulation import generate_manipulation

Generator = Callable[[int, int], list[dict[str, Any]]]

GENERATORS: dict[str, Generator] = {
    "calibration": generate_calibration,
    "manipulation": generate_manipulation,
}

__all__ = ["GENERATORS", "generate_calibration", "generate_manipulation"]
