"""Entry point.

    python -m halvane_bot            run the bot (same as `run`)
    python -m halvane_bot check      validate config, ids and embeds offline
    python -m halvane_bot export-ids write config/ids.yaml from the live server (needs DISCORD_TOKEN)
"""
from __future__ import annotations

import argparse
import sys

from .config import ConfigError, discord_token, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="halvane_bot", description="Halvane Utility Bot")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "check", "export-ids"])
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        if args.command == "check":
            from .check import run_check
            return run_check(settings)
        if args.command == "export-ids":
            from .export_ids import export_ids
            path = export_ids(settings)
            print(f"wrote {path}")
            return 0

        import discord

        from .bot import HalvaneBot
        discord.utils.setup_logging()
        HalvaneBot(settings).run(discord_token(), log_handler=None)
        return 0
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
