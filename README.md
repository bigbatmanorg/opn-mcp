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

<!-- install:start -->
## Install in your agent

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_opnsense-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=opnsense&inputs=%5B%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22opnsense_url%22%2C%22description%22%3A%22Firewall%20API%20URL%22%2C%22password%22%3Afalse%7D%2C%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22opnsense_api_key%22%2C%22description%22%3A%22API%20key%22%2C%22password%22%3Atrue%7D%2C%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22opnsense_api_secret%22%2C%22description%22%3A%22API%20secret%22%2C%22password%22%3Atrue%7D%5D&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22git%2Bhttps%3A//github.com/bigbatmanorg/opn-mcp%40v0.1.0%22%2C%22opn-mcp%22%5D%2C%22env%22%3A%7B%22OPNSENSE_URL%22%3A%22%24%7Binput%3Aopnsense_url%7D%22%2C%22OPNSENSE_API_KEY%22%3A%22%24%7Binput%3Aopnsense_api_key%7D%22%2C%22OPNSENSE_API_SECRET%22%3A%22%24%7Binput%3Aopnsense_api_secret%7D%22%7D%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install_opnsense-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=opnsense&inputs=%5B%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22opnsense_url%22%2C%22description%22%3A%22Firewall%20API%20URL%22%2C%22password%22%3Afalse%7D%2C%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22opnsense_api_key%22%2C%22description%22%3A%22API%20key%22%2C%22password%22%3Atrue%7D%2C%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22opnsense_api_secret%22%2C%22description%22%3A%22API%20secret%22%2C%22password%22%3Atrue%7D%5D&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22git%2Bhttps%3A//github.com/bigbatmanorg/opn-mcp%40v0.1.0%22%2C%22opn-mcp%22%5D%2C%22env%22%3A%7B%22OPNSENSE_URL%22%3A%22%24%7Binput%3Aopnsense_url%7D%22%2C%22OPNSENSE_API_KEY%22%3A%22%24%7Binput%3Aopnsense_api_key%7D%22%2C%22OPNSENSE_API_SECRET%22%3A%22%24%7Binput%3Aopnsense_api_secret%7D%22%7D%7D&quality=insiders)
[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en-US/install-mcp?name=opnsense&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJnaXQraHR0cHM6Ly9naXRodWIuY29tL2JpZ2JhdG1hbm9yZy9vcG4tbWNwQHYwLjEuMCIsIm9wbi1tY3AiXSwiZW52Ijp7Ik9QTlNFTlNFX1VSTCI6Imh0dHBzOi8vb3Buc2Vuc2UuZXhhbXBsZS5sYW4vYXBpIiwiT1BOU0VOU0VfQVBJX0tFWSI6InlvdXItYXBpLWtleSIsIk9QTlNFTlNFX0FQSV9TRUNSRVQiOiJ5b3VyLWFwaS1zZWNyZXQifX0%3D)

It works with any MCP client that can start a stdio command. It needs [uv](https://docs.astral.sh/uv/). `uvx` fetches the pinned release, so no clone or virtualenv is needed. Pick your agent below.

| Variable | Meaning | Example |
|---|---|---|
| `OPNSENSE_URL` | Firewall API URL | `https://opnsense.example.lan/api` |
| `OPNSENSE_API_KEY` | API key | `your-api-key` |
| `OPNSENSE_API_SECRET` | API secret | `your-api-secret` |

Optional: `OPNSENSE_ALLOW_WRITES=true` (writes), `OPNSENSE_VERIFY_SSL` (`true` or a CA path). Without `OPNSENSE_ALLOW_WRITES` the server is **read-only**. Create the key under System → Access → Users → (user) → API keys. A dedicated user with only the pages you need is safer than root.

The VS Code buttons ask for each value, keeping secrets out of files. The Cursor button writes placeholders: edit them in `~/.cursor/mcp.json` afterwards. Elsewhere, replace the example values.

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add opnsense -s user -e OPNSENSE_URL=https://opnsense.example.lan/api -e OPNSENSE_API_KEY=your-api-key -e OPNSENSE_API_SECRET=your-api-secret -- uvx --from git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0 opn-mcp
```

</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "opnsense": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "env": {
        "OPNSENSE_URL": "https://opnsense.example.lan/api",
        "OPNSENSE_API_KEY": "your-api-key",
        "OPNSENSE_API_SECRET": "your-api-secret"
      }
    }
  }
}
```

</details>

<details>
<summary><b>VS Code / GitHub Copilot</b></summary>

Use the button above, or add this to `.vscode/mcp.json` (workspace) or to the user `mcp.json` (command
*MCP: Open User Configuration*):

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "opnsense_url",
      "description": "Firewall API URL",
      "password": false
    },
    {
      "type": "promptString",
      "id": "opnsense_api_key",
      "description": "API key",
      "password": true
    },
    {
      "type": "promptString",
      "id": "opnsense_api_secret",
      "description": "API secret",
      "password": true
    }
  ],
  "servers": {
    "opnsense": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "env": {
        "OPNSENSE_URL": "${input:opnsense_url}",
        "OPNSENSE_API_KEY": "${input:opnsense_api_key}",
        "OPNSENSE_API_SECRET": "${input:opnsense_api_secret}"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Cursor</b></summary>

Use the button above, or add this to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project):

```json
{
  "mcpServers": {
    "opnsense": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "env": {
        "OPNSENSE_URL": "https://opnsense.example.lan/api",
        "OPNSENSE_API_KEY": "your-api-key",
        "OPNSENSE_API_SECRET": "your-api-secret"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Windsurf</b></summary>

Add this to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "opnsense": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "env": {
        "OPNSENSE_URL": "https://opnsense.example.lan/api",
        "OPNSENSE_API_KEY": "your-api-key",
        "OPNSENSE_API_SECRET": "your-api-secret"
      }
    }
  }
}
```

</details>

<details>
<summary><b>OpenAI Codex CLI</b></summary>

```bash
codex mcp add opnsense --env OPNSENSE_URL=https://opnsense.example.lan/api --env OPNSENSE_API_KEY=your-api-key --env OPNSENSE_API_SECRET=your-api-secret -- uvx --from git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0 opn-mcp
```

Or add this to `~/.codex/config.toml`:

```toml
[mcp_servers.opnsense]
command = "uvx"
args = ["--from", "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0", "opn-mcp"]
env = { OPNSENSE_URL = "https://opnsense.example.lan/api", OPNSENSE_API_KEY = "your-api-key", OPNSENSE_API_SECRET = "your-api-secret" }
```

</details>

<details>
<summary><b>Gemini CLI</b></summary>

Add this to `~/.gemini/settings.json` (or to `.gemini/settings.json` in a project):

```json
{
  "mcpServers": {
    "opnsense": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "env": {
        "OPNSENSE_URL": "https://opnsense.example.lan/api",
        "OPNSENSE_API_KEY": "your-api-key",
        "OPNSENSE_API_SECRET": "your-api-secret"
      }
    }
  }
}
```

</details>

<details>
<summary><b>DeepAgents</b></summary>

**Deep Agents CLI:** add this to `~/.deepagents/.mcp.json` (all projects) or to `.mcp.json` in a project. `${VAR}` values are read from your shell or `~/.deepagents/.env`, so secrets stay out of the file.

```json
{
  "mcpServers": {
    "opnsense": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "env": {
        "OPNSENSE_URL": "${OPNSENSE_URL}",
        "OPNSENSE_API_KEY": "${OPNSENSE_API_KEY}",
        "OPNSENSE_API_SECRET": "${OPNSENSE_API_SECRET}"
      }
    }
  }
}
```

**Python library** (`pip install deepagents langchain-mcp-adapters`):

```python
import asyncio
import os

from deepagents import create_deep_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "opnsense": {
        "transport": "stdio",
        "command": "uvx",
        "args": ["--from", "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0", "opn-mcp"],
        "env": {
            "OPNSENSE_URL": os.environ["OPNSENSE_URL"],
            "OPNSENSE_API_KEY": os.environ["OPNSENSE_API_KEY"],
            "OPNSENSE_API_SECRET": os.environ["OPNSENSE_API_SECRET"],
        },
    }
})


async def main():
    tools = await client.get_tools()
    agent = create_deep_agent(model="anthropic:claude-sonnet-5-5", tools=tools)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": "What can you do?"}]})
    print(result["messages"][-1].content)


asyncio.run(main())
```

</details>

<details>
<summary><b>Docker Agent</b></summary>

Save this as `agent.yaml`, then run `docker agent run agent.yaml`:

```yaml
agents:
  root:
    model: anthropic/claude-sonnet-5-5
    description: Assistant with the opnsense MCP tools
    instruction: Use the opnsense tools to answer the user's requests.
    toolsets:
      - type: mcp
        command: uvx
        args:
          - "--from"
          - "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0"
          - "opn-mcp"
        env:
          OPNSENSE_URL: "https://opnsense.example.lan/api"
          OPNSENSE_API_KEY: "your-api-key"
          OPNSENSE_API_SECRET: "your-api-secret"
```

</details>

<details>
<summary><b>Hermes Agent</b></summary>

Add this to `~/.hermes/config.yaml`, then run `/reload-mcp` in a chat:

```yaml
mcp_servers:
  opnsense:
    command: uvx
    args: ["--from", "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0", "opn-mcp"]
    env:
      OPNSENSE_URL: "https://opnsense.example.lan/api"
      OPNSENSE_API_KEY: "your-api-key"
      OPNSENSE_API_SECRET: "your-api-secret"
```

</details>

<details>
<summary><b>Cline / Roo Code / Kilo Code</b></summary>

MCP Servers → Configure (`cline_mcp_settings.json` / `mcp_settings.json`):

```json
{
  "mcpServers": {
    "opnsense": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "env": {
        "OPNSENSE_URL": "https://opnsense.example.lan/api",
        "OPNSENSE_API_KEY": "your-api-key",
        "OPNSENSE_API_SECRET": "your-api-secret"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Zed</b></summary>

Add this to `settings.json`:

```json
{
  "context_servers": {
    "opnsense": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "env": {
        "OPNSENSE_URL": "https://opnsense.example.lan/api",
        "OPNSENSE_API_KEY": "your-api-key",
        "OPNSENSE_API_SECRET": "your-api-secret"
      }
    }
  }
}
```

</details>

<details>
<summary><b>opencode</b></summary>

Add this to `opencode.json` (project) or `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "opnsense": {
      "type": "local",
      "command": [
        "uvx",
        "--from",
        "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0",
        "opn-mcp"
      ],
      "environment": {
        "OPNSENSE_URL": "https://opnsense.example.lan/api",
        "OPNSENSE_API_KEY": "your-api-key",
        "OPNSENSE_API_SECRET": "your-api-secret"
      },
      "enabled": true
    }
  }
}
```

</details>

<details>
<summary><b>Goose</b></summary>

Run `goose configure` → Add Extension → Command-line Extension, with the command `uvx --from git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0 opn-mcp`. Or add this to
`~/.config/goose/config.yaml`:

```yaml
extensions:
  opnsense:
    type: stdio
    enabled: true
    cmd: uvx
    args: ["--from", "git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0", "opn-mcp"]
    envs:
      OPNSENSE_URL: "https://opnsense.example.lan/api"
      OPNSENSE_API_KEY: "your-api-key"
      OPNSENSE_API_SECRET: "your-api-secret"
```

</details>

<details>
<summary><b>Other clients, a permanent install, a local checkout</b></summary>

Most clients take the standard `mcpServers` JSON shown for Claude Desktop. Some use `servers` instead.

To install once instead of fetching on each start:

```bash
uv tool install git+https://github.com/bigbatmanorg/opn-mcp@v0.1.0
```

then use `"command": "opn-mcp"`, `"args": []`.

From a checkout: `"command": "uv"`, `"args": ["run", "--directory", "/abs/path/opn-mcp", "opn-mcp"]`.

</details>

<!-- install:end -->

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

## License

BSD-2-Clause. The bundled index (`src/opn_mcp/data/opnsense-api.json.gz`) is generated from [opnsense/core](https://github.com/opnsense/core) and [opnsense/plugins](https://github.com/opnsense/plugins), BSD-2-Clause, Copyright Deciso B.V. and the OPNsense contributors.
