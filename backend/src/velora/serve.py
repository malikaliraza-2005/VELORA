"""`python -m velora.serve`: the production server for the API and dashboard (P12).

Runs `velora.asgi:app` under uvicorn with VELORA's logging: uvicorn's own error and access
lines go through the same redacting JSON formatter as everything else, instead of uvicorn's
default plain-text handlers (P5 finding).

TLS is the edge proxy's job (Caddy, `infra/`); this server speaks plain HTTP on the private
network. The client address used by the login rate limit comes from `X-Forwarded-For` only
when the request arrives from an address in VELORA_FORWARDED_ALLOW_IPS (the proxy). Without
it, every request would appear to come from the proxy and the limit would be global (P3
finding); with it set too widely, a client could pick its own address.

VELORA_HTTP_HOST     address to listen on (default 127.0.0.1)
VELORA_HTTP_PORT     port (default 8000)
VELORA_FORWARDED_ALLOW_IPS
                     comma-separated proxy addresses whose forwarding headers are trusted.
                     Unset: none are trusted. `*` is refused.
"""

from __future__ import annotations

import ipaddress
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

import uvicorn

from velora.config import Settings
from velora.redaction import configure_logging


@dataclass(frozen=True)
class ServeSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    # Empty: forwarding headers are ignored and the peer address is the client.
    trusted_proxies: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ServeSettings:
        source = os.environ if environ is None else environ
        host = source.get("VELORA_HTTP_HOST", "").strip() or cls.host
        port_text = source.get("VELORA_HTTP_PORT", "").strip()
        try:
            port = cls.port if not port_text else int(port_text)
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            raise ValueError("VELORA_HTTP_PORT must be a port number")
        proxies: list[str] = []
        for item in source.get("VELORA_FORWARDED_ALLOW_IPS", "").split(","):
            item = item.strip()
            if not item:
                continue
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError:
                raise ValueError(
                    "VELORA_FORWARDED_ALLOW_IPS must list proxy addresses or networks, "
                    f"got {item!r}"
                ) from None
            proxies.append(item)
        return cls(host=host, port=port, trusted_proxies=tuple(proxies))

    def uvicorn_config(self) -> uvicorn.Config:
        return uvicorn.Config(
            "velora.asgi:app",
            host=self.host,
            port=self.port,
            # velora.asgi configures logging; uvicorn must not install its own handlers.
            log_config=None,
            proxy_headers=bool(self.trusted_proxies),
            forwarded_allow_ips=list(self.trusted_proxies) or None,
            server_header=False,
        )


def main() -> int:
    try:
        settings = Settings.from_env()
        serve = ServeSettings.from_env()
    except ValueError as exc:
        print(f"invalid settings: {exc}", file=sys.stderr)
        return 2
    # Configured here as well, so uvicorn's start-up lines are formatted before the app loads.
    configure_logging(settings.log_level, fmt=settings.log_format)
    uvicorn.Server(serve.uvicorn_config()).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
