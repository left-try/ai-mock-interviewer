"""Summarize one local interview test JSONL log; this script uses no model/network."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mock_interviewer.test_run_summary import summarize_events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="JSONL test-run log")
    args = parser.parse_args()
    try:
        with args.log.open("r", encoding="utf-8") as stream:
            summary = summarize_events(stream)
    except OSError as exc:
        parser.error(f"cannot read {args.log}: {exc}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
