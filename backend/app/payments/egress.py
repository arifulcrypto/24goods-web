"""Outbound route choice for server-to-server payment-gateway calls: direct first, relay fallback.

Why: production's own route to some gateways can be lossy (2026-11-09: Japan -> Xiaodou in
mainland China). A configured relay (``APP_ZPAY_RELAYS="pay.example.cn=203.0.113.5:20443"``, a plain
TCP forwarder) is used only when the direct connection cannot even be opened. TLS stays end to end:
the relay sees ciphertext; SNI and certificate checks are for the gateway host on both paths.

Safety: a path is abandoned only on a connect-level failure (the request provably never left this
host), so falling back can never send the same create/query twice. Anything after the connection
is established (read timeout, protocol error, HTTP status) is returned to the caller unchanged.

State (per process, per gateway host):
* sticky route (owner, 2026-11-09): the path that worked last goes first; the other one is tried
  only when it fails. Direct is the default until a failure moves the preference to the relay,
  and only a relay failure moves it back. Fewer failed first attempts than any timer.
* the last address that worked is reused; when every path fails, DNS is consulted again and
  direct is retried once if the address changed.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

log = logging.getLogger("app.payments.egress")

FIRST_PATH_CONNECT_S = 1.0  # when another path remains, do not wait long on this one

T = TypeVar("T")


@dataclass
class _HostState:
    ip: str | None = None
    preferred: str = "direct"


_STATE: dict[str, _HostState] = {}
_RELAYS: tuple[str, dict[str, tuple[str, int]]] = ("", {})


def parse_relays(raw: str) -> dict[str, tuple[str, int]]:
    """``host=ip:port[,host=ip:port]`` -> {host: (ip, port)}; malformed entries are ignored."""
    out: dict[str, tuple[str, int]] = {}
    for item in (raw or "").split(","):
        host, _, dest = item.strip().partition("=")
        ip, _, port = dest.strip().rpartition(":")
        try:
            out[host.strip().lower()] = (str(ipaddress.ip_address(ip)), int(port))
        except ValueError:
            if item.strip():
                log.warning("ignoring malformed relay entry")
    return out


def relays() -> dict[str, tuple[str, int]]:
    global _RELAYS
    from app.core.config import get_settings

    raw = get_settings().zpay_relays
    if raw != _RELAYS[0]:
        _RELAYS = (raw, parse_relays(raw))
    return _RELAYS[1]


def _url(scheme: str, ip: str, port: int, path: str) -> str:
    addr = ipaddress.ip_address(ip)
    h = f"[{addr}]" if addr.version == 6 else str(addr)
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{h}{'' if port == default else f':{port}'}{path}"


def route_order(host: str, has_relay: bool) -> list[str]:
    if not has_relay:
        return ["direct"]
    st = _STATE.get(host)
    first = st.preferred if st else "direct"
    return [first, "relay" if first == "direct" else "direct"]


async def send(
    *,
    scheme: str,
    host: str,
    port: int,
    path: str,
    fresh_ip: Callable[[], Awaitable[str]],
    exchange: Callable[[str, float | None], Awaitable[T]],
    never_dispatched: Callable[[BaseException], bool],
) -> T:
    """Run ``exchange(url, connect_timeout)`` over the best path.

    ``fresh_ip`` resolves and vets the gateway host (only called when no remembered address works);
    ``exchange`` performs exactly one HTTP request to ``url`` (Host/SNI = ``host`` are the caller's
    job); ``never_dispatched`` says whether an exception happened before any byte was sent.
    """
    host_key = host.lower()
    relay = relays().get(host_key)
    st = _STATE.setdefault(host_key, _HostState())
    remembered = st.ip
    ip = remembered or await fresh_ip()
    order = route_order(host_key, relay is not None)
    last: BaseException | None = None

    async def one(route: str, addr: str, connect: float | None) -> tuple[bool, T | None]:
        nonlocal last
        dest = (addr, port) if route == "direct" else relay
        assert dest is not None
        try:
            return True, await exchange(_url(scheme, dest[0], dest[1], path), connect)
        except BaseException as exc:
            if not never_dispatched(exc):
                raise  # may have reached the gateway: never retry
            last = exc
            log.info("gateway %s: %s path not reachable", host_key, route)
            return False, None

    # one try per path; with two paths the first gets FIRST_PATH_CONNECT_S, the second 1.5 s
    budgets = [FIRST_PATH_CONNECT_S, 1.5] if len(order) == 2 else [None]
    for route, connect in zip(order, budgets, strict=True):
        ok, result = await one(route, ip, connect)
        if ok:
            st.preferred = route  # stick with what worked
            st.ip = ip
            return result  # type: ignore[return-value]
    # every path failed with a remembered address: look the domain up again and retry direct once
    # (a moved load balancer); the relay dials the gateway by name itself, so it needs no retry
    st.ip = None
    if remembered is not None:
        fresh = await fresh_ip()
        if fresh != remembered:
            ok, result = await one("direct", fresh, FIRST_PATH_CONNECT_S if relay else None)
            if ok:
                st.preferred = "direct"
                st.ip = fresh
                return result  # type: ignore[return-value]
    assert last is not None
    raise last


def reset_state() -> None:
    """Tests only."""
    _STATE.clear()
