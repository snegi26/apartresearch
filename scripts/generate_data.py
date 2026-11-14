"""Generate benchmark items for the dimensions with computable gold answers.

    python scripts/generate_data.py                      # both tasks, 200 items each
    python scripts/generate_data.py --task calibration --n 300 --seed 7

Output goes to ``data/generated/<task>.jsonl``. The same seed and size always
produce the same file; the committed files use the defaults.
"""

import argparse
import hashlib
import json
from pathlib import Path

from epistemic_virtue.generators import GENERATORS
from epistemic_virtue.runner import GENERATED


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", choices=[*GENERATORS, "all"], default="all")
    parser.add_argument("--n", type=int, default=200, help="items per task (default 200)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=GENERATED, help="output directory")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for task in GENERATORS if args.task == "all" else [args.task]:
        try:
            items = GENERATORS[task](args.n, args.seed)
        except ValueError as exc:
            parser.error(f"{task}: {exc}")
        text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
        path = args.out / f"{task}.jsonl"
        path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        print(f"{path}: {len(items)} items, seed={args.seed}, sha256={digest}")


if __name__ == "__main__":
    main()
