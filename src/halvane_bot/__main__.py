"""Entry point.

    python -m halvane_bot            run the bot (same as `run`)
    python -m halvane_bot check      validate config, ids and embeds offline
    python -m halvane_bot export-ids write config/ids.yaml from the live server (needs DISCORD_TOKEN)
    python -m halvane_bot roblox-check  prove the Open Cloud key works from this host (harmless probes only)
    python -m halvane_bot link-check CODE  show what Roblox has filed under one link code (read-only)
"""
from __future__ import annotations

import argparse
import sys

from .config import ConfigError, discord_token, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="halvane_bot", description="Halvane Utility Bot")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "check", "export-ids", "roblox-check", "link-check"])
    parser.add_argument("code", nargs="?", help="for link-check: the code filed in game")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        if args.command == "check":
            from .check import run_check
            return run_check(settings)
        if args.command == "roblox-check":
            from .roblox_check import run_roblox_check
            return run_roblox_check(settings)
        if args.command == "link-check":
            if not args.code:
                parser.error("link-check needs the code, e.g. `python -m halvane_bot link-check EKEVSF`")
            from .roblox_check import run_link_check
            return run_link_check(settings, args.code)
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
