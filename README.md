# ComputeNexa MCP server

<!-- mcp-name: io.github.computenexa-gpuInfrastructure/computenexa-mcp -->

Let your AI assistant find, launch and stop private GPUs on [ComputeNexa](https://computenexa.com). Ask it
*"Which GPUs with 48 GB or more are in stock?"*, *"Launch the cheapest one with Data Science for two hours"* or
*"Stop it"*: it uses your team's API key, shows you what each launch costs, and only spends money after you agree.

Works with Claude Desktop, Claude Code, Cursor, VS Code and other assistants that support the
[Model Context Protocol](https://modelcontextprotocol.io). It runs on your computer and talks to the ComputeNexa API;
your key stays in your own settings.

## Set up

1. In the ComputeNexa dashboard, open **API keys** and create a key:
   - **Look only** to browse GPUs, machines and spending;
   - **Launch and stop** to also launch, extend and stop machines.
2. Install [uv](https://docs.astral.sh/uv/) (it runs the server with `uvx`, no separate install needed).
3. Add the server to your assistant (below), with your key as `CNX_API_KEY`.

### Claude Code

```bash
claude mcp add --env CNX_API_KEY=cnxk_... --transport stdio computenexa -- uvx computenexa-mcp
```

Add `--scope user` to use it in every project.

### Claude Desktop

Settings → Developer → Edit config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "computenexa": {
      "command": "uvx",
      "args": ["computenexa-mcp"],
      "env": { "CNX_API_KEY": "cnxk_..." }
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json` (every project) or `.cursor/mcp.json` (one project): the same `mcpServers` entry as Claude
Desktop.

### VS Code

`.vscode/mcp.json`; VS Code asks for the key once and keeps it out of the file:

```json
{
  "inputs": [
    { "type": "promptString", "id": "cnx-key", "description": "ComputeNexa API key", "password": true }
  ],
  "servers": {
    "computenexa": {
      "type": "stdio",
      "command": "uvx",
      "args": ["computenexa-mcp"],
      "env": { "CNX_API_KEY": "${input:cnx-key}" }
    }
  }
}
```

## What it can do

| Tool | What it does | Key |
|---|---|---|
| `whoami` | Your email, team, balance, GPU limit and the key's permissions | any |
| `list_gpus` | GPUs with live prices and stock, cheapest first; filter by app, region, memory | any |
| `list_apps` | The apps a GPU starts with, their models, and what's installed | any |
| `list_machines` · `get_machine` | Your machines; how to connect (SSH, browser apps, API or endpoint address) | any |
| `get_usage` · `list_receipts` | Spending and GPU-hours; finished sessions and their cost | any |
| `list_projects` · `list_endpoints` | Saved projects; Endpoints and the machine each points at | any |
| `launch_machine` | Launch a GPU (quote first, then launch when you agree) | launch and stop |
| `extend_machine` | Add time (quote first) | launch and stop |
| `set_keep_running` | Renew a machine automatically until stopped (quote first) | launch and stop |
| `stop_machine` | End a machine now; unused time is refunded | launch and stop |
| `create_endpoint` · `get_endpoint_key` | A stable HTTPS address for your own server; its key | launch and stop |

A look-only key only gets the read tools (the first six rows).

## Safety

- **Nothing is charged without you.** `launch_machine`, `extend_machine` and `set_keep_running` first return a quote,
  e.g. *"NVIDIA RTX A6000 with the datascience app for 2 h: $1.44 charged now ($0.72/h); unused time is refunded if
  it stops early."* Only a second call with `confirm=true` and the quoted total goes ahead; if the price went up in
  between, it's refused.
- **A limit per call:** a launch, extension or keep-running renewal costing more than **$25** is refused. Change it
  with `CNX_MCP_MAX_LAUNCH_USD` (in dollars) in the server's `env`. Your team's GPU limit and prepaid balance still
  apply on top.
- **Your API key never appears in a tool result.** Endpoint keys are shown only when you create an endpoint or ask
  for its key.
- Tools that spend money or end a machine are marked as such, so your assistant asks before using them.

## Settings

| Variable | |
|---|---|
| `CNX_API_KEY` | Required. A ComputeNexa API key (`cnxk_…`) |
| `CNX_MCP_MAX_LAUNCH_USD` | Most one call may spend, in US dollars. Default `25` |
| `CNX_API_URL` | Only for testing against another ComputeNexa API |

## Develop

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

Built on the [ComputeNexa Python SDK](https://pypi.org/project/computenexa/) and the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

Questions: [support@computenexa.com](mailto:support@computenexa.com) · Security issues: see [SECURITY.md](SECURITY.md).
