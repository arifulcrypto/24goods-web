"""Minimal Cloudflare for SaaS (Custom Hostnames) client.

Only talks to ``api.cloudflare.com/client/v4/zones/{zone}/custom_hostnames``. The API token is a
tenant secret and is only ever placed in the Authorization header: never logged, never in an
error message or a response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

API = "https://api.cloudflare.com/client/v4"
TIMEOUT_S = 10.0
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class CFError(Exception):
    """Cloudflare call failed. The message is safe to show (no token, no raw body)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # HTTP status when Cloudflare answered, else None


@dataclass(frozen=True)
class CustomHostname:
    cf_id: str
    status: str  # pending | active | moved | deleted | blocked ...
    ssl_status: str | None
    verification: dict[str, Any] = field(default_factory=dict)

    @property
    def app_status(self) -> str:
        """Map to reseller_domains.status."""
        if self.status == "active" and self.ssl_status == "active":
            return "active"
        if self.status in ("blocked", "deleted") or self.ssl_status in (
            "validation_timed_out",
            "deactivated",
            "deleted",
        ):
            return "failed"
        return "pending"


def parse(result: dict[str, Any]) -> CustomHostname:
    ssl = result.get("ssl") or {}
    ov = result.get("ownership_verification") or {}
    ovh = result.get("ownership_verification_http") or {}
    records = [
        {
            k: str(r[k])
            for k in ("txt_name", "txt_value", "http_url", "http_body", "cname", "cname_target")
            if r.get(k)
        }
        for r in (ssl.get("validation_records") or [])
        if isinstance(r, dict)
    ]
    verification = {
        "ownership": {k: str(ov[k]) for k in ("type", "name", "value") if ov.get(k)},
        "ownership_http": {k: str(ovh[k]) for k in ("http_url", "http_body") if ovh.get(k)},
        "ssl_records": [r for r in records if r][:10],
    }
    return CustomHostname(
        cf_id=str(result.get("id") or ""),
        status=str(result.get("status") or "pending"),
        ssl_status=str(ssl["status"]) if ssl.get("status") else None,
        verification=verification,
    )


class CFSaaS:
    def __init__(self, http: httpx.AsyncClient, zone_id: str, token: str) -> None:
        self._http, self._zone, self._token = http, zone_id, token

    async def _call(self, method: str, path: str = "", json: Any = None) -> dict[str, Any]:
        if not _ID.match(self._zone) or (path and not _ID.match(path[1:])):
            raise CFError("invalid Cloudflare id")
        url = f"{API}/zones/{self._zone}/custom_hostnames{path}"
        try:
            r = await self._http.request(
                method,
                url,
                json=json,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            raise CFError(f"Cloudflare unreachable ({type(exc).__name__})") from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or not data.get("success", r.status_code < 400):
            msg = ""
            errs = data.get("errors") if isinstance(data, dict) else None
            if errs and isinstance(errs[0], dict):
                msg = str(errs[0].get("message", ""))[:200]
            raise CFError(f"Cloudflare error {r.status_code}: {msg}".strip(), r.status_code)
        res = data.get("result")
        return res if isinstance(res, dict) else {}

    async def create(self, hostname: str, method: str = "http") -> CustomHostname:
        if method not in ("http", "txt"):
            raise ValueError("method")
        body = {"hostname": hostname, "ssl": {"method": method, "type": "dv"}}
        return parse(await self._call("POST", json=body))

    async def get(self, cf_id: str) -> CustomHostname:
        return parse(await self._call("GET", f"/{cf_id}"))

    async def delete(self, cf_id: str) -> None:
        await self._call("DELETE", f"/{cf_id}")
