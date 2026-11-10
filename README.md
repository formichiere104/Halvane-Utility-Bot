# Halvane Utility Bot

The Recruitment Office's Discord bot for **Halvane Corporation** (Unusual Shipping).
Plan: `design/unusual-shipping/community/personnel-office-bot-plan.html` in the game repo (NPC-Framework).

| Phase | What | Status |
|---|---|---|
| 1 | Information messages (embeds) from YAML files, `/embeds` | **this release** |
| 2 | Account linking with an in-game code, read-only rank sync | planned |
| 3 | Bot owns the synced roles (Bloxlink binds removed) | planned |
| 4 | Approvals into the game (`/approve`, `/demote`, `/certify`) | planned |
| 5 | `/file`, `/reference`, game-side role assignment | planned |

## What phase 1 does

The welcome, rules, ranks and how-to pages live as YAML files in `embeds/`. The bot posts them and, on later
publishes, **edits the same messages**, so links to them never break. Role and channel mentions are written
as keys (`{role:station_chief}`, `{channel:events}`) and become real mentions. Mentions in embeds never ping.

| Command | Does |
|---|---|
| `/embeds list` | Every managed message and its state: not published, up to date, changed since last publish. |
| `/embeds preview key` | Shows the message privately, exactly as it would be posted. |
| `/embeds publish key [prune]` | Posts the message, or edits the one already posted. `prune` deletes extra old messages when a file got shorter. |
| `/embeds adopt key message_ids` | Takes over messages **this bot** already posted, so publish edits them. |

Only the server owner and the roles in `embed_managers` (`config/bot.yaml`) can use them; the group is also
hidden from members without Manage Server. Every publish is logged in `#server-logs`.

## Layout

```
config/bot.yaml        guild, managers, log channel, links ({link:key})
config/ids.yaml        role and channel ids by key (generated, see below)
embeds/*.yaml          one file per managed message set; the file name is the key; _name.yaml = draft
src/halvane_bot/       the bot (config, ids, store, embeds/, cogs/)
tests/                 pytest; includes a test that every real embed file renders
deploy/                systemd unit and update script for the VPS
```

## Writing an embed file

```yaml
channel: rules                   # channel key from config/ids.yaml
note: Server rules.              # shown in /embeds list
messages:                        # each item is one Discord message (max 10 embeds, 6,000 characters)
  - content: optional text above the embeds
    embeds:
      - title: Staff Conduct     # no mentions in titles, field names, footers (Discord shows them raw)
        color: "#B03A3A"
        description: |
          **1. Respect.** ... see {channel:open_a_ticket}, ask a {role:moderator}
        fields:
          - {name: Plain text, value: "Mentions work here: {role:team_lead}", inline: false}
        footer: Discretion. Delivery. Duty.
```

Tokens: `{role:key}`, `{channel:key}`, `{link:key}`. An unknown key or a message over Discord's limits fails
`check`. An empty link is a warning and blocks publishing until it is filled in `config/bot.yaml`.

## Local development (Windows)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m halvane_bot check     # validate everything, no token needed
.venv/Scripts/python -m pytest -q
```

Run `check` and the tests before every commit.

## Discord application

1. In the [Developer Portal](https://discord.com/developers/applications) create an application for this bot
   (keep the setup bot separate), open **Bot**, reset and copy the token.
2. Invite it with the scopes `bot` and `applications.commands` and the permissions View Channels, Send Messages,
   Embed Links and Read Message History:
   `https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&scope=bot%20applications.commands&permissions=84992`
3. Give it the **Bots** role. Its own role must sit under Bots, like Ticket Tool and carl-bot.

No privileged intents are needed for phase 1.

## VPS setup (DigitalOcean, Ubuntu 24.04)

```bash
sudo apt update && sudo apt install -y git python3-venv
sudo adduser --system --group --home /opt/halvane-utility-bot halvane
sudo -u halvane git clone https://github.com/formichiere104/Halvane-Utility-Bot.git /opt/halvane-utility-bot
cd /opt/halvane-utility-bot
sudo -u halvane python3 -m venv .venv
sudo -u halvane .venv/bin/pip install -e .
sudo -u halvane mkdir -p data
sudo -u halvane cp .env.example .env && sudo -u halvane nano .env    # paste DISCORD_TOKEN
sudo chmod 600 .env
sudo -u halvane .venv/bin/python -m halvane_bot check
sudo cp deploy/halvane-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now halvane-bot
journalctl -u halvane-bot -f
```

If the repository is private, give the VPS a read-only [deploy key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
and clone over SSH instead.

**Updating:** `sudo /opt/halvane-utility-bot/deploy/update.sh` (pull, reinstall, check, restart).

## Refreshing role and channel ids

After creating or renaming roles or channels:

```bash
.venv/bin/python -m halvane_bot export-ids
```

Existing keys are kept by id, so a rename in Discord doesn't break the embeds. New roles and channels get a key
from their name (`Packing · NCO` → `packing_nco`; a voice channel that shares a text channel's name ends in `_voice`).
Commit the new `config/ids.yaml`.

## The ranks message already in #ranks-explained

It was posted by the setup bot (GladOS). If this bot runs under GladOS's token, take it over with
`/embeds adopt ranks-explained 1569723197044170898`. Under its own application it can't edit GladOS's message:
publish `ranks-explained` to post a fresh copy, then delete the old one by hand.
