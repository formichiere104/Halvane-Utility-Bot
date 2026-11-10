"""`python -m halvane_bot check`: validate config, ids and every embed file offline (no token needed)."""
from __future__ import annotations

from .config import ConfigError, Settings
from .embeds.limits import embed_chars
from .embeds.loader import EmbedSpecError, load_all
from .embeds.render import RenderError, render_set
from .ids import load_ids


def run_check(settings: Settings) -> int:
    errors = 0
    if not settings.ids_file.exists():
        print(f"ERROR  {settings.ids_file} is missing; run `python -m halvane_bot export-ids`")
        return 1
    ids = load_ids(settings.ids_file)
    if ids.guild_id != settings.guild_id:
        print(f"ERROR  ids.yaml is for guild {ids.guild_id}, bot.yaml says {settings.guild_id}")
        errors += 1
    for key in settings.embed_managers:
        if key not in ids.roles:
            print(f"ERROR  embed_managers: unknown role key '{key}'")
            errors += 1
    for key in settings.sync_managers:
        if key not in ids.roles:
            print(f"ERROR  sync_managers: unknown role key '{key}'")
            errors += 1
    if settings.log_channel and settings.log_channel not in ids.channels:
        print(f"ERROR  log_channel: unknown channel key '{settings.log_channel}'")
        errors += 1

    if settings.role_map_file:
        from .sync.role_map import RoleMapError, load_role_map
        try:
            rm = load_role_map(settings.role_map_file, ids)
            if settings.group_id and rm.group_id != settings.group_id:
                print(f"ERROR  role_map.yaml is for group {rm.group_id}, bot.yaml says {settings.group_id}")
                errors += 1
            else:
                print(f"ok     role map: {len(rm.group_roles)} group roles -> {len(rm.managed)} managed Discord roles, "
                      f"{len(rm.protected)} protected")
        except RoleMapError as exc:
            print(f"ERROR  {exc}")
            errors += 1

    approvals_file = settings.root / "config" / "approvals.yaml"
    if approvals_file.exists():
        from .approvals.ledger import ApprovalConfigError, load_approvals
        try:
            cfg = load_approvals(approvals_file, ids)
            if cfg.notices_channel not in ids.channels:
                print(f"ERROR  approvals.yaml: unknown channel key '{cfg.notices_channel}'")
                errors += 1
            else:
                print(f"ok     approvals: {len(cfg.divisions)} divisions, {len(cfg.steps)} ranks, "
                      f"{len(cfg.certifications)} certifications")
        except ApprovalConfigError as exc:
            print(f"ERROR  {exc}")
            errors += 1

    try:
        sets = load_all(settings.embeds_dir)
    except EmbedSpecError as exc:
        print(f"ERROR  {exc}")
        return 1
    print(f"{len(ids.roles)} roles, {len(ids.channels)} channels, {len(sets)} embed files\n")
    for key, es in sets.items():
        try:
            rendered = render_set(es, ids, settings.links)
        except RenderError as exc:
            print(f"ERROR  {exc}")
            errors += 1
            continue
        sizes = ", ".join(f"{len(m['embeds'])} embeds/{sum(embed_chars(e) for e in m['embeds'])} chars"
                          for m in rendered.messages)
        status = "WARN " if rendered.warnings else "ok   "
        print(f"{status}  {key:<22} -> #{es.channel:<20} {len(rendered.messages)} message(s): {sizes}")
        for w in rendered.warnings:
            print(f"         {w} (publishing is blocked until it is set)")
    print("\n" + ("All good." if not errors else f"{errors} error(s)."))
    return 1 if errors else 0


__all__ = ["run_check", "ConfigError"]
