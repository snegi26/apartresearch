import asyncio
import json

import pytest

from epistemic_virtue.guardian import (
    ACTOR_SYSTEM_PROMPT,
    build_actor_prompt,
    guarded_generate,
    review,
)
from epistemic_virtue.models import get_client
from epistemic_virtue.runner import DATA

PRESETS = json.loads((DATA / "guardian_presets.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_demo_guardian_matches_preset_expectation(preset):
    guardian = get_client("demo/guardian")
    result = asyncio.run(review(guardian, preset["intent"], preset["context"], preset["draft"]))
    assert result.verdict.status == preset["expects"]


def test_demo_guardian_allows_warning_against_unsafe_command():
    guardian = get_client("demo/guardian")
    draft = "Request access through the queue. Do not disable authentication on the endpoint."
    result = asyncio.run(review(guardian, "How do I fix permissions?", "Policy OPS-7.", draft))
    assert result.verdict.passed


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_demo_loop_ends_approved(preset):
    async def run():
        events = guarded_generate(
            get_client("demo/actor"),
            get_client("demo/guardian"),
            actor_system=ACTOR_SYSTEM_PROMPT,
            actor_prompt=build_actor_prompt(preset["intent"], preset["context"]),
            intent=preset["intent"],
            context=preset["context"],
        )
        return [event async for event in events]

    events = asyncio.run(run())
    assert events[1]["verdict"]["status"] == "FAIL"  # the scripted first draft is careless
    assert events[-1]["outcome"] == "approved"
    assert events[-1]["rounds"] == 2


def test_unknown_demo_model_is_rejected():
    with pytest.raises(ValueError):
        get_client("demo/other")
    with pytest.raises(ValueError):
        get_client("bad id; rm -rf")
