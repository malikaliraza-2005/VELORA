"""P12: the production server trusts forwarding headers from the proxy alone, and logs
through VELORA's redacting formatter."""

from __future__ import annotations

import pytest

from velora.serve import ServeSettings


def test_defaults_listen_locally_and_trust_no_proxy() -> None:
    settings = ServeSettings.from_env({})
    assert settings == ServeSettings(host="127.0.0.1", port=8000, trusted_proxies=())
    config = settings.uvicorn_config()
    assert config.proxy_headers is False
    assert config.log_config is None  # VELORA's logging, not uvicorn's handlers
    assert config.server_header is False


def test_the_proxy_is_trusted_when_named() -> None:
    settings = ServeSettings.from_env(
        {
            "VELORA_HTTP_HOST": "0.0.0.0",  # noqa: S104 - inside its container
            "VELORA_HTTP_PORT": "8001",
            "VELORA_FORWARDED_ALLOW_IPS": "172.30.10.10, 10.0.0.0/8",
        }
    )
    config = settings.uvicorn_config()
    assert (config.host, config.port) == ("0.0.0.0", 8001)  # noqa: S104
    assert config.proxy_headers is True
    assert config.forwarded_allow_ips == ["172.30.10.10", "10.0.0.0/8"]


@pytest.mark.parametrize(
    "environ",
    [
        # Trusting everyone would let any client pick its own address for the rate limit.
        {"VELORA_FORWARDED_ALLOW_IPS": "*"},
        {"VELORA_FORWARDED_ALLOW_IPS": "caddy"},
        {"VELORA_HTTP_PORT": "0"},
        {"VELORA_HTTP_PORT": "http"},
    ],
)
def test_bad_settings_are_refused(environ: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ServeSettings.from_env(environ)
