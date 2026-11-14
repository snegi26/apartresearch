"""Run an evaluation from the command line and save it as a run record.

    eve-run --task calibration --dataset generated --model <provider/model-id>
    eve-run --task manipulation --model <provider/model-id> \
        --guardian <provider/other-model-id> --repeat 3

Records go to ``runs/`` (or ``$EVE_RUNS_DIR``) and appear in the console's
Runs tab.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from epistemic_virtue import runner
from epistemic_virtue.guardian import MAX_REVISIONS_LIMIT
from epistemic_virtue.models import Metered, get_client
from epistemic_virtue.store import RunStore, record_run


async def run_once(config: dict[str, Any], store: RunStore) -> str:
    """Run and save one evaluation; return its run id."""
    actor = Metered(get_client(config["model"]))
    guardian = Metered(get_client(config["guardian_model"])) if config["guardian_model"] else None
    events = runner.run_task(
        config["task"],
        actor,
        guardian,
        limit=config["limit"],
        dataset=config["dataset"],
        max_revisions=config["max_revisions"],
    )
    run_id = ""
    async for event in record_run(
        events, store=store, config=config, actor=actor, guardian=guardian
    ):
        if event["type"] == "start":
            total = event["total"]
        elif event["type"] == "item":
            print(f"\r  scored {event['id']} ({total} items)", end="", file=sys.stderr)
        elif event["type"] == "summary":
            print(file=sys.stderr)
            for name, stats in event["metrics"].items():
                mean = "n/a" if stats["mean"] is None else f"{stats['mean']:.4f}"
                print(f"  {name}: {mean} (scored {stats['n_scored']}/{event['n']})")
        elif event["type"] == "saved":
            run_id = event["run_id"]
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", choices=list(runner.TASKS), required=True)
    parser.add_argument("--model", required=True, help="provider/model-id, or demo/actor")
    parser.add_argument("--dataset", choices=["seed", "generated", "all"], default="seed")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--guardian", help="Guardian model id; enables the Guardian gate")
    parser.add_argument(
        "--max-revisions", type=int, default=2, choices=range(MAX_REVISIONS_LIMIT + 1)
    )
    parser.add_argument("--repeat", type=int, default=1, help="number of identical runs to save")
    args = parser.parse_args()

    config = {
        "task": args.task,
        "dataset": args.dataset,
        "limit": args.limit,
        "model": args.model,
        "guardian_model": args.guardian,
        "max_revisions": args.max_revisions,
    }
    store = RunStore()
    for index in range(args.repeat):
        print(f"Run {index + 1} of {args.repeat}: {args.task} with {args.model}")
        try:
            run_id = asyncio.run(run_once(config, store))
        except Exception as exc:  # noqa: BLE001 - the failed run is already saved
            sys.exit(f"Run failed and was saved as failed: {type(exc).__name__}: {exc}")
        print(f"  saved {store.root / (run_id + '.json')}")


if __name__ == "__main__":
    main()
