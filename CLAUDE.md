# Halvane Utility Bot: project instructions

Python Discord bot for Halvane Corporation (Unusual Shipping). The full plan is the "Personnel Office Bot"
page in the game repo: `NPC-Framework/design/unusual-shipping/community/personnel-office-bot-plan.html`.
The Discord structure it serves is the Recruitment Office Blueprint in the same folder.

## Repos
- Everything about the bot is committed to **this** repo. Everything about the Roblox game goes to NPC-Framework.
- The bot runs on the user's DigitalOcean VPS from this repo (systemd, `deploy/`). Never touch the VPS yourself.
- Commit and push after each completed task, with the phase in the message ("Phase 1: ...").

## Secrets
- The bot token (and later the Roblox Open Cloud key) live only in `.env` on the host. Never commit them, never
  print them, never put them in config files or test data.

## Code rules
- Python 3.11+, type hints, `from __future__ import annotations`. One responsibility per module.
- No raw Discord ids in code or embeds: use keys from `config/ids.yaml` (`{role:key}`, `{channel:key}`).
- Content is data: adding a message means adding a YAML file in `embeds/`, never code.
- Rendering stays pure (no discord.py in `embeds/render.py`, `loader.py`, `limits.py`) so it is testable offline.
- Every message the bot sends uses `AllowedMentions.none()`.
- Before committing: `.venv/Scripts/python -m halvane_bot check` and `.venv/Scripts/python -m pytest -q` both pass.

## Content rules (from the game's design documents)
- Never explain the lore. Dept. 21 and Dept. 22 appear only as "[REDACTED]"; Records and Retention only as
  "File-keepers, application via [REDACTED]."
- Never show a Watch level, Clearance or the intake profile anywhere, including staff-only replies.
- Sign copy and in-world names come from the design documents; don't invent new ones.
- Corporate, quiet tone. Plain sentences.
