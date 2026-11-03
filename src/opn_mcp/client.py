"""Async HTTP client for the OPNsense API (key/secret basic auth, JSON bodies)."""

import os
from dataclasses import dataclass
from typing import Any

import httpx


class OPNError(Exception):
    def __init__(self, message: str, status: int | None = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


@dataclass
class Config:
    url: str  # https://host/api
    key: str
    secret: str
    verify: bool | str = False
    allow_writes: bool = False
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> "Config":
        missing = [n for n in ("OPNSENSE_URL", "OPNSENSE_API_KEY", "OPNSENSE_API_SECRET") if not os.environ.get(n)]
        if missing:
            raise OPNError(f"Missing environment: {', '.join(missing)}")
        verify_env = os.environ.get("OPNSENSE_VERIFY_SSL", "false").strip()
        verify: bool | str
        if verify_env.lower() in ("1", "true", "yes", "on"):
            verify = True
        elif verify_env.lower() in ("", "0", "false", "no", "off"):
            verify = False
        else:
            verify = verify_env
        url = os.environ["OPNSENSE_URL"].rstrip("/")
        if not url.endswith("/api"):
            url += "/api"
        return cls(url=url, key=os.environ["OPNSENSE_API_KEY"], secret=os.environ["OPNSENSE_API_SECRET"],
                   verify=verify,
                   allow_writes=os.environ.get("OPNSENSE_ALLOW_WRITES", "false").lower() in ("1", "true", "yes"),
                   timeout=float(os.environ.get("OPNSENSE_TIMEOUT", "60")))


def failure(data: Any) -> str | None:
    """OPNsense reports many failures with HTTP 200 and a status/result field."""
    if not isinstance(data, dict):
        return None
    if data.get("result") == "failed" or data.get("status") in ("failed", "failure", "error"):
        if data.get("validations"):
            return "validation failed: " + "; ".join(f"{k}: {v}" for k, v in data["validations"].items())
        return str(data.get("message") or data.get("status_msg") or data)
    return None


class OPNClient:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self._http = httpx.AsyncClient(base_url=config.url, auth=(config.key, config.secret),
                                       verify=config.verify, timeout=config.timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, body: dict[str, Any] | None = None,
                      params: dict[str, Any] | None = None, raw: bool = False) -> Any:
        method = method.upper()
        kwargs: dict[str, Any] = {"params": {k: v for k, v in (params or {}).items() if v is not None}}
        if method == "POST":
            kwargs["json"] = body or {}
        try:
            resp = await self._http.request(method, "/" + path.strip("/"), **kwargs)
        except httpx.HTTPError as e:
            raise OPNError(f"{method} {path}: {type(e).__name__}: {e}") from e
        ctype = resp.headers.get("content-type", "")
        data: Any = resp.json() if "json" in ctype and resp.content else resp.text
        if resp.status_code >= 400:
            msg = data.get("message") if isinstance(data, dict) else str(data)[:300]
            raise OPNError(f"{method} {path}: HTTP {resp.status_code} {msg or resp.reason_phrase}".strip(),
                           status=resp.status_code, detail=data)
        if raw:
            return data
        reason = failure(data)
        if reason:
            raise OPNError(f"{method} {path}: {reason}", detail=data)
        return data

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, body)
