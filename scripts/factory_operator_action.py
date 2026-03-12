#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

try:
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env", override=True)
except ImportError:
    pass

import config
from factory.registry import FactoryRegistry


def _registry() -> FactoryRegistry:
    root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
    if not root.is_absolute():
        root = project_root / root
    return FactoryRegistry(root)


def _row(action) -> dict:
    return {
        "action_id": action.action_id,
        "action_key": action.action_key,
        "family_id": action.family_id,
        "lineage_id": action.lineage_id,
        "signal_type": action.signal_type,
        "requested_action": action.requested_action,
        "summary": action.summary,
        "status": action.status,
        "decision": action.decision,
        "instruction": action.instruction,
        "note": action.note,
        "resolved_by": action.resolved_by,
        "resolved_at": action.resolved_at,
        "updated_at": action.updated_at,
        "context": dict(action.context or {}),
    }


def _cmd_list(args: argparse.Namespace) -> int:
    actions = _registry().operator_actions(status=args.status, lineage_id=args.lineage_id)
    print(json.dumps([_row(action) for action in actions], indent=2))
    return 0


def _resolve(args: argparse.Namespace, decision: str) -> int:
    action = _registry().resolve_operator_action(
        args.action_id,
        decision=decision,
        resolved_by=args.by,
        note=args.note,
        instruction=getattr(args, "instruction", None),
    )
    if action is None:
        print(json.dumps({"ok": False, "error": "operator_action_not_found", "action_id": args.action_id}, indent=2))
        return 1
    print(json.dumps({"ok": True, "action": _row(action)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate factory human-action inbox items.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List operator action inbox items.")
    list_parser.add_argument("--status", default="pending")
    list_parser.add_argument("--lineage-id")
    list_parser.set_defaults(func=_cmd_list)

    approve_parser = subparsers.add_parser("approve", help="Approve a pending operator action.")
    approve_parser.add_argument("action_id")
    approve_parser.add_argument("--by", required=True)
    approve_parser.add_argument("--note")
    approve_parser.set_defaults(func=lambda args: _resolve(args, "approve"))

    reject_parser = subparsers.add_parser("reject", help="Reject a pending operator action.")
    reject_parser.add_argument("action_id")
    reject_parser.add_argument("--by", required=True)
    reject_parser.add_argument("--note")
    reject_parser.set_defaults(func=lambda args: _resolve(args, "reject"))

    instruct_parser = subparsers.add_parser("instruct", help="Send an instruction back to the factory for a pending operator action.")
    instruct_parser.add_argument("action_id")
    instruct_parser.add_argument("--by", required=True)
    instruct_parser.add_argument("--instruction", required=True)
    instruct_parser.add_argument("--note")
    instruct_parser.set_defaults(func=lambda args: _resolve(args, "instruct"))

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
