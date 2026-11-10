# Halvane Utility Bot

The Recruitment Office's Discord bot for **Halvane Corporation** (Unusual Shipping).
Plan: `design/unusual-shipping/community/personnel-office-bot-plan.html` in the game repo (NPC-Framework).

| Phase | What | Status |
|---|---|---|
| 1 | Information messages (embeds) from YAML files, `/embeds` | done |
| 2 | Account linking with an in-game code, rank sync (the bot owns the synced roles; Bloxlink skipped) | **built**: `/verify`, Verify button, `/update`, `/unlink`, `/sync`, sweep |
| 3 | merged into 2 (Bloxlink was never installed) | – |
| 4 | Approvals into the game (`/approve`, `/demote`, `/certify`) | **built**: ledger, group roles, tiers, live servers, notices |
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

## Secrets (.env)

The bot reads `.env` from the repo folder at start-up (next to `.env.example`). It is gitignored, so it never
reaches GitHub. There is one file per machine; nothing goes into system environment variables.

| Variable | Needed for | Notes |
|---|---|---|
| `DISCORD_TOKEN` | running the bot | GladOS's token |
| `ROBLOX_API_KEY` | phase 2+ | Open Cloud key, see [Key permissions](#key-permissions) |
| `SYNC_COMMANDS` | optional | `0` skips re-registering slash commands on start |

- **This PC:** `C:\Users\lucaf\Documents\Halvane Utility Bot\Halvane-Utility-Bot\.env`. Only needed to test
  Open Cloud calls locally, and the key only answers from IPs allowed on the key: add this PC's public IP
  to the key while developing, or test on the VPS. Don't run the full bot here while the VPS bot is online, or
  both will answer slash commands.
- **VPS:** `~/Halvane-Utility-Bot/.env`. Add the line with `nano`, keep it `chmod 600`, then restart the
  bot's screen session so it reads the new value.

### Key permissions

One Open Cloud key (Creator Hub → Open Cloud → API Keys), restricted to the VPS's IPv4 address. It must be owned
by an account ranked above every group role the bot assigns (the group owner's key is): Roblox only lets a key
assign roles below its owner's highest role.

| Area | Target | Operations | Used by |
|---|---|---|---|
| Groups | Staff Register (467115756) | Read | phase 2: role sync |
| Groups | Staff Register (467115756) | **Write** | phase 4: group roles on `/approve`, `/demote`, `/certify` |
| Data stores | experience 10775400484 | Read entry, List entry keys, Delete entry | phase 2: `LinkCodes` |
| Data stores | experience 10775400484 | **Create entry, Update entry** | phase 4: `ApprovalLedger` |
| Messaging Service | experience 10775400484 | **Publish** | phase 4: tells live servers to reload a player |

### Checking the Roblox key

```bash
.venv/bin/python -m halvane_bot roblox-check
```

Checks every permission above from this host and every group role id in `config/role_map.yaml` and
`config/approvals.yaml`. The only writes are harmless: one probe entry (`halvane-permission-probe`) is written to
`ApprovalLedger` and deleted again, one message goes to the `halvane-probe` topic no server listens to, and role
assignment is tried on a membership that doesn't exist. Run it on the VPS: the key is IP-restricted, so anywhere
else it answers 401. A `FAIL ... -> add ...` line names the permission to add to the key.

## Account linking and role sync (phase 2)

| Command | Who | Does |
|---|---|---|
| `/verify` or the **Verify** button in #verify | anyone | Private message with a 6-character code (10 minutes). File it at the Staff Register terminal in the game's Lobby; the message updates to "Linked" within seconds. |
| `/update` | anyone | Re-reads your Roblox group roles and fixes your Discord roles now. |
| `/unlink` | anyone | Removes the link and every synced role. |
| `/sync member` / `/sync all` | `sync_managers` | Sync one member, or every linked member (also reloads `role_map.yaml`). |

- Roles come only from `config/role_map.yaml`. The bot adds or removes **only** the 31 roles that file manages;
  staff, Dept., tier, Restricted and every other role are never touched.
- A sweep re-syncs every linked member every `sync_interval_minutes` (15). Unlinked members are only synced when
  they join (they get Unverified) or run `/update`, so roles given by hand to unlinked staff are left alone.
- If Roblox doesn't answer, nothing changes for that member.
- Linked members get their Roblox username as nickname (`nickname_format`), except the server owner.
- One Roblox account belongs to one Discord member; filing a code for an account linked elsewhere moves it.
- Needs the **Server Members** privileged intent (Developer Portal → Bot) and `ROBLOX_API_KEY` in `.env`.
  Without the key the bot still runs phase 1, and `/verify` says it is unavailable.

### Linking contract

The game's Staff Register terminal (NPC Lab, `NPC_Lab/StaffRegister` in the NPC-Framework repo) and this bot
share one format, set under `link_codes:` in `config/bot.yaml` and pinned by `tests/test_link_codes.py`:

- Codes are 6 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (no 0/O, no 1/I). The bot generates them.
- The game writes data store `LinkCodes`, key = the code, value = `{ "userId": <number>, "at": <unix seconds> }`.
- The bot reads the entry, links the accounts if it is younger than `expires_minutes`, then deletes it.
- Every sweep also deletes entries nobody is waiting for (older than `expires_minutes` + 5 minutes), so the
  store only ever holds codes in use.

## Approvals (phase 4)

The Directorate records a decision in Discord; the bot carries it to the group, the game and the server.

| Command | Does |
|---|---|
| `/approve member division rank` | Approves a leadership rank in a division (Team Lead → Station Chief), or **Posting** to unlock the division. Refused if the member already holds that rank or higher there. |
| `/demote member division to` | Lowers the rank in that division, or **None** removes it. Not announced. |
| `/certify member certification` | Records a certification (Reach Truck, Registry, Instructor Qualified, ...). |

Only `sync_managers` (Director to Assistant Director) can run them; the commands are hidden from members
without Manage Server. Divisions, the ladder and certifications live in `config/approvals.yaml`.

Each command, in order, and nothing is written if a step before the write fails:

1. Checks the member has linked a Roblox account and is in the Staff Register.
2. Reads the member's entry in the game's `ApprovalLedger` data store, applies the change, writes it back
   (retried if the game or another command changed it meanwhile).
3. Sets group roles: the **grade** role is the member's highest step in any division (the old grade role goes),
   posting and certification roles are only added.
4. Publishes `{"userId": ..., "kind": "ledger"}` on topic `personnel`, so a live server reloads that player at
   once; otherwise the game reads the ledger on the next join.
5. Sets the division's Discord tier role (NCO / Officer / Command).
6. Syncs the member's Discord roles from the group, as `/update` does.
7. Posts the notice in #personnel-notices (approvals and certifications) and a line in #server-logs.

Quota is still checked by staff in the ticket: the game has no Compliance or Personnel File yet.

### Ledger contract

The game's reader (`NPC_Lab/Personnel/PersonnelLedger` in NPC-Framework) and this bot share one format. Only the
bot writes it.

- Data store `ApprovalLedger`, key = the Roblox user id as a string.
- Value: `{"v": 1, "leadership": {"<division>": "<step>"}, "grade": "<step>" | null, "postings": ["<division>"],
  "certifications": ["<key>"], "updatedAt": <unix seconds>, "log": [last 50 changes]}`.
- The game mirrors it onto the player as `HC_Grade`, `HC_Leadership` (`division=step,...`), `HC_Postings` and
  `HC_Certifications`.

## Local development (Windows)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m halvane_bot check     # validate everything, no token needed
.venv/Scripts/python -m pytest -q
```

Run `check` and the tests before every commit.

## Bot account: GladOS

The bot runs as **GladOS** (application id `1569664921501831259`), the same bot that built the server. It is
already in the server with the Bots role, and `/embeds` is registered in the guild (checked 10 Nov 2026).
No privileged intents are needed for phase 1.

- **One token, two users.** The same token is used by the `discord-setup` MCP server on the dev PC
  (`.mcp.json` in NPC-Framework, gitignored) and by this bot on the VPS. Two connections at once are fine. If the
  token is ever reset, update both places.
- **Administrator.** GladOS keeps Administrator (owner's decision, 10 Nov 2026): it is the owner's own bot and
  will do more than this project. That makes the token as powerful as the owner's account, so keep it only in
  the two `.env`/`.mcp.json` files and reset it at once if it is ever exposed.
- **Role order.** GladOS's role must stay above every role it syncs or tiers, or Discord refuses the change.

## Running on the VPS in `screen` (next to another bot)

Each bot gets its own folder, virtual environment, `.env` and `screen` session, so they never share packages or
settings.

```bash
# 1. One-time setup (as your normal user, in your home folder)
python3 --version                                  # needs 3.11 or newer
sudo apt install -y python3-venv git screen        # skip what is already installed
cd ~
git clone https://github.com/formichiere104/Halvane-Utility-Bot.git
cd Halvane-Utility-Bot
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env && nano .env                  # DISCORD_TOKEN=<GladOS token>
chmod 600 .env
.venv/bin/python -m halvane_bot check              # must end with "All good."

# 2. Start it in its own detached screen session
screen -dmS halvane bash -c 'cd ~/Halvane-Utility-Bot && .venv/bin/python -m halvane_bot run 2>&1 | tee -a bot.log'
screen -ls                                         # shows "halvane" next to your other bot's session

# Look at it: screen -r halvane      Leave it running: Ctrl+A, then D      Stop it: Ctrl+C inside it
```

**Updating:**

```bash
screen -S halvane -X quit                          # stop the bot
cd ~/Halvane-Utility-Bot && git pull --ff-only
.venv/bin/pip install -e . && .venv/bin/python -m halvane_bot check
screen -dmS halvane bash -c 'cd ~/Halvane-Utility-Bot && .venv/bin/python -m halvane_bot run 2>&1 | tee -a bot.log'
```

`screen` does not restart the bot after a crash or a reboot. Run the start line again by hand, or switch to the
systemd service below later.

## VPS setup with systemd (alternative)

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

## First run on the server

All five messages were published on 10 Nov 2026 from the dev PC, so their ids live in that PC's
`data/halvane.db`, not on the VPS. Give them to the VPS bot once, so later publishes edit them instead of
posting duplicates. Either copy the database before the first start:

```bash
scp "C:\Users\lucaf\Documents\Halvane Utility Bot\Halvane-Utility-Bot\data\halvane.db" <user>@<vps>:~/Halvane-Utility-Bot/data/
```

or, once the bot runs, adopt them in Discord:

```
/embeds adopt key:welcome             message_ids:1569808141044945009
/embeds adopt key:rules               message_ids:1569808147470876855
/embeds adopt key:how-to-join         message_ids:1569808154261200947
/embeds adopt key:ranks-explained     message_ids:1569723197044170898
/embeds adopt key:how-promotion-works message_ids:1569808171600580720
```

Then `/embeds publish <key>` for each. Nothing visibly changes, and `/embeds list` shows them as up to date.
