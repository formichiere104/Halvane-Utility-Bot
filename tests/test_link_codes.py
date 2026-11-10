"""The link-code contract shared with the game's Staff Register terminal (NPC-Framework, NPC_Lab/StaffRegister)."""
from __future__ import annotations

from halvane_bot.config import load_settings

GAME_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # LinkCode.luau, pattern ^[A-HJ-NP-Z2-9]+$
GAME_LENGTH = 6


def test_contract_matches_the_game():
    s = load_settings()
    assert s.link_store == "LinkCodes"
    assert s.link_alphabet == GAME_ALPHABET
    assert s.link_length == GAME_LENGTH
    assert not set("0O1I") & set(s.link_alphabet), "look-alike characters must stay out"
    assert len(set(s.link_alphabet)) == len(s.link_alphabet) == 32
