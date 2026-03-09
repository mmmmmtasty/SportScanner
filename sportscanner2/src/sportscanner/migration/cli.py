from __future__ import annotations

import argparse
from pathlib import Path

from sportscanner.migration.convert_sidecars import convert_library_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sportscanner.migration.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert", help="Convert legacy SportScanner sidecars")
    convert.add_argument("--library-root", required=True, type=Path)
    convert.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "convert":
        results = convert_library_root(args.library_root, dry_run=args.dry_run)
        for result in results:
            print(f"{result.source} -> {result.target}")
        print(f"converted={len(results)} dry_run={'yes' if args.dry_run else 'no'}")
        return 0
    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

