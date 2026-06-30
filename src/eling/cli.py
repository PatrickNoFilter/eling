"""Eling CLI — eling remember / recall / reason / stats / mcp."""

import argparse
import json
import sys

from .brain import Brain


def main():
    parser = argparse.ArgumentParser(prog="eling", description="Eling — unified second brain")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rem = sub.add_parser("remember", help="Store content")
    p_rem.add_argument("content")
    p_rem.add_argument("--layer", default="auto", choices=["auto", "facts", "kb", "notion"])
    p_rem.add_argument("--category", default="general")
    p_rem.add_argument("--tags", default="")
    p_rem.add_argument("--title", default="")

    p_rec = sub.add_parser("recall", help="Search across all layers")
    p_rec.add_argument("query")
    p_rec.add_argument("--limit", type=int, default=10)
    p_rec.add_argument("--layers", default="", help="comma-separated subset")

    p_reason = sub.add_parser("reason", help="Compositional entity query")
    p_reason.add_argument("entities", nargs="+")
    p_reason.add_argument("--limit", type=int, default=10)

    p_reflect = sub.add_parser("reflect", help="Promote fact_id to Notion")
    p_reflect.add_argument("fact_id", type=int)

    sub.add_parser("stats", help="Show brain stats")

    p_mcp = sub.add_parser("mcp", help="Run MCP server (stdio)")
    p_mcp.add_argument("--transport", default="stdio")

    args = parser.parse_args()

    if args.cmd == "mcp":
        from .mcp_server import run_stdio
        run_stdio()
        return

    brain = Brain()
    try:
        if args.cmd == "remember":
            out = brain.remember(args.content, layer=args.layer, category=args.category,
                                  tags=args.tags, title=args.title)
        elif args.cmd == "recall":
            layers = [s.strip() for s in args.layers.split(",") if s.strip()] or None
            out = brain.recall(args.query, layers=layers, limit=args.limit)
        elif args.cmd == "reason":
            out = brain.reason(args.entities, limit=args.limit)
        elif args.cmd == "reflect":
            out = brain.reflect(args.fact_id)
        elif args.cmd == "stats":
            out = brain.stats()
        print(json.dumps(out, indent=2, default=str))
    finally:
        brain.close()


if __name__ == "__main__":
    main()
