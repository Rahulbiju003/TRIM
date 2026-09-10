"""CLI entry point for the shunt worker.

Usage:
  python -m worker bulk-read --file PATH [--question TEXT]
  python -m worker serve
"""
from __future__ import annotations

import argparse
import json
import sys


def cmd_bulk_read(args: argparse.Namespace) -> None:
    from worker.modes.bulk_reader import BulkReaderMode

    reader = BulkReaderMode()
    try:
        result = reader.run(file_path=args.file, question=args.question or None)
    except Exception as exc:
        # Fail-open: print error to stderr, exit 1 so the hook can bail out
        print(f"shunt-worker error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(
            json.dumps(
                {
                    "summary": result.summary,
                    "file_path": result.file_path,
                    "line_count": result.line_count,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "latency_ms": round(result.latency_ms, 1),
                    "model": result.model,
                }
            )
        )
    else:
        print(result.summary)


def cmd_serve(_args: argparse.Namespace) -> None:
    from worker.server import serve

    serve()


def main() -> None:
    parser = argparse.ArgumentParser(prog="worker", description="Shunt worker CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # bulk-read subcommand
    p_read = sub.add_parser("bulk-read", help="Summarise a large file")
    p_read.add_argument("--file", required=True, help="Absolute path to the file")
    p_read.add_argument("--question", default="", help="Optional question about the file")
    p_read.add_argument(
        "--json", action="store_true", help="Output full JSON instead of plain summary"
    )
    p_read.set_defaults(func=cmd_bulk_read)

    # serve subcommand
    p_serve = sub.add_parser("serve", help="Start the HTTP worker server")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
