import pytest

from opn_mcp import index


def test_index_loads_with_core_and_plugins():
    eps = index.load()["endpoints"]
    assert index.version() == "26.7.4"
    assert len(eps) > 2000
    assert {e["origin"] for e in eps.values()} >= {"core", "os-nginx"}


def test_resolve_splits_args_and_accepts_camel_case():
    ep, args = index.resolve("/api/firewall/alias/setItem/abc-123")
    assert ep.path == "firewall/alias/set_item" and args == ["abc-123"]


def test_resolve_unknown_lists_controller_actions():
    with pytest.raises(index.OPNIndexError, match="search_item"):
        index.resolve("firewall/alias/nope")


def test_removed_savepoint_is_not_indexed():
    assert not index.exists("firewall/filter/savepoint")


def test_methods_inferred_from_source():
    assert index.get("firewall/filter/apply").method == "POST"  # isPost() with else-branch refusal
    assert index.get("core/firmware/status").method == "GET"  # isPost() only switches on a probe
    assert index.get("firewall/alias/add_item").method == "POST"
    assert index.get("unbound/settings/set").method == "POST"  # inherited from ApiMutableModelControllerBase
    assert index.get("unbound/service/status").method == "GET"  # inherited from ApiMutableServiceControllerBase


def test_dnat_body_validation_catches_legacy_field_names():
    ep = index.get("firewall/d_nat/add_rule")
    good = {"rule": {"interface": "wan", "protocol": "tcp", "target": "192.168.1.10", "local-port": "8006",
                     "destination": {"network": "wanip", "port": "8006"}, "descr": "x", "disabled": "0"}}
    assert index.validate_body(ep, good) == {"validated": True}
    with pytest.raises(index.OPNIndexError, match="dstport: unknown field"):
        index.validate_body(ep, {"rule": {"dstport": "8006", "target": "1.2.3.4"}})


def test_body_validation_types_options_and_required():
    ep = index.get("firewall/alias/add_item")
    with pytest.raises(index.OPNIndexError) as e:
        index.validate_body(ep, {"alias": {"type": "bogus", "enabled": "yes"}})
    msg = str(e.value)
    assert "type: bogus not in" in msg and "enabled: expected 0 or 1" in msg
    with pytest.raises(index.OPNIndexError, match="name: required"):
        index.validate_body(index.get("firewall/category/add_item"), {"category": {"color": "ff0000"}})
    with pytest.raises(index.OPNIndexError, match="body must be"):
        index.validate_body(ep, {"name": "x"})


def test_set_is_not_held_to_required_fields():
    ep = index.get("firewall/alias/set_item")
    assert index.validate_body(ep, {"alias": {"description": "only this"}})["validated"]


def test_destructive_classification():
    assert index.is_destructive(index.get("firewall/alias/del_item"))
    assert index.is_destructive(index.get("core/firmware/update"))
    assert index.is_destructive(index.get("core/service/stop"))
    assert not index.is_destructive(index.get("firewall/alias/add_item"))
    assert not index.is_destructive(index.get("core/firmware/status"))


def test_apply_endpoint_lookup():
    assert index.apply_endpoint("firewall/alias").path == "firewall/alias/reconfigure"
    assert index.apply_endpoint("firewall/d_nat").path == "firewall/d_nat/apply"
    assert index.apply_endpoint("unbound/settings").path == "unbound/service/reconfigure"


def test_describe_shows_nested_body_fields():
    d = index.describe(index.get("firewall/d_nat/add_rule"))
    rule = d["body"]["rule"]
    assert "network" in rule["destination"]["fields"] and rule["ipprotocol"]["options"] == ["inet", "inet6", "inet46"]
    assert index.describe(index.get("firewall/alias/search_item"))["search_params"]


def test_search():
    paths = [h["path"] for h in index.search("alias item")]
    assert "firewall/alias/add_item" in paths
    assert all(h["origin"] == "core" for h in index.search("settings", origins={"core"}))
