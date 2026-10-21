"""P5-T1 and P5-T7: the `WhatsAppProvider` contract.

`CONTRACT_PROVIDERS` lists every working adapter. The OpenWA adapter joins it in P5-T2 (after
the Go, V-01), with its contract tests against real payloads recorded in P0. The Meta Cloud
adapter is interface-only: it must fit the interface, and every method must raise.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest

from velora.messaging.provider import (
    OBSERVED_STATES,
    ChannelDownError,
    DeliveryStatus,
    InboundMedia,
    InboundMessage,
    InvalidWebhookError,
    MediaRef,
    ProviderEvent,
    SessionState,
    WhatsAppProvider,
)
from velora.messaging.providers.fake import SIGNATURE_HEADER, FakeProvider
from velora.messaging.providers.meta_cloud import MetaCloudAdapter

SESSION = "session-contract"
AT = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)

CONTRACT_PROVIDERS: dict[str, Callable[[], WhatsAppProvider]] = {"fake": FakeProvider}
contract = pytest.mark.parametrize(
    "make", list(CONTRACT_PROVIDERS.values()), ids=list(CONTRACT_PROVIDERS)
)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=loop)


def sample_events() -> list[ProviderEvent]:
    return [
        InboundMessage(
            session_ref=SESSION,
            dedup_key="wamid-1",
            provider_message_id="wamid-1",
            sender="+923000000101",
            chat_kind="direct",
            kind="image",
            text="2 zinger",
            received_at=AT,
            media=InboundMedia("media-1", "image/jpeg"),
        ),
        DeliveryStatus(SESSION, "out-1:read", "out-1", "read", AT),
        SessionState(SESSION, "needs_qr", AT, "phone unlinked"),
    ]


def test_the_interface_is_the_one_the_plan_names() -> None:
    assert WhatsAppProvider.__abstractmethods__ == {
        "capabilities",
        "send_text",
        "send_media",
        "get_session_status",
        "start_session",
        "get_qr",
        "logout",
        "parse_webhook",
    }


@contract
def test_capabilities_are_reported(make: Callable[[], WhatsAppProvider]) -> None:
    caps = make().capabilities()
    assert caps.max_text_length > 0
    assert isinstance(caps.buttons, bool) and isinstance(caps.lists, bool)


@contract
def test_send_returns_a_provider_message_id(make: Callable[[], WhatsAppProvider]) -> None:
    provider = make()
    text = run(provider.send_text(SESSION, "+923000000101", "hello", idempotency_key="k1"))
    media = run(
        provider.send_media(
            SESSION,
            "+923000000101",
            MediaRef("https://files.velora.test/menu.pdf", "application/pdf", "menu.pdf"),
            caption="Our menu",
            idempotency_key="k2",
        )
    )
    assert text.provider_message_id and media.provider_message_id
    assert text.provider_message_id != media.provider_message_id


@contract
def test_an_idempotent_provider_sends_a_key_once(make: Callable[[], WhatsAppProvider]) -> None:
    provider = make()
    if not provider.capabilities().idempotent_send:
        pytest.skip("provider does not claim idempotent sends")
    first = run(provider.send_text(SESSION, "+923000000101", "hi", idempotency_key="same"))
    again = run(provider.send_text(SESSION, "+923000000101", "hi", idempotency_key="same"))
    assert first == again


@contract
def test_session_status_is_a_canonical_state(make: Callable[[], WhatsAppProvider]) -> None:
    assert run(make().get_session_status(SESSION)) in OBSERVED_STATES


@contract
def test_webhook_round_trip_gives_canonical_events(make: Callable[[], WhatsAppProvider]) -> None:
    provider = make()
    assert isinstance(provider, FakeProvider)  # each adapter brings its own recorded payloads
    events = sample_events()
    headers, body = provider.signed_webhook(SESSION, events)
    assert provider.parse_webhook(SESSION, headers, body) == events


@contract
def test_a_bad_signature_is_rejected(make: Callable[[], WhatsAppProvider]) -> None:
    provider = make()
    assert isinstance(provider, FakeProvider)
    headers, body = provider.signed_webhook(SESSION, sample_events())
    with pytest.raises(InvalidWebhookError):
        provider.parse_webhook(SESSION, {SIGNATURE_HEADER: "sha256=" + "0" * 64}, body)
    with pytest.raises(InvalidWebhookError):
        provider.parse_webhook(SESSION, {}, body)
    with pytest.raises(InvalidWebhookError):
        provider.parse_webhook(SESSION, headers, body.replace(b"zinger", b"zinged"))


@contract
def test_a_webhook_for_another_session_is_rejected(make: Callable[[], WhatsAppProvider]) -> None:
    provider = make()
    assert isinstance(provider, FakeProvider)
    # Signed with this session's secret, but the payload names another session.
    headers, body = provider.signed_webhook(SESSION, [], payload_session="session-other")
    with pytest.raises(InvalidWebhookError):
        provider.parse_webhook(SESSION, headers, body)


def test_fake_provider_failure_injection_and_sessions() -> None:
    fake = FakeProvider()
    fake.fail_next(SESSION, ChannelDownError("needs_qr"))
    with pytest.raises(ChannelDownError):
        run(fake.send_text(SESSION, "+923000000101", "x", idempotency_key="a"))
    run(fake.send_text(SESSION, "+923000000101", "x", idempotency_key="a"))
    assert fake.texts_to("+923000000101") == ["x"]

    run(fake.start_session("new"))
    assert run(fake.get_qr("new")) == "fake-qr:new"
    fake.set_state("new", "connected")
    assert run(fake.get_qr("new")) is None
    run(fake.logout("new"))
    assert run(fake.get_session_status("new")) == "logged_out"


# Meta Cloud API stub (P5-T7) --------------------------------------------------------------


def _dummy_args(method: Callable[..., Any]) -> dict[str, Any]:
    values = {
        "session_ref": SESSION,
        "to": "+923000000101",
        "text": "x",
        "media": MediaRef("https://files.velora.test/a.jpg", "image/jpeg"),
        "caption": None,
        "idempotency_key": "k",
        "headers": {},
        "body": b"{}",
    }
    params = inspect.signature(method).parameters
    return {name: values[name] for name in params}


@pytest.mark.parametrize("name", sorted(WhatsAppProvider.__abstractmethods__))
def test_meta_stub_fits_the_interface_and_every_method_raises(name: str) -> None:
    adapter = MetaCloudAdapter()  # would raise TypeError if a method were missing
    assert adapter.name == "meta_cloud"
    method = getattr(adapter, name)
    with pytest.raises(NotImplementedError, match="AD-06"):
        result = method(**_dummy_args(method))
        if inspect.iscoroutine(result):
            run(result)
