from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "backend"))

from app.data.market_cache_inspector import inspect_entries, inspect_jobs, inspect_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the local market-data cache without reading cached payloads.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser("summary", help="Print cache table, endpoint, job, and conflict counts.")
    _add_cache_path(summary_parser)

    entries_parser = subparsers.add_parser("entries", help="List current cache entries without payloads.")
    _add_cache_path(entries_parser)
    entries_parser.add_argument("--endpoint", default=None)
    entries_parser.add_argument("--instrument-id", default=None)
    entries_parser.add_argument("--limit", type=int, default=50)

    jobs_parser = subparsers.add_parser("jobs", help="List cache write jobs without payloads.")
    _add_cache_path(jobs_parser)
    jobs_parser.add_argument("--status", default=None)
    jobs_parser.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()
    if args.command == "summary":
        payload = inspect_summary(args.cache_path)
    elif args.command == "entries":
        payload = inspect_entries(args.cache_path, endpoint=args.endpoint, instrument_id=args.instrument_id, limit=args.limit)
    else:
        payload = inspect_jobs(args.cache_path, status=args.status, limit=args.limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _add_cache_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=Path("data/market-cache/market_data.sqlite3"),
        help="Path to the SQLite market-data cache.",
    )

if __name__ == "__main__":
    raise SystemExit(main())
