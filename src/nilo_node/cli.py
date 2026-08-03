"""CLI for storage and chunk management."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from nilo_node.config.loader import load_config
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.models import ChunkQuery
from nilo_node.storage.paths import StoragePaths


def _load_storage_manager(config_path: str | None) -> StorageManager:
    path = config_path or os.environ.get("NILO_CONFIG_PATH", "/etc/nilo-node/nilo-node.yaml")
    config = load_config(path)
    storage_base = Path(config.storage.base_path)
    db = Database(storage_base / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    paths = StoragePaths(storage_base, config.storage.recordings_dir)
    return StorageManager(config, repo, paths)


def cmd_storage_usage(args: argparse.Namespace) -> int:
    manager = _load_storage_manager(args.config)
    print(json.dumps(manager.disk_usage(), indent=2))
    return 0


def cmd_chunks_list(args: argparse.Namespace) -> int:
    manager = _load_storage_manager(args.config)
    query = ChunkQuery(
        start_ts=datetime.fromisoformat(args.start) if args.start else None,
        end_ts=datetime.fromisoformat(args.end) if args.end else None,
        campaign_id=args.campaign_id,
        subject_user_id=args.subject_user_id,
        status=args.status,
    )
    chunks = manager.list_chunks(query)
    print(json.dumps([c.model_dump(mode="json") for c in chunks], indent=2))
    return 0


def cmd_chunks_delete(args: argparse.Namespace) -> int:
    manager = _load_storage_manager(args.config)
    result = manager.delete_chunks_in_range(
        datetime.fromisoformat(args.start),
        datetime.fromisoformat(args.end),
        dry_run=args.dry_run,
        campaign_id=args.campaign_id,
        subject_user_id=args.subject_user_id,
    )
    print(json.dumps(result.__dict__, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nilo-node")
    parser.add_argument("--config", help="Path to nilo-node.yaml")
    sub = parser.add_subparsers(dest="command")

    usage = sub.add_parser("storage-usage", help="Show disk and chunk usage")
    usage.set_defaults(func=cmd_storage_usage)

    lst = sub.add_parser("chunks-list", help="List chunks")
    lst.add_argument("--start")
    lst.add_argument("--end")
    lst.add_argument("--campaign-id")
    lst.add_argument("--subject-user-id")
    lst.add_argument("--status", default="complete")
    lst.set_defaults(func=cmd_chunks_list)

    delete = sub.add_parser("chunks-delete", help="Delete chunks in time range")
    delete.add_argument("--start", required=True)
    delete.add_argument("--end", required=True)
    delete.add_argument("--campaign-id")
    delete.add_argument("--subject-user-id")
    delete.add_argument("--dry-run", action="store_true")
    delete.set_defaults(func=cmd_chunks_delete)

    return parser


def cli_main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(cli_main())
