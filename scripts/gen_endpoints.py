"""Build the OPNsense API index bundled with opn-mcp from opnsense/core and opnsense/plugins sources.

Usage: python scripts/gen_endpoints.py <core-checkout> <plugins-checkout> <version>

OPNsense publishes no machine-readable API schema, so this reads the MVC sources:
- controllers/OPNsense/<Module>/Api/<Name>Controller.php -> /api/<module>/<name_snake>/<action_snake>[/<args>]
- inheritance through abstract controllers and the Base classes (get/set, service start/stop/...)
- the CRUD helpers each action calls ($this->setBase("alias", "aliases.alias", ...)) -> body key + model node
- models/**/<Model>.xml -> field names, types, required/default/options
"""

import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CLASS_RE = re.compile(r"^\s*(abstract\s+)?class\s+(\w+)\s+extends\s+([\\\w]+)", re.M)
NS_RE = re.compile(r"^namespace\s+([\\\w]+);", re.M)
USE_RE = re.compile(r"^use\s+([\\\w]+)(?:\s+as\s+(\w+))?;", re.M)
ACTION_RE = re.compile(r"(/\*\*(?:(?!\*/).)*\*/\s*)?public\s+function\s+(\w+)Action\s*\(([^)]*)\)", re.S)
STATIC_RE = re.compile(r"protected\s+static\s+\$(internal\w+)\s*=\s*['\"]([^'\"]*)['\"]")
HELPER_RE = re.compile(r"\$this->(searchBase|searchRecordsetBase|getBase|setBase|addBase|delBase|toggleBase)\(\s*"
                       r"['\"]([\w.]+)['\"](?:\s*,\s*['\"]([\w.]+)['\"])?")
POST_HINTS = ("getPost(", "hasPost(", "addBase(", "setBase(", "delBase(", "toggleBase(", "->setAction(", "::setAction(")
GUARD_RE = re.compile(r"if\s*\(\s*(!?)\s*\$this->request->isPost\(\)\s*\)\s*\{")

BASE_ACTIONS = {
    "ApiMutableModelControllerBase": {
        "get": {"method": "GET", "helper": "model_get", "doc": "Return the whole model (settings) as stored."},
        "set": {"method": "POST", "helper": "model_set", "doc": "Validate and save the whole model (settings)."},
    },
    "ApiMutableServiceControllerBase": {
        "start": {"method": "POST", "doc": "Start the service."},
        "stop": {"method": "POST", "doc": "Stop the service."},
        "restart": {"method": "POST", "doc": "Restart the service."},
        "reconfigure": {"method": "POST", "doc": "Regenerate config and reload/restart the service as needed."},
        "status": {"method": "GET", "doc": "Service status (running/stopped/disabled)."},
    },
}
BASE_PARENTS = {"ApiMutableServiceControllerBase": "ApiMutableModelControllerBase",
                "ApiMutableModelControllerBase": "ApiControllerBase"}


def snake(name: str) -> str:
    # Mirrors OPNsense's router: DNat -> d_nat, searchItem -> search_item.
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def docline(block: str | None) -> str:
    if not block:
        return ""
    for line in block.splitlines():
        text = line.strip().lstrip("/*").strip()
        if text and not text.startswith("@"):
            return text[:300]
    return ""


def function_body(src: str, start: int) -> str:
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j]
    return src[i:]


def method_for(body: str) -> str:
    """POST when the action reads a POST body or is wrapped in an isPost() guard; GET otherwise.
    Actions that only branch on isPost() (e.g. firmware status) accept both, so they stay GET."""
    if any(h in body for h in POST_HINTS):
        return "POST"
    m = GUARD_RE.search(body)
    if m:
        if m.group(1):  # if (!isPost()) { refuse }
            return "POST"
        block_end = m.end() - 1 + len(function_body(body, m.end() - 1)) + 1
        rest = body[block_end:]
        # The whole action sits inside the if, or an else branch refuses non-POST requests.
        if re.fullmatch(r"\s*(return\s[^;]*;\s*)?", rest) or re.match(r"\s*else\b", rest):
            return "POST"
    return "GET"


def parse_args(raw: str) -> list[dict]:
    args = []
    for part in filter(None, (p.strip() for p in raw.split(","))):
        m = re.search(r"\$(\w+)(\s*=\s*(.+))?", part)
        if m:
            args.append({"name": m.group(1), "optional": bool(m.group(2))})
    return args


class Source:
    """One tree of MVC sources (core, or one plugin)."""

    def __init__(self, mvc: Path, origin: str):
        self.mvc, self.origin = mvc, origin


def scan_classes(sources: list[Source]) -> dict[str, dict]:
    classes: dict[str, dict] = {}
    for s in sources:
        for php in (s.mvc / "app/controllers").rglob("*.php"):
            src = php.read_text(errors="replace")
            m = CLASS_RE.search(src)
            ns = NS_RE.search(src)
            if not m or not ns:
                continue
            fq = f"{ns.group(1)}\\{m.group(2)}"
            uses = {(alias or u.rsplit("\\", 1)[-1]): u.lstrip("\\") for u, alias in USE_RE.findall(src)}
            parent = m.group(3)
            if parent.startswith("\\"):
                parent_fq = parent.lstrip("\\")
            elif parent in uses:
                parent_fq = uses[parent]
            else:
                parent_fq = f"{ns.group(1)}\\{parent}"
            actions = {}
            for am in ACTION_RE.finditer(src):
                name = am.group(2)
                body = function_body(src, am.end())
                helpers = [(h, a, b) for h, a, b in HELPER_RE.findall(body)]
                actions[name] = {
                    "doc": docline(am.group(1)),
                    "args": parse_args(am.group(3)),
                    "method": method_for(body),
                    "helpers": helpers,
                }
            classes[fq] = {"abstract": bool(m.group(1)), "parent": parent_fq, "file": php, "origin": s.origin,
                           "statics": {k: v.replace("\\\\", "\\") for k, v in STATIC_RE.findall(src)},
                           "actions": actions,
                           "short": m.group(2), "ns": ns.group(1)}
    return classes


def lineage(fq: str, classes: dict[str, dict]) -> list[str]:
    chain, seen = [], set()
    while fq and fq not in seen:
        seen.add(fq)
        chain.append(fq)
        short = fq.rsplit("\\", 1)[-1]
        if fq in classes:
            fq = classes[fq]["parent"]
        elif short in BASE_PARENTS:
            fq = "OPNsense\\Base\\" + BASE_PARENTS[short]
        else:
            break
    return chain


# --- models --------------------------------------------------------------------------------------

FIELD_KEYS = {"Required": "required", "Default": "default", "Multiple": "multiple",
              "MinimumValue": "min", "MaximumValue": "max", "Mask": "mask", "BlankDesc": "blank"}


def field_base_types(sources: list[Source]) -> dict[str, str]:
    """Map custom field class names to their parent (AliasField -> ArrayField)."""
    out = {}
    for s in sources:
        for php in (s.mvc / "app/models").rglob("FieldTypes/*.php"):
            m = CLASS_RE.search(php.read_text(errors="replace"))
            if m:
                out[m.group(2)] = m.group(3).rsplit("\\", 1)[-1]
                if php.parent.parent.name == "Base" and s.origin == "core":
                    CORE_FIELD_TYPES.add(m.group(2))
    return out


CORE_FIELD_TYPES: set[str] = set()


def resolve_type(raw: str, bases: dict[str, str]) -> str:
    """Walk custom field classes up to the nearest stock type from OPNsense/Base/FieldTypes."""
    name = raw.replace("/", "\\").rsplit("\\", 1)[-1]
    seen = set()
    while name not in CORE_FIELD_TYPES and name in bases and name not in seen:
        seen.add(name)
        name = bases[name]
    return name


def parse_fields(el: ET.Element, bases: dict[str, str]) -> dict:
    out = {}
    for child in el:
        if not isinstance(child.tag, str):
            continue
        raw = child.get("type")
        if raw is None:
            nested = parse_fields(child, bases)
            if nested:
                out[child.tag] = {"type": "container", "fields": nested}
            continue
        base = resolve_type(raw, bases)
        f: dict = {"type": base}
        cls = raw.replace("/", "\\").rsplit("\\", 1)[-1]
        if cls != base:
            f["class"] = cls
        for tag, key in FIELD_KEYS.items():
            node = child.find(tag)
            if node is not None and node.text is not None:
                text = node.text.strip()
                f[key] = text == "Y" if key in ("required", "multiple") else text
        opts = child.find("OptionValues")
        if opts is not None:
            f["options"] = [o.get("value") or o.tag for o in opts if isinstance(o.tag, str)][:200]
        if base == "ArrayField" or (child.find("*[@type]") is not None and base not in ("OptionField",)):
            sub = parse_fields(child, bases)
            if sub:
                f["fields"] = sub
        out[child.tag] = f
    return out


def scan_models(sources: list[Source], bases: dict[str, str]) -> dict[str, dict]:
    models = {}
    for s in sources:
        root = s.mvc / "app/models"
        for xml in root.rglob("*.xml"):
            if "Menu" in xml.parts or "ACL" in xml.parts or "Migrations" in xml.parts:
                continue
            try:
                tree = ET.parse(xml).getroot()
            except ET.ParseError:
                continue
            if tree.tag != "model" or tree.find("items") is None:
                continue
            cls = "\\".join(xml.relative_to(root).with_suffix("").parts)
            models[cls] = {"mount": (tree.findtext("mount") or "").strip(),
                           "description": (tree.findtext("description") or "").strip(),
                           "fields": parse_fields(tree.find("items"), bases)}
    return models


def model_node(model: dict, path: str) -> dict | None:
    node = {"fields": model["fields"]}
    for part in path.split("."):
        node = node.get("fields", {}).get(part)
        if node is None:
            return None
    return node


# --- assemble ------------------------------------------------------------------------------------


def build(sources: list[Source], version: str) -> dict:
    classes = scan_classes(sources)
    models = scan_models(sources, field_base_types(sources))
    endpoints = {}
    for fq, c in classes.items():
        if c["abstract"] or "\\Api\\" not in fq + "\\" or not c["short"].endswith("Controller"):
            continue
        parts = fq.split("\\")
        if len(parts) < 4 or parts[-2] != "Api":
            continue
        module = parts[-3].lower()
        controller = snake(c["short"].removesuffix("Controller"))
        chain = lineage(fq, classes)
        statics: dict[str, str] = {}
        for anc in reversed(chain):
            statics.update(classes.get(anc, {}).get("statics", {}))
        model_cls = statics.get("internalModelClass", "").lstrip("\\")
        model_name = statics.get("internalModelName")
        actions: dict[str, dict] = {}
        for anc in reversed(chain):
            short = anc.rsplit("\\", 1)[-1]
            for name, spec in BASE_ACTIONS.get(short, {}).items():
                actions[name] = {"doc": spec["doc"], "args": [], "method": spec["method"],
                                 "helpers": [(spec["helper"], model_name or "", "")] if "helper" in spec else []}
            actions.update(classes.get(anc, {}).get("actions", {}))
        for name, a in actions.items():
            path = f"{module}/{controller}/{snake(name)}"
            ep: dict = {"method": a["method"], "origin": c["origin"], "doc": a["doc"],
                        "args": a["args"]}
            body = None
            for helper, first, second in a["helpers"]:
                if helper in ("setBase", "addBase") and second:
                    ep["method"] = "POST"
                    body = (first, second)
                elif helper == "model_set" and model_name:
                    body = (model_name, "")
                elif helper == "model_get":
                    ep["node"] = ""
                elif helper in ("searchBase", "searchRecordsetBase"):
                    ep["kind"] = "search"
                    ep["node"] = first
                elif helper in ("getBase",) and second:
                    ep["node"] = second
                elif helper in ("delBase", "toggleBase"):
                    ep["method"] = "POST"
                    ep["node"] = first
            if body and model_cls in models:
                key, node_path = body
                ep["body_key"] = key
                ep["model"] = model_cls
                ep["node"] = node_path
            elif model_cls in models:
                ep["model"] = model_cls  # the controller's model, for context even without a known node
                if name in ("get", "set") and "node" not in ep:
                    ep["node"] = ""
                    if name == "set" and model_name:
                        ep["body_key"] = model_name
            endpoints[path] = ep
    return {"version": version, "endpoints": dict(sorted(endpoints.items())),
            "models": {k: models[k] for k in sorted({e["model"] for e in endpoints.values() if "model" in e})}}


def main() -> None:
    core, plugins, version = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    sources = [Source(core / "src/opnsense/mvc", "core")]
    for mk in sorted(plugins.glob("*/*/Makefile")):
        mvc = mk.parent / "src/opnsense/mvc"
        name = re.search(r"^PLUGIN_NAME=\s*(\S+)", mk.read_text(), re.M)
        if mvc.is_dir() and name:
            sources.append(Source(mvc, f"os-{name.group(1)}"))
    index = build(sources, version)
    out = Path(__file__).parent.parent / "src/opn_mcp/data/opnsense-api.json.gz"
    out.write_bytes(gzip.compress(json.dumps(index, separators=(",", ":")).encode(), mtime=0))
    by_origin: dict[str, int] = {}
    for e in index["endpoints"].values():
        by_origin[e["origin"]] = by_origin.get(e["origin"], 0) + 1
    print(f"{len(index['endpoints'])} endpoints ({by_origin.get('core', 0)} core, "
          f"{len(by_origin) - 1} plugins), {len(index['models'])} models -> {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
