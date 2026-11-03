"""Against the disposable lab OPNsense (VM 950) only. Never point this at production.

Run: set -a; . ../lab/.env; set +a; OPN_LAB=1 uv run pytest -m lab -v
"""

import os
import uuid

import pytest

from opn_mcp import server
from opn_mcp.client import Config, OPNClient

pytestmark = [
    pytest.mark.lab,
    pytest.mark.skipif(os.environ.get("OPN_LAB") != "1", reason="set OPN_LAB=1 with lab credentials"),
]


@pytest.fixture
async def lab():
    c = OPNClient(Config(url=os.environ["OPN_URL"], key=os.environ["OPN_API_KEY"],
                         secret=os.environ["OPN_API_SECRET"], allow_writes=True))
    server.set_client(c)
    yield c
    server.set_client(None)
    await c.aclose()


async def _alias_names() -> set[str]:
    rows = (await server.opn_items("firewall/alias", "item", search="mcp_"))["rows"]
    return {r["name"] for r in rows}


async def test_overview(lab):
    out = await server.opn_overview()
    assert out["version"].startswith("26.7")
    assert any(i["identifier"] == "wan" for i in out["interfaces"])
    assert any(s["name"] == "configd" for s in out["services"])


async def test_alias_crud_with_confirm(lab):
    name = f"mcp_{uuid.uuid4().hex[:6]}"
    out = await server.opn_items("firewall/alias", "item", "add",
                                 data={"name": name, "type": "host", "content": "192.0.2.1"})
    alias_id = out["result"]["uuid"]
    try:
        assert out["auto_revert"]["seconds_left"] > 0
        assert (await server.opn_firewall_confirm())["status"] == "confirmed"
        await server.opn_items("firewall/alias", "item", "set", uuid=alias_id,
                               data={"content": "192.0.2.1\n192.0.2.2", "description": "opn-mcp lab test"})
        await server.opn_firewall_confirm()
        got = await server.opn_items("firewall/alias", "item", "get", uuid=alias_id)
        assert got["alias"]["description"] == "opn-mcp lab test"
    finally:
        await server.opn_items("firewall/alias", "item", "del", uuid=alias_id, confirm=True)
        await server.opn_firewall_confirm()
    assert name not in await _alias_names()


async def test_unconfirmed_change_can_be_reverted(lab):
    nat_before = (await server.opn_items("firewall/d_nat", "rule"))["total"]
    name = f"mcp_{uuid.uuid4().hex[:6]}"
    await server.opn_items("firewall/alias", "item", "add", data={"name": name, "type": "host", "content": "192.0.2.9"})
    assert name in await _alias_names()
    out = await server.opn_firewall_revert_now()
    assert out["result"]["status"] == "reverted"
    assert name not in await _alias_names()
    assert (await server.opn_items("firewall/d_nat", "rule"))["total"] == nat_before  # older changes kept


async def test_dnat_rule_round_trip(lab):
    out = await server.opn_items("firewall/d_nat", "rule", "add", data={
        "interface": "wan", "protocol": "tcp", "destination": {"network": "wanip", "port": "8443"},
        "target": "192.168.1.10", "local-port": "8006", "descr": "opn-mcp lab test", "disabled": "1"})
    rule_id = out["result"]["uuid"]
    try:
        await server.opn_firewall_confirm()
        rule = (await server.opn_items("firewall/d_nat", "rule", "get", uuid=rule_id))["rule"]
        assert rule["target"] == "192.168.1.10" and rule["destination"]["port"] == "8443"
    finally:
        await server.opn_items("firewall/d_nat", "rule", "del", uuid=rule_id, confirm=True)
        await server.opn_firewall_confirm()


async def test_settings_and_services(lab):
    unbound = await server.opn_settings("unbound/settings")
    assert "unbound" in unbound
    services = await server.opn_service()
    assert any(s["name"] == "unbound" for s in services)
    status = await server.opn_api("unbound/service/status")
    assert status["status"] in ("running", "stopped", "disabled")


async def test_reads(lab):
    assert (await server.opn_neighbors())["total"] >= 1
    assert isinstance(await server.opn_firewall_log(limit=5), list)
    history = await server.opn_config_history(limit=3)
    assert history and history[0]["id"].startswith("config-")


async def test_ping_cleans_up_its_job(lab):
    out = await server.opn_ping("192.168.1.10", count=2)
    assert out["received"] >= 1
    jobs = await lab.post("diagnostics/ping/search_jobs", {"current": 1, "rowCount": -1})
    assert jobs["total"] == 0
