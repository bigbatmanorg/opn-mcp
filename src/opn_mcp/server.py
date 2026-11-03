"""MCP server for the OPNsense API.

- opn_api_search / opn_api_describe / opn_api reach every endpoint of core and the plugins (index generated from the
  OPNsense sources for the bundled version). Bodies are checked against the model XML, so misspelled fields fail
  instead of being silently dropped.
- opn_items / opn_settings do model CRUD for any controller (aliases, rules, NAT, DHCP, DNS, VPN, plugins).
- Firewall changes are checkpointed in the config history. Applying them arms an auto-revert that only
  opn_firewall_confirm cancels.

Writes need OPNSENSE_ALLOW_WRITES=true; destructive actions (delete, stop, reboot, firmware update, revert) also
need confirm=true on the call.
"""

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from . import index
from .client import Config, OPNClient, OPNError

log = logging.getLogger(__name__)
WRITE_ACTION = re.compile(r"^(add|set|del|toggle|upload|apply|reconfigure|start|stop|restart|import|move|revert|"
                          r"flush|kill|reboot|halt|update|upgrade)")
FIREWALL_CONTROLLERS = re.compile(r"^firewall/(filter|d_nat|one_to_one|source_nat|npt|alias|group|category)\b")

_client: OPNClient | None = None
_plugins: set[str] | None = None


class _Rollback:
    """Auto-revert state for firewall changes (lives in this server process)."""

    checkpoint: str | None = None
    deadline: float | None = None
    task: asyncio.Task | None = None


rollback = _Rollback()


def client() -> OPNClient:
    global _client
    if _client is None:
        try:
            _client = OPNClient(Config.from_env())
        except OPNError as e:
            raise ToolError(str(e)) from e
    return _client


def set_client(c: OPNClient | None) -> None:
    """Inject a client (tests)."""
    global _client, _plugins
    _client, _plugins = c, None
    if rollback.task:
        rollback.task.cancel()
    rollback.checkpoint = rollback.deadline = rollback.task = None


@asynccontextmanager
async def lifespan(_server: FastMCP):
    try:
        yield
    finally:
        if _client is not None:
            await _client.aclose()


mcp = FastMCP("opnsense", instructions=__doc__, lifespan=lifespan)


async def installed_plugins() -> set[str] | None:
    """Installed plugin packages, or None when the firewall would not say. (core/firmware/info is large; some
    builds cut HTTP/1.1 responses at 64 KiB, so this can fail.)"""
    global _plugins
    if _plugins is None:
        try:
            info = await client().get("core/firmware/info")
            _plugins = {p["name"] for p in info.get("plugin", []) if str(p.get("installed")) == "1"}
        except OPNError as e:
            log.warning("could not list installed plugins: %s", e)
            return None
    return _plugins


def _is_write(ep: index.Endpoint) -> bool:
    return index.is_write(ep) or bool(WRITE_ACTION.match(ep.action))


async def _checkpoint() -> str | None:
    """Newest config-history entry, i.e. the config as it is before our next change."""
    items = (await client().get("core/backup/backups/this")).get("items", [])
    return items[0]["id"] if items else None


async def _call(path: str, method: str | None = None, body: dict[str, Any] | None = None,
                params: dict[str, Any] | None = None, *, confirm: bool = False, dry_run: bool = False,
                strict: bool = True) -> Any:
    try:
        ep, args = index.resolve(path)
        index.check_args(ep, args)
        method = (method or ep.method).upper()
        if method == "POST" and body and strict:
            index.validate_body(ep, body)
    except index.OPNIndexError as e:
        raise ToolError(str(e)) from e
    write = _is_write(ep)  # search_* endpoints take a POST body but only read
    full = "/".join([ep.path, *args])
    if dry_run:
        return {"dry_run": True, "method": method, "path": full, "body": body, "params": params,
                "write": write, "destructive": index.is_destructive(ep), "origin": ep.origin}
    if write:
        if not client().config.allow_writes:
            raise ToolError("Writes are disabled. Set OPNSENSE_ALLOW_WRITES=true for this server to allow them.")
        if index.is_destructive(ep) and not confirm:
            raise ToolError(f"{full} is destructive. Repeat the call with confirm=true to proceed.")
        if FIREWALL_CONTROLLERS.match(ep.path) and rollback.checkpoint is None:
            rollback.checkpoint = await _checkpoint()
    if ep.origin != "core":
        plugins = await installed_plugins()
        if plugins is not None and ep.origin not in plugins:
            raise ToolError(f"{full} belongs to plugin {ep.origin}, which is not installed.")
    try:
        return await client().request(method, full, body if method == "POST" else None, params)
    except OPNError as e:
        if e.status == 404 and ep.origin != "core":
            raise ToolError(f"{full}: not found; plugin {ep.origin} is probably not installed.") from e
        raise ToolError(str(e)) from e


# --- generic API access -------------------------------------------------------------------------


@mcp.tool()
async def opn_api_search(query: str, installed_only: bool = True, limit: int = 30) -> dict[str, Any]:
    """Search OPNsense API endpoints by words in the path, description or model
    (e.g. "alias", "kea reservation", "unbound host override", "wireguard peer")."""
    origins = None
    note = None
    if installed_only:
        plugins = await installed_plugins()
        if plugins is None:
            note = "Could not list installed plugins; showing endpoints of all plugins."
        else:
            origins = {"core", *plugins}
    out: dict[str, Any] = {"index_version": index.version(), "results": index.search(query, origins, limit)}
    if note:
        out["note"] = note
    return out


@mcp.tool()
async def opn_api_describe(path: str) -> dict[str, Any]:
    """Method, path args, body fields (types, required, options) and search params of an endpoint,
    e.g. "firewall/alias/add_item"."""
    try:
        return index.describe(index.get(path))
    except index.OPNIndexError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
async def opn_api(path: str, method: Literal["GET", "POST"] | None = None, body: dict[str, Any] | None = None,
                  params: dict[str, Any] | None = None, confirm: bool = False, dry_run: bool = False,
                  strict: bool = True) -> Any:
    """Call any endpoint: path is module/controller/action plus positional args, e.g. "core/service/restart/unbound"
    or "firewall/alias/set_item/<uuid>". The method defaults to what the endpoint expects. POST bodies are checked
    against the model unless strict=false. dry_run=true shows the request without sending it.
    Changes saved this way are not active until the controller's apply/reconfigure runs (see opn_items)."""
    return await _call(path, method, body, params, confirm=confirm, dry_run=dry_run, strict=strict)


# --- model CRUD -----------------------------------------------------------------------------------


def _item_action(controller: str, verb: str, item: str) -> str:
    actions = index.controller_actions(controller)
    for name in (f"{verb}_{item}", f"{verb}{item.replace('_', '')}", f"{verb}_{item}s"):
        if name in actions:
            return f"{index.normalize(controller)}/{name}"
    avail = sorted(a for a in actions if a.startswith(verb))
    raise ToolError(f"{controller} has no {verb} action for {item!r}. {verb}* actions: {', '.join(avail) or 'none'}")


async def _apply(controller: str) -> dict[str, Any] | None:
    ep = index.apply_endpoint(controller)
    if ep is None:
        return None
    return {"endpoint": ep.path, "result": await _call(ep.path, "POST")}


@mcp.tool()
async def opn_items(controller: str, item: str,
                    action: Literal["search", "get", "add", "set", "del", "toggle"] = "search",
                    uuid: str | None = None, data: dict[str, Any] | None = None, search: str | None = None,
                    rows: int = 100, enabled: bool | None = None, apply: bool = True, confirm: bool = False,
                    dry_run: bool = False) -> Any:
    """Work with the items of any OPNsense model controller.
    controller + item name the endpoints: ("firewall/alias", "item") -> firewall/alias/search_item, add_item, ...;
    ("firewall/filter", "rule"), ("firewall/d_nat", "rule"), ("unbound/settings", "host_override"),
    ("dnsmasq/settings", "host"), ("kea/dhcpv4", "reservation"), ("nginx/settings", "upstream").
    data holds just the item's fields (see opn_api_describe on the add_ endpoint).
    apply=true runs the controller's apply/reconfigure afterwards; for firewall rules that arms the auto-revert
    (confirm with opn_firewall_confirm). del needs confirm=true."""
    path = _item_action(controller, action, item)
    if action == "search":
        return await _call(path, "POST", {"current": 1, "rowCount": rows, "searchPhrase": search or ""},
                           strict=False)
    if action in ("get", "set", "del", "toggle") and not uuid:
        raise ToolError(f"uuid is required for {action}")
    if action == "get":
        return await _call(f"{path}/{uuid}", "GET")
    if action == "toggle":
        suffix = f"/{uuid}" + ("" if enabled is None else f"/{int(enabled)}")
        result = await _call(path + suffix, "POST", dry_run=dry_run)
    elif action == "del":
        result = await _call(f"{path}/{uuid}", "POST", confirm=confirm, dry_run=dry_run)
    else:
        if not data:
            raise ToolError(f"data is required for {action}")
        key = index.get(path).spec.get("body_key")
        if not key:
            raise ToolError(f"{path}: unknown body key; use opn_api with an explicit body")
        result = await _call(path + (f"/{uuid}" if action == "set" else ""), "POST", {key: data}, dry_run=dry_run)
    if dry_run:
        return result
    out: dict[str, Any] = {"result": result}
    if apply:
        out["applied"] = await _apply(controller)
        out.update(await _arm_rollback(controller))
    return out


@mcp.tool()
async def opn_settings(controller: str, data: dict[str, Any] | None = None, apply: bool = True,
                       dry_run: bool = False) -> Any:
    """Read a controller's whole settings model (controller/get), or change part of it: data is merged into the
    model and saved with controller/set, e.g. opn_settings("unbound/settings", {"general": {"port": "53"}}).
    apply=true then runs the service reconfigure."""
    controller = index.normalize(controller)
    current = await _call(f"{controller}/get", "GET")
    if not data:
        return current
    key = index.get(f"{controller}/set").spec.get("body_key") or next(iter(current), None)
    if not key:
        raise ToolError(f"Cannot tell the body key for {controller}/set")
    result = await _call(f"{controller}/set", "POST", {key: data}, dry_run=dry_run)
    if dry_run:
        return result
    out: dict[str, Any] = {"result": result}
    if apply:
        out["applied"] = await _apply(controller)
        out.update(await _arm_rollback(controller))
    return out


# --- firewall safety net --------------------------------------------------------------------------


async def _revert(checkpoint: str) -> dict[str, Any]:
    result = await client().post(f"core/backup/revert_backup/{checkpoint}")
    await client().post("firewall/alias/reconfigure")
    await client().post("firewall/filter/apply")
    return {"reverted_to": checkpoint, "result": result}


async def _arm_rollback(controller: str, seconds: int = 60) -> dict[str, Any]:
    if not FIREWALL_CONTROLLERS.match(index.normalize(controller)) or rollback.checkpoint is None:
        return {}
    if rollback.task is None:
        checkpoint = rollback.checkpoint

        async def revert_later() -> None:
            await asyncio.sleep(seconds)
            try:
                log.warning("firewall change not confirmed; reverting to %s", checkpoint)
                await _revert(checkpoint)
            finally:
                rollback.checkpoint = rollback.deadline = rollback.task = None

        rollback.deadline = time.time() + seconds
        rollback.task = asyncio.create_task(revert_later())
    return {"auto_revert": {"checkpoint": rollback.checkpoint,
                            "seconds_left": round(rollback.deadline - time.time()),
                            "note": "Call opn_firewall_confirm once you have checked access still works."}}


@mcp.tool()
async def opn_firewall_confirm() -> dict[str, Any]:
    """Keep the applied firewall changes and cancel the pending auto-revert."""
    if rollback.task is None:
        rollback.checkpoint = None
        return {"status": "nothing pending"}
    rollback.task.cancel()
    checkpoint = rollback.checkpoint
    rollback.checkpoint = rollback.deadline = rollback.task = None
    return {"status": "confirmed", "checkpoint_was": checkpoint}


@mcp.tool()
async def opn_firewall_revert_now() -> dict[str, Any]:
    """Revert the pending firewall changes immediately instead of waiting for the timer."""
    if rollback.checkpoint is None:
        raise ToolError("No checkpoint pending.")
    if rollback.task:
        rollback.task.cancel()
    checkpoint = rollback.checkpoint
    rollback.checkpoint = rollback.deadline = rollback.task = None
    try:
        return await _revert(checkpoint)
    except OPNError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
async def opn_config_history(limit: int = 20, diff_with: str | None = None) -> Any:
    """Config history (newest first: id, time, description, user). With diff_with=<id>, the diff between that
    backup and the newest one."""
    items = (await _call("core/backup/backups/this", "GET")).get("items", [])
    if diff_with:
        if not items:
            raise ToolError("No config history")
        return await _call(f"core/backup/diff/this/{diff_with}/{items[0]['id']}", "GET")
    return items[:limit]


@mcp.tool()
async def opn_config_revert(backup: str, confirm: bool = False, reload_firewall: bool = True) -> dict[str, Any]:
    """Restore config.xml from a history entry (id from opn_config_history). Services other than the firewall keep
    running with their old config until reconfigured. Needs confirm=true."""
    await _call(f"core/backup/revert_backup/{backup}", "POST", confirm=confirm)
    out: dict[str, Any] = {"reverted_to": backup}
    if reload_firewall:
        out["aliases"] = await _call("firewall/alias/reconfigure", "POST")
        out["filter"] = await _call("firewall/filter/apply", "POST")
    return out


# --- curated reads --------------------------------------------------------------------------------


def _rows(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return {"rows": data, "total": len(data)}
    return data


@mcp.tool()
async def opn_overview() -> dict[str, Any]:
    """Version, update status, interfaces, gateways and services in one call, plus whether writes are enabled."""
    async def safe(path: str, method: str = "GET", body: dict | None = None) -> Any:
        try:
            return await client().request(method, path, body)
        except OPNError as e:
            return {"error": str(e)}

    firmware, ifaces, gateways, services = await asyncio.gather(
        safe("core/firmware/status"), safe("interfaces/overview/interfaces_info"), safe("routes/gateway/status"),
        safe("core/service/search", "POST", {"current": 1, "rowCount": -1}))
    product = firmware.get("product", {}) if isinstance(firmware, dict) else {}
    return {
        "version": product.get("product_version"),
        "updates": {k: firmware.get(k) for k in ("status", "status_msg", "updates", "upgrade_major_version")
                    if isinstance(firmware, dict) and k in firmware},
        "index_version": index.version(),
        "writes_enabled": client().config.allow_writes,
        "interfaces": [{k: i.get(k) for k in ("identifier", "description", "device", "status", "addr4", "addr6",
                                              "macaddr", "enabled")}
                       for i in (ifaces.get("rows", []) if isinstance(ifaces, dict) else [])],
        "gateways": gateways.get("items", gateways) if isinstance(gateways, dict) else gateways,
        "services": [{k: s.get(k) for k in ("name", "id", "description", "running", "locked")}
                     for s in (services.get("rows", []) if isinstance(services, dict) else [])],
    }


@mcp.tool()
async def opn_service(name: str | None = None, action: Literal["status", "start", "stop", "restart"] = "status",
                      service_id: str | None = None, confirm: bool = False) -> Any:
    """List services (no name), or start/stop/restart one by name (e.g. "unbound", "dhcpd", "openvpn" with
    service_id for multi-instance services). stop needs confirm=true."""
    services = (await _call("core/service/search", "POST", {"current": 1, "rowCount": -1}, strict=False))["rows"]
    if not name:
        return services
    matches = [s for s in services if s["name"] == name and (service_id is None or str(s.get("id")) == service_id)]
    if not matches:
        raise ToolError(f"No service {name!r}. Known: {', '.join(sorted({s['name'] for s in services}))}")
    if action == "status":
        return matches
    svc = matches[0]
    suffix = f"/{svc['name']}" + (f"/{svc['id']}" if svc.get("id") else "")
    return await _call(f"core/service/{action}{suffix}", "POST", confirm=confirm)


@mcp.tool()
async def opn_neighbors(family: Literal["ipv4", "ipv6"] = "ipv4", search: str | None = None) -> dict[str, Any]:
    """ARP (ipv4) or NDP (ipv6) table, optionally filtered by IP/MAC/hostname substring."""
    data = _rows(await _call("diagnostics/interface/get_arp" if family == "ipv4" else
                             "diagnostics/interface/get_ndp", "GET"))
    if search:
        s = search.lower()
        data["rows"] = [r for r in data["rows"] if s in " ".join(map(str, r.values())).lower()]
        data["total"] = len(data["rows"])
    return data


@mcp.tool()
async def opn_firewall_log(limit: int = 100, search: str | None = None) -> list[dict[str, Any]]:
    """Recent firewall log entries (newest first), optionally filtered by a substring (IP, port, interface)."""
    rows = await _call("diagnostics/firewall/log", "GET", params={"limit": limit if not search else 1000})
    if search:
        s = search.lower()
        rows = [r for r in rows if s in " ".join(map(str, r.values())).lower()]
    return rows[:limit]


@mcp.tool()
async def opn_ping(host: str, count: int = 3, timeout: float = 30) -> dict[str, Any]:
    """Ping a host from the firewall and return loss and round-trip times. Works in read-only mode: it only runs
    a temporary diagnostics job, which is removed afterwards."""
    c = client()
    try:
        job = (await c.post("diagnostics/ping/set",
                            {"ping": {"settings": {"hostname": host, "count": str(count)}}}))["uuid"]
        await c.post(f"diagnostics/ping/start/{job}")
        row: dict[str, Any] = {}
        try:
            # The job keeps pinging until stopped, whatever count says.
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(1)
                jobs = await c.post("diagnostics/ping/search_jobs", {"current": 1, "rowCount": -1})
                row = next((r for r in jobs.get("rows", []) if r.get("id") == job), {})
                if int(row.get("send") or 0) >= count or row.get("last_error"):
                    break
        finally:
            await c.post(f"diagnostics/ping/stop/{job}")
            await c.post(f"diagnostics/ping/remove/{job}")
    except OPNError as e:
        raise ToolError(str(e)) from e
    return {k: row.get(k) for k in ("hostname", "send", "received", "loss", "min", "avg", "max", "last_error")}


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
