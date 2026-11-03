import asyncio
import json

import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from opn_mcp import server
from opn_mcp.client import Config, OPNClient

BASE = "https://opn.test/api"
BACKUPS = {"items": [{"id": "config-100.xml", "time": "100"}, {"id": "config-90.xml", "time": "90"}]}


@pytest.fixture
def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/core/firmware/info").respond(json={"plugin": [{"name": "os-nginx", "installed": "1"}]})
        mock.get("/core/backup/backups/this").respond(json=BACKUPS)
        yield mock


def make(writes: bool) -> OPNClient:
    c = OPNClient(Config(url=BASE, key="k", secret="s", allow_writes=writes))
    server.set_client(c)
    return c


@pytest.fixture(autouse=True)
def _reset():
    yield
    server.set_client(None)


async def test_writes_disabled_by_default(api):
    make(False)
    with pytest.raises(ToolError, match="Writes are disabled"):
        await server.opn_items("firewall/alias", "item", "add", data={"name": "x", "type": "host"})


async def test_unknown_field_rejected_before_sending(api):
    make(True)
    with pytest.raises(ToolError, match="dstport: unknown field"):
        await server.opn_items("firewall/d_nat", "rule", "add", data={"dstport": "80"})
    assert not [c for c in api.calls if c.request.method == "POST"]


async def test_delete_needs_confirm(api):
    make(True)
    with pytest.raises(ToolError, match="confirm=true"):
        await server.opn_items("firewall/alias", "item", "del", uuid="u1")


async def test_validation_failure_in_200_response_is_an_error(api):
    make(True)
    api.post("/firewall/alias/add_item").respond(
        json={"result": "failed", "validations": {"alias.name": "already exists"}})
    with pytest.raises(ToolError, match=r"alias\.name: already exists"):
        await server.opn_items("firewall/alias", "item", "add", data={"name": "x", "type": "host"})


async def test_add_then_apply_arms_auto_revert_and_confirm_cancels(api):
    make(True)
    add = api.post("/firewall/alias/add_item").respond(json={"result": "saved", "uuid": "u1"})
    api.post("/firewall/alias/reconfigure").respond(json={"status": "ok"})
    out = await server.opn_items("firewall/alias", "item", "add",
                                 data={"name": "lab_hosts", "type": "host", "content": "10.0.0.1"})
    assert json.loads(add.calls[0].request.content) == {"alias": {"name": "lab_hosts", "type": "host",
                                                                  "content": "10.0.0.1"}}
    assert out["applied"]["endpoint"] == "firewall/alias/reconfigure"
    assert out["auto_revert"]["checkpoint"] == "config-100.xml"
    assert (await server.opn_firewall_confirm())["status"] == "confirmed"
    assert server.rollback.task is None


async def test_unconfirmed_change_is_reverted(api, monkeypatch):
    make(True)
    api.post("/firewall/filter/toggle_rule/u1").respond(json={"result": "Disabled", "changed": True})
    api.post("/firewall/filter/apply").respond(json={"status": "OK"})
    revert = api.post("/core/backup/revert_backup/config-100.xml").respond(json={"status": "reverted"})
    api.post("/firewall/alias/reconfigure").respond(json={"status": "ok"})
    real_sleep = asyncio.sleep
    monkeypatch.setattr(server.asyncio, "sleep", lambda s: real_sleep(0))
    await server.opn_items("firewall/filter", "rule", "toggle", uuid="u1")
    await real_sleep(0.05)
    assert revert.called and server.rollback.checkpoint is None


async def test_plugin_endpoint_requires_installed_plugin(api):
    make(False)
    with pytest.raises(ToolError, match="os-haproxy, which is not installed"):
        await server.opn_api("haproxy/settings/get")


async def test_search_posts_paging_body(api):
    make(False)
    route = api.post("/firewall/alias/search_item").respond(json={"rows": [], "total": 0})
    await server.opn_items("firewall/alias", "item", search="lab")
    assert json.loads(route.calls[0].request.content) == {"current": 1, "rowCount": 100, "searchPhrase": "lab"}


async def test_neighbors_wraps_bare_list(api):
    make(False)
    api.get("/diagnostics/interface/get_arp").respond(json=[{"ip": "10.0.0.1", "mac": "aa"}, {"ip": "10.0.0.2"}])
    out = await server.opn_neighbors(search="10.0.0.2")
    assert out == {"rows": [{"ip": "10.0.0.2"}], "total": 1}


async def test_plugin_404_explained_when_plugin_list_unavailable(api):
    make(False)
    api.get("/core/firmware/info").respond(500)
    api.get("/haproxy/settings/get").respond(404, json={"message": "Endpoint not found"})
    with pytest.raises(ToolError, match="os-haproxy is probably not installed"):
        await server.opn_api("haproxy/settings/get")
