"""Auth: api key + source-IP allowlist.

Loaded from a YAML file at startup. The FastAPI dependency `authorize`
returns the matched `Client` so downstream handlers (and future middlewares
like audit logging) can attribute the request.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Union

import yaml
from fastapi import Header, HTTPException, Request, status

from app.config import get_settings

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


@dataclass(frozen=True)
class Client:
    name: str
    api_key: str
    allowed_networks: tuple[IPNetwork, ...]

    def ip_allowed(self, ip: IPAddress) -> bool:
        return any(ip in net for net in self.allowed_networks)


class AuthStore:
    def __init__(self, clients: Iterable[Client]) -> None:
        self._by_key: dict[str, Client] = {c.api_key: c for c in clients}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AuthStore":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        clients: list[Client] = []
        for entry in raw.get("clients", []):
            networks = tuple(
                ipaddress.ip_network(s, strict=False)
                for s in entry.get("allowed_ips", [])
            )
            clients.append(
                Client(
                    name=entry["name"],
                    api_key=entry["api_key"],
                    allowed_networks=networks,
                )
            )
        return cls(clients)

    def get(self, api_key: str) -> Client | None:
        return self._by_key.get(api_key)

    def __len__(self) -> int:
        return len(self._by_key)


def _client_ip(request: Request) -> IPAddress:
    settings = get_settings()
    if settings.trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return ipaddress.ip_address(fwd.split(",")[0].strip())
    if request.client is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no client address on request")
    return ipaddress.ip_address(request.client.host)


async def authorize(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> Client:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing x-api-key header")

    store: AuthStore = request.app.state.auth_store
    client = store.get(x_api_key)
    if client is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")

    ip = _client_ip(request)
    if not client.ip_allowed(ip):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"source ip {ip} not allowed for this api key",
        )

    return client
