#!/usr/bin/env bash
# Pull the latest code, reinstall, validate, restart. Run on the VPS as a sudo user:
#   sudo /opt/halvane-utility-bot/deploy/update.sh
set -euo pipefail

APP=/opt/halvane-utility-bot
cd "$APP"

sudo -u halvane git pull --ff-only
sudo -u halvane "$APP/.venv/bin/pip" install --quiet -e .
sudo -u halvane "$APP/.venv/bin/python" -m halvane_bot check
systemctl restart halvane-bot
systemctl --no-pager --lines=5 status halvane-bot
