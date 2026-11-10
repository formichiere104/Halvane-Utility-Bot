"""OpenCloud request handling (status codes, retries, network errors) without the network."""
from __future__ import annotations

import asyncio

import pytest

from halvane_bot.roblox import opencloud
from halvane_bot.roblox.opencloud import OpenCloud, OpenCloudError


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(opencloud.asyncio, "sleep", instant)


def client(responses):
    """An OpenCloud whose HTTP layer returns the given (status, body) pairs, or raises given exceptions."""
    c = OpenCloud("key", 10775400484, 467115756, "LinkCodes")
    calls = []

    def fake_send(method, url, body=None):
        calls.append((method, url) if body is None else (method, url, body))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    c._send = fake_send
    return c, calls


def test_entry_found():
    c, calls = client([(200, '{"value": {"userId": 5, "at": 9}}')])
    assert asyncio.run(c.get_link_entry("3YAGUA")) == {"userId": 5, "at": 9}
    assert calls == [("GET", "https://apis.roblox.com/cloud/v2/universes/10775400484/data-stores/LinkCodes/entries/3YAGUA")]


def test_not_filed_yet_is_none():
    c, _ = client([(404, '{"code":"NOT_FOUND"}')])
    assert asyncio.run(c.get_link_entry("3YAGUA")) is None


def test_refused_key_raises_with_status():
    c, _ = client([(403, '{"code":"PERMISSION_DENIED"}')])
    with pytest.raises(OpenCloudError) as err:
        asyncio.run(c.get_link_entry("3YAGUA"))
    assert err.value.status == 403


def test_retries_rate_limits_and_network_errors_then_succeeds():
    c, calls = client([(429, ""), TimeoutError("timed out"), (503, ""), (200, '{"value": {"userId": 1, "at": 2}}')])
    assert asyncio.run(c.get_link_entry("X")) == {"userId": 1, "at": 2}
    assert len(calls) == 4


def test_network_failure_names_the_error():
    c, _ = client([TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()])
    with pytest.raises(OpenCloudError, match="network error: TimeoutError"):
        asyncio.run(c.get_link_entry("X"))


def test_membership_filter_is_encoded():
    c, calls = client([(200, '{"groupMemberships": []}')])
    assert asyncio.run(c.member_roles(1069090294)) is None
    assert "filter=user%20%3D%3D%20%27users%2F1069090294%27" in calls[0][1]


def test_put_entry_drops_nulls():
    c, calls = client([(200, "{}")])
    asyncio.run(c.put_entry("ApprovalLedger", "5", {"grade": None, "log": [{"step": None, "at": 1}, None]},
                            user_ids=[5]))
    method, url, body = calls[0]
    assert method == "PATCH" and url.endswith("/ApprovalLedger/entries/5?allowMissing=true")
    assert body["value"] == {"log": [{"at": 1}]} and body["users"] == ["users/5"]
