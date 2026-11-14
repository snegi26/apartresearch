import asyncio
import json

import pytest

from epistemic_virtue import runner
from epistemic_virtue.cli import run_once
from epistemic_virtue.models import Metered, get_client
from epistemic_virtue.store import (
    RUN_ID_PATTERN,
    RunNotFound,
    RunStore,
    compare,
    items_hash,
    record_run,
)

CONFIG = {
    "task": "uncertainty",
    "dataset": "seed",
    "limit": None,
    "model": "demo/actor",
    "guardian_model": None,
    "max_revisions": 2,
}


def run(config=CONFIG, store=None):
    """Run and save one evaluation; return (store, run_id)."""
    store = store or RunStore()
    return store, asyncio.run(run_once(dict(config), store))


def test_record_holds_everything_needed_to_repeat_the_run():
    store, run_id = run()
    record = store.load(run_id)

    assert RUN_ID_PATTERN.match(run_id)
    assert record["schema_version"] == 1
    assert record["status"] == "completed"
    assert record["error"] is None
    assert record["config"] == CONFIG
    assert record["metric_directions"] == runner.TASKS["uncertainty"].metrics
    assert record["summary"]["n"] == len(record["items"]) == 3
    assert record["items"][0].keys() >= {"id", "input", "target", "response", "score"}
    assert record["usage"] == {
        "actor": {"calls": 3, "input_tokens": None, "output_tokens": None},
        "guardian": None,
    }

    provenance = record["provenance"]
    assert provenance["items_sha256"] == items_hash(runner.load_items("uncertainty"))
    assert provenance["guardian_prompt_sha256"] is None
    assert len(provenance["system_prompt_sha256"]) == 64
    assert provenance["python_version"]


def test_identical_runs_are_reproducible():
    store, first = run()
    _, second = run(store=store)
    a, b = store.load(first), store.load(second)
    assert first != second
    assert a["provenance"]["items_sha256"] == b["provenance"]["items_sha256"]
    assert a["summary"] == b["summary"]
    assert compare(a, b)["differing_items"] == []


def test_gated_run_records_guardian_prompt_and_usage():
    store, run_id = run({**CONFIG, "guardian_model": "demo/guardian"})
    record = store.load(run_id)
    assert len(record["provenance"]["guardian_prompt_sha256"]) == 64
    assert record["usage"]["actor"]["calls"] == 4
    assert record["usage"]["guardian"]["calls"] == 4
    assert record["summary"]["baseline"] is not None


def test_list_is_newest_first_and_omits_items():
    store, first = run()
    _, second = run({**CONFIG, "task": "calibration"}, store)
    listed = store.list()
    assert sorted(r["run_id"] for r in listed) == sorted([first, second])
    assert [r["run_id"] for r in listed] == [second, first]
    assert all("items" not in r for r in listed)


def test_empty_store_and_missing_runs():
    store = RunStore()
    assert store.list() == []
    for bad in ("nope", "../../etc/passwd", "20260101T000000Z-calibration-abcdef"):
        with pytest.raises(RunNotFound):
            store.load(bad)
        with pytest.raises(RunNotFound):
            store.delete(bad)


def test_failed_run_is_saved_with_the_items_that_finished():
    class FailsOnThirdCall:
        name = "flaky"

        def __init__(self):
            self.inner, self.calls = get_client("demo/actor"), 0

        async def complete(self, system, messages):
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("provider unavailable")
            return await self.inner.complete(system, messages)

    store = RunStore()
    actor = Metered(FailsOnThirdCall())

    async def go():
        events = runner.run_task("uncertainty", actor)
        recorded = record_run(events, store=store, config=CONFIG, actor=actor, guardian=None)
        return [event async for event in recorded]

    with pytest.raises(RuntimeError):
        asyncio.run(go())

    (record,) = store.list()
    assert record["status"] == "failed"
    assert record["error"] == "RuntimeError: provider unavailable"
    assert record["n_items"] == 2
    assert record["summary"]["n"] == 2


def test_interrupted_run_is_saved():
    store = RunStore()
    actor = Metered(get_client("demo/actor"))

    async def go():
        events = runner.run_task("uncertainty", actor)
        recorded = record_run(events, store=store, config=CONFIG, actor=actor, guardian=None)
        async for event in recorded:
            if event["type"] == "item":
                await recorded.aclose()  # what a dropped browser connection does
                break

    asyncio.run(go())
    (record,) = store.list()
    assert record["status"] == "interrupted"
    assert record["n_items"] == 1


def test_metered_sums_reported_tokens():
    class Reporting:
        name = "reporting"
        last_usage = (10, 4)

        async def complete(self, system, messages):
            return "{}"

    metered = Metered(Reporting())
    asyncio.run(metered.complete("s", []))
    asyncio.run(metered.complete("s", []))
    assert metered.usage.to_dict() == {"calls": 2, "input_tokens": 20, "output_tokens": 8}


def test_compare_reports_differences_and_rejects_other_tasks():
    store, plain = run()
    _, gated = run({**CONFIG, "guardian_model": "demo/guardian"}, store)
    _, other = run({**CONFIG, "task": "calibration"}, store)

    result = compare(store.load(plain), store.load(gated))
    accuracy = result["metrics"]["status_accuracy"]
    assert accuracy["a"] == pytest.approx(2 / 3)
    assert accuracy["b"] == 1.0
    assert accuracy["direction"] == "higher"
    assert result["shared_items"] == 3
    assert result["only_a"] == result["only_b"] == 0
    assert [d["id"] for d in result["differing_items"]] == ["unc-002"]

    with pytest.raises(ValueError):
        compare(store.load(plain), store.load(other))


def test_record_is_plain_json_on_disk(runs_dir):
    _, run_id = run()
    on_disk = json.loads((runs_dir / f"{run_id}.json").read_text(encoding="utf-8"))
    assert on_disk["run_id"] == run_id
    assert not list(runs_dir.glob("*.tmp"))
