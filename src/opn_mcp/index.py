"""The bundled OPNsense API index (generated from opnsense/core + plugins sources): lookup, search, validation."""

import gzip
import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

# Actions that delete data, cut connectivity or replace the running system. Matched on the action name.
DESTRUCTIVE_ACTION = re.compile(r"^(del|delete|remove|flush|kill|purge|drop|clear|disconnect|revert|reset|"
                                r"wipe|stop|reboot|halt|poweroff|reinstall|upgrade|update$)")
NOT_DESTRUCTIVE = re.compile(r"^(update_?[a-z]|reset_?(log|stat)|clear_?log)")


class OPNIndexError(ValueError):
    """The request does not match the OPNsense API index."""


@dataclass(frozen=True)
class Endpoint:
    path: str  # module/controller/action
    spec: dict[str, Any]

    @property
    def action(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def method(self) -> str:
        return self.spec["method"]

    @property
    def origin(self) -> str:
        return self.spec["origin"]


@cache
def load() -> dict[str, Any]:
    raw = resources.files("opn_mcp.data").joinpath("opnsense-api.json.gz").read_bytes()
    return json.loads(gzip.decompress(raw))


def version() -> str:
    return load()["version"]


def normalize(path: str) -> str:
    path = path.strip().strip("/")
    return path.removeprefix("api/")


def resolve(path: str) -> tuple[Endpoint, list[str]]:
    """Split module/controller/action[/arg...] into the endpoint and its positional args."""
    parts = normalize(path).split("/")
    if len(parts) < 3:
        raise OPNIndexError(f"{path}: expected module/controller/action")
    eps = load()["endpoints"]
    base = "/".join(parts[:3])
    spec = eps.get(base)
    if spec is None:
        # OPNsense also routes camelCase actions (searchItem); the index uses snake_case.
        snake = re.sub(r"(?<!^)([A-Z])", r"_\1", parts[2]).lower()
        spec = eps.get("/".join([*parts[:2], snake]))
        base = "/".join([*parts[:2], snake])
    if spec is None:
        near = [k for k in eps if k.startswith("/".join(parts[:2]) + "/")]
        hint = f" Actions on {'/'.join(parts[:2])}: {', '.join(k.rsplit('/', 1)[1] for k in near)}" if near else \
            " Use opn_api_search to find endpoints."
        raise OPNIndexError(f"Unknown endpoint {base}.{hint}")
    return Endpoint(base, spec), parts[3:]


def get(path: str) -> Endpoint:
    return resolve(path)[0]


def exists(path: str) -> bool:
    return normalize(path) in load()["endpoints"]


def search(query: str, origins: set[str] | None = None, limit: int = 30) -> list[dict[str, Any]]:
    terms = query.lower().replace("/", " ").split()
    hits = []
    for path, spec in load()["endpoints"].items():
        if origins is not None and spec["origin"] not in origins:
            continue
        hay = f"{path.replace('_', ' ')} {path} {spec['doc']} {spec.get('model', '')}".lower()
        if all(t in hay for t in terms):
            score = (-sum(t in path for t in terms), len(path))
            hits.append((score, {"path": path, "method": spec["method"], "doc": spec["doc"],
                                 "args": [a["name"] for a in spec["args"]], "origin": spec["origin"]}))
    hits.sort(key=lambda h: h[0])
    return [h[1] for h in hits[:limit]]


def is_write(ep: Endpoint) -> bool:
    return ep.method == "POST" and not ep.action.startswith(("search", "get", "list", "status"))


def is_destructive(ep: Endpoint) -> bool:
    action = ep.action
    if NOT_DESTRUCTIVE.match(action):
        return False
    return bool(DESTRUCTIVE_ACTION.match(action)) and ep.method == "POST"


def model_node(ep: Endpoint) -> dict[str, Any] | None:
    """The model subtree this endpoint reads or writes (an ArrayField item, or the whole model)."""
    model = load()["models"].get(ep.spec.get("model", ""))
    if model is None or "node" not in ep.spec:
        return None
    node: dict[str, Any] = {"fields": model["fields"]}
    for part in filter(None, ep.spec["node"].split(".")):
        node = node.get("fields", {}).get(part)
        if node is None:
            return None
    return node


def check_args(ep: Endpoint, args: list[str]) -> None:
    spec_args = ep.spec["args"]
    required = [a["name"] for a in spec_args if not a["optional"]]
    if len(args) < len(required):
        raise OPNIndexError(f"{ep.path} needs path args: {'/'.join(required)} (got {len(args)})")
    if len(args) > len(spec_args):
        raise OPNIndexError(f"{ep.path} takes at most {len(spec_args)} path args "
                          f"({'/'.join(a['name'] for a in spec_args) or 'none'})")


def _validate_fields(fields: dict[str, Any], data: dict[str, Any], prefix: str, creating: bool,
                     errors: list[str]) -> None:
    for key, value in data.items():
        name = f"{prefix}{key}"
        f = fields.get(key)
        if f is None:
            errors.append(f"{name}: unknown field (OPNsense would silently ignore it); "
                          f"known: {', '.join(sorted(fields))}")
            continue
        sub = f.get("fields")
        if f["type"] == "container" or (sub and f["type"] != "ArrayField"):
            if not isinstance(value, dict):
                errors.append(f"{name}: expected an object with {', '.join(sorted(sub or {}))}")
                continue
            _validate_fields(sub or {}, value, name + ".", creating, errors)
            continue
        if isinstance(value, (dict, list)) and f["type"] != "ArrayField":
            if isinstance(value, list) and f.get("multiple"):
                value = ",".join(map(str, value))
            else:
                errors.append(f"{name}: expected a scalar value")
                continue
        text = "" if value is None else str(value)
        if f["type"] == "BooleanField" and text not in ("0", "1", ""):
            errors.append(f"{name}: expected 0 or 1, got {value!r}")
        elif f["type"] == "IntegerField" and text:
            if not re.fullmatch(r"-?\d+", text):
                errors.append(f"{name}: expected an integer, got {value!r}")
            else:
                if "min" in f and int(text) < int(f["min"]):
                    errors.append(f"{name}: {text} is below minimum {f['min']}")
                if "max" in f and int(text) > int(f["max"]):
                    errors.append(f"{name}: {text} is above maximum {f['max']}")
        elif f["type"] == "OptionField" and text and f.get("options"):
            values = text.split(",") if f.get("multiple") else [text]
            bad = [v for v in values if v not in f["options"]]
            if bad:
                errors.append(f"{name}: {', '.join(bad)} not in {f['options']}")
    if creating:
        for key, f in fields.items():
            # Custom field classes (e.g. DNatSequenceField) often fill themselves in, so only stock types count.
            if (f.get("required") and "default" not in f and "class" not in f and key not in data
                    and f["type"] != "container"):
                errors.append(f"{prefix}{key}: required")


def validate_body(ep: Endpoint, body: dict[str, Any]) -> dict[str, Any]:
    """Check a POST body against the model. Returns warnings that are not fatal (none at the moment)."""
    key = ep.spec.get("body_key")
    node = model_node(ep)
    if not key or node is None:
        return {"validated": False}
    if set(body) != {key}:
        raise OPNIndexError(f"{ep.path}: body must be {{\"{key}\": {{...fields}}}}, got keys {sorted(body)}")
    if not isinstance(body[key], dict):
        raise OPNIndexError(f"{ep.path}: body[{key!r}] must be an object")
    errors: list[str] = []
    creating = ep.action.startswith("add")
    _validate_fields(node.get("fields", {}), body[key], "", creating, errors)
    if errors:
        raise OPNIndexError(f"{ep.path}: " + "; ".join(errors))
    return {"validated": True}


def describe_fields(fields: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    out = {}
    for name, f in fields.items():
        entry = {k: v for k, v in f.items() if k != "fields"}
        if f.get("fields"):
            if depth < 2:
                entry["fields"] = describe_fields(f["fields"], depth + 1)
            else:
                entry["fields"] = sorted(f["fields"])
        out[name] = entry
    return out


def describe(ep: Endpoint) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": ep.path, "method": ep.method, "doc": ep.spec["doc"], "origin": ep.origin,
        "args": ep.spec["args"], "write": is_write(ep), "destructive": is_destructive(ep),
    }
    if ep.spec.get("kind") == "search":
        out["search_params"] = {"current": "page (1-based)", "rowCount": "rows per page, -1 for all",
                                "searchPhrase": "free-text filter", "sort": {"<field>": "asc|desc"}}
    if "model" in ep.spec:
        out["model"] = ep.spec["model"]
        out["model_node"] = ep.spec.get("node")
    node = model_node(ep)
    if ep.spec.get("body_key") and node is not None:
        out["body"] = {ep.spec["body_key"]: describe_fields(node.get("fields", {}))}
    elif node is not None and ep.spec.get("node"):
        out["row_fields"] = sorted(node.get("fields", {}))
    return out


def controller_actions(controller: str) -> dict[str, Endpoint]:
    prefix = normalize(controller) + "/"
    return {p[len(prefix):]: Endpoint(p, s) for p, s in load()["endpoints"].items() if p.startswith(prefix)}


def apply_endpoint(path: str) -> Endpoint | None:
    """The endpoint that activates saved changes for a controller: its apply/reconfigure, else the module's
    service/reconfigure."""
    module, controller = normalize(path).split("/")[:2]
    for candidate in (f"{module}/{controller}/apply", f"{module}/{controller}/reconfigure",
                      f"{module}/service/reconfigure"):
        if exists(candidate):
            return get(candidate)
    return None
