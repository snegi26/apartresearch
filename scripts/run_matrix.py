"""Run every configured model against every configured task with ``inspect eval``."""

import argparse
import subprocess
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/models.yaml")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--guardian", help="Guardian model id; enables the Guardian gate")
    parser.add_argument("--dataset", choices=["seed", "generated", "all"], default="seed")
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    for model in config["models"]:
        for task in config["tasks"]:
            cmd = ["inspect", "eval", f"epistemic_virtue/tasks.py@{task}", "--model", model]
            if args.dataset != "seed":
                cmd += ["-T", f"dataset={args.dataset}"]
            if args.limit:
                cmd += ["--limit", str(args.limit)]
            if args.guardian:
                cmd += ["-T", f"guardian={args.guardian}"]
            print("+", " ".join(cmd), flush=True)
            subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
