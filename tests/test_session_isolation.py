"""Use real local HTTP responses to prove login-cookie isolation."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestServer
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from yarl import URL

from custom_components.unifi_air_quality import async_setup_entry, async_unload_entry
from custom_components.unifi_air_quality.api import PrivateProtectClient
from custom_components.unifi_air_quality.config_flow import UnifiAirQualityConfigFlow
from custom_components.unifi_air_quality.const import DOMAIN

from .ha_test_helpers import MockConfigEntry
from .test_client import _client
from .test_config_flow import USER_INPUT


async def test_login_cookies_do_not_reach_shared_or_other_entry_session(
    hass, socket_enabled
):
    """Verify login cookies do not reach shared or other entry session."""

    seen_accounts = []

    async def login(request):
        """Set a synthetic session cookie identifying the supplied test account."""
        account = (await request.json())["username"]
        response = web.json_response({})
        response.set_cookie("SESSION", account)
        return response

    async def bootstrap(request):
        """Record which entry authenticated each adapter bootstrap request."""
        seen_accounts.append(request.cookies.get("SESSION"))
        return web.json_response(
            {
                "nvr": {"id": "synthetic-console"},
                "sensors": [],
            }
        )

    async def cookies(request):
        """Echo the session account so the test can detect credential leakage."""
        return web.json_response({"account": request.cookies.get("SESSION")})

    app = web.Application()
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/proxy/protect/api/bootstrap", bootstrap)
    app.router.add_get("/cookies", cookies)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = str(site._server.sockets[0].getsockname()[1])
    base = URL("http://127.0.0.1:" + url)
    shared = async_get_clientsession(hass)
    first = async_create_clientsession(hass, cookie_jar=DummyCookieJar())
    second = async_create_clientsession(hass, cookie_jar=DummyCookieJar())
    clients = [
        PrivateProtectClient(
            session,
            host="protect.local",
            port=443,
            username=account,
            password="synthetic",
            verify_ssl=False,
        )
        for session, account in (
            (first, "synthetic-first"),
            (second, "synthetic-second"),
        )
    ]
    try:
        for client in clients:
            client._base_url = str(base)
        await asyncio.gather(*(client.async_get_snapshot() for client in clients))
        assert set(seen_accounts) == {"synthetic-first", "synthetic-second"}
        for session in (first, second, shared):
            assert not session.cookie_jar.filter_cookies(base)
            async with session.get(base / "cookies") as response:
                # An unadorned request must not inherit any adapter credentials.
                assert await response.json() == {"account": None}
        for client, session, account in zip(
            clients,
            (first, second),
            ("synthetic-first", "synthetic-second"),
            strict=True,
        ):
            async with session.get(
                base / "cookies", headers=client._headers.copy()
            ) as response:
                assert await response.json() == {"account": account}
        await clients[0].async_close()
        assert first.closed
        assert not second.closed
        assert not shared.closed
    finally:
        await asyncio.gather(*(client.async_close() for client in clients))
        await runner.cleanup()


@pytest.mark.parametrize("error", [None, RuntimeError(), asyncio.CancelledError()])
async def test_config_validation_closes_session_on_every_exit(hass, error):
    """Verify config validation closes session on every exit."""
    flow = UnifiAirQualityConfigFlow()
    flow.hass = hass
    captured = []

    def create_client(session, **kwargs):
        """Capture sessions and inject a client or a construction failure."""
        captured.append(session)
        if error:
            raise error
        from types import SimpleNamespace

        client = AsyncMock()
        client.async_get_snapshot.return_value = SimpleNamespace(
            console_id="synthetic-console", console_name="Protect"
        )
        return client

    with patch(
        "custom_components.unifi_air_quality.config_flow.PrivateProtectClient",
        side_effect=create_client,
    ):
        if error:
            with pytest.raises(type(error)):
                await flow._validate(USER_INPUT)
        else:
            assert await flow._validate(USER_INPUT) == ("synthetic-console", "Protect")
            assert await flow._validate(USER_INPUT) == ("synthetic-console", "Protect")
            assert captured[0] is not captured[1]
    assert all(session.closed for session in captured)
    assert all(isinstance(session.cookie_jar, DummyCookieJar) for session in captured)


async def test_setup_entries_own_distinct_sessions_and_close_on_unload(hass):
    """Verify setup entries own distinct sessions and close on unload."""
    from custom_components.unifi_air_quality.models import ProtectSnapshot

    entries = [MockConfigEntry(domain=DOMAIN, data=USER_INPUT.copy()) for _ in range(2)]
    for entry in entries:
        entry.add_to_hass(hass)
    sessions = []

    def create_client(session, **kwargs):
        """Capture entry sessions and detach each one when its fake client closes."""
        sessions.append(session)
        client = AsyncMock()
        from unittest.mock import MagicMock

        client.set_update_callback = MagicMock()
        client.set_auth_failure_callback = MagicMock()
        client.start_websocket = MagicMock()
        client.async_get_snapshot.return_value = ProtectSnapshot(
            "synthetic-console", "Protect", None, None, ()
        )
        client.async_close.side_effect = session.detach
        return client

    with (
        patch(
            "custom_components.unifi_air_quality.PrivateProtectClient",
            side_effect=create_client,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(
            hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
        ),
        patch(
            "custom_components.unifi_air_quality.UnifiAirQualityCoordinator"
        ) as coordinators,
    ):
        coordinators.return_value.data = ProtectSnapshot(
            "synthetic-console", "Protect", None, None, ()
        )
        coordinators.return_value.async_config_entry_first_refresh = AsyncMock()
        for entry in entries:
            assert await async_setup_entry(hass, entry)
        assert sessions[0] is not sessions[1]
        assert sessions[0].cookie_jar is not sessions[1].cookie_jar
        assert all(
            isinstance(session.cookie_jar, DummyCookieJar) for session in sessions
        )
        assert await async_unload_entry(hass, entries[0])
        assert sessions[0].closed and not sessions[1].closed
        assert await async_unload_entry(hass, entries[1])
        assert sessions[1].closed


@pytest.mark.parametrize("late_status", [200, 401, 403])
async def test_late_cookie_response_cannot_replace_renewed_session(
    hass, socket_enabled, late_status
):
    """Keep the current wire cookie after a superseded response sets an old one."""
    old_started = asyncio.Event()
    release_old = asyncio.Event()
    state = {"logins": 0, "reads": 0, "seen": []}
    payload = {"nvr": {"id": "synthetic-console"}, "sensors": []}

    async def login(request):
        """Issue a unique synthetic cookie for each successful session renewal."""
        await request.json()
        state["logins"] += 1
        response = web.json_response({})
        response.set_cookie("SESSION", f"synthetic-{state['logins']}")
        return response

    async def bootstrap(request):
        """Delay one old response until a newer authenticated refresh has finished."""
        state["reads"] += 1
        state["seen"].append(request.cookies.get("SESSION"))
        if state["reads"] == 2:
            old_started.set()
            await release_old.wait()
            response = web.json_response(payload, status=late_status)
            response.set_cookie("SESSION", "synthetic-1")
            return response
        if request.cookies.get("SESSION") != f"synthetic-{state['logins']}":
            return web.json_response({}, status=401)
        return web.json_response(payload)

    app = web.Application()
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/proxy/protect/api/bootstrap", bootstrap)
    async with TestServer(app, host="127.0.0.1") as server:
        session = async_create_clientsession(hass, cookie_jar=DummyCookieJar())
        client = _client(session)
        client._base_url = str(server.make_url("")).rstrip("/")
        old_request = None
        try:
            await client.async_get_snapshot()
            old_request = asyncio.create_task(client.async_get_snapshot())
            await old_started.wait()
            await client._async_authenticate(
                rejected_generation=client._auth_generation
            )
            confirmed = await client.async_get_snapshot()
            release_old.set()
            assert await old_request is confirmed
            assert not client.authentication_failed
            assert not session.cookie_jar.filter_cookies(server.make_url(""))
            assert client._headers["Cookie"] == "SESSION=synthetic-2"
            await client.async_get_snapshot()
            assert state["seen"] == [
                "synthetic-1", "synthetic-1", "synthetic-2", "synthetic-2"
            ]
            assert state["logins"] == 2
        finally:
            release_old.set()
            if old_request is not None:
                await old_request
            await client.async_close()
