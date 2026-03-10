#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sportscanner.testing.workflow_harness import ScenarioFailure, ScenarioHarness


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deterministic Sport_Test end-to-end workflow validation.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the temporary workspace after the run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    harness = ScenarioHarness(keep_temp=args.keep_temp)
    try:
        harness.run()
    except ScenarioFailure as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        if args.keep_temp:
            print(f"workspace {harness.base_dir}", file=sys.stderr)
        return 1
    finally:
        harness.close()
    print("PASS all scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
