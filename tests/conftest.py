from __future__ import annotations

import pytest

from halvane_bot.ids import IdRegistry


@pytest.fixture
def ids() -> IdRegistry:
    return IdRegistry(
        guild_id=1,
        roles={"station_chief": 100, "team_lead": 101, "dept_21": 102},
        channels={"rules": 200, "events": 201, "ranks_explained": 202},
    )


@pytest.fixture
def links() -> dict[str, str]:
    return {"roblox_group": "https://example.com/group", "game": ""}
