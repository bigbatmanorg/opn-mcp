# opn-mcp

MCP server for OPNsense. It covers the whole API of core and the plugins, using an index generated from the
OPNsense sources, and checks request bodies against the model XML.

- **Index:** `src/opn_mcp/data/opnsense-api.json.gz` comes from `opnsense/core` + `opnsense/plugins` at tag 26.7.4:
  2469 endpoints (986 core, 69 plugins) and 169 models. Methods are read from the controller code and inheritance,
  bodies from the CRUD helpers plus the model XML. Regenerate it after an upgrade:

      git clone --depth 1 --branch <ver> https://github.com/opnsense/core opn-src/core-<ver>
      git clone --depth 1 --filter=blob:none --sparse --branch <ver> https://github.com/opnsense/plugins opn-src/plugins-<ver>
      (cd opn-src/plugins-<ver> && git sparse-checkout set --no-cone '/*/*/src/opnsense/mvc/' '/*/*/Makefile')
      python scripts/gen_endpoints.py opn-src/core-<ver> opn-src/plugins-<ver> <ver>

- **Generic:** `opn_api_search`, `opn_api_describe`, `opn_api`. Plugin endpoints are refused when the plugin is not
  installed.
- **Model CRUD:** `opn_items` (search/get/add/set/del/toggle for any controller, then apply) and `opn_settings`
  (get/set a whole model).
- **Curated:** `opn_overview`, `opn_service`, `opn_neighbors`, `opn_firewall_log`, `opn_ping`.

## Safety

- Read-only by default. `OPNSENSE_ALLOW_WRITES=true` enables writes. Destructive actions (del, stop, reboot,
  firmware update, revert) also need `confirm=true` on the call.
- Unknown body fields are rejected. OPNsense itself drops them silently and still answers "saved".
- **Firewall auto-revert:** OPNsense 26.7 removed `firewall/filter/savepoint`. Instead, the first firewall write
  records the newest config-history entry, and applying arms a 60 s timer. The timer reverts to that entry
  (`core/backup/revert_backup`, then alias reconfigure and filter apply) unless `opn_firewall_confirm` is called.
  `opn_firewall_revert_now` reverts at once. The timer lives in the server process, so it does not fire if the
  server exits.

## Configuration

`OPNSENSE_URL` (`https://host/api`), `OPNSENSE_API_KEY`, `OPNSENSE_API_SECRET`, `OPNSENSE_VERIFY_SSL`
(`false`, `true` or a CA path), `OPNSENSE_ALLOW_WRITES`, `OPNSENSE_TIMEOUT`.

## Tests

    uv run pytest                                   # unit tests (mocked HTTP)
    OPN_LAB=1 uv run pytest -m lab   # lab OPNsense only

Lab tests create and delete real objects; run them only against a disposable instance (OPN_LAB=1 with OPN_URL, OPN_API_KEY, OPN_API_SECRET).

## Use with Claude Code

    claude mcp add-json -s user opn '{"type":"stdio","command":"uvx","args":["--from","git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0","opn-mcp"],"env":{...}}'

## License

BSD-2-Clause. The bundled index (`src/opn_mcp/data/opnsense-api.json.gz`) is generated from [opnsense/core](https://github.com/opnsense/core) and [opnsense/plugins](https://github.com/opnsense/plugins), BSD-2-Clause, Copyright Deciso B.V. and the OPNsense contributors.
