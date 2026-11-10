"""Buttons an embed file may place under a message. Each action is handled by a persistent view in a cog."""
from __future__ import annotations

VERIFY_CUSTOM_ID = "halvane:verify"

# action name in YAML -> Discord custom_id
ACTIONS: dict[str, str] = {
    "verify": VERIFY_CUSTOM_ID,
}

STYLES = {"primary", "secondary", "success", "danger"}
MAX_BUTTONS = 5      # one row
MAX_LABEL = 80
