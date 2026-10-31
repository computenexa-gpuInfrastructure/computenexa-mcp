# Use ComputeNexa from your AI assistant

*Draft for the dashboard help page at /help/mcp.*

Your AI assistant (Claude, Cursor, VS Code and others that support MCP, the Model Context Protocol) can find,
launch and stop GPUs for you. Ask it things like:

- *"Which GPUs with at least 48 GB are in stock, and what do they cost?"*
- *"Launch the cheapest one with Data Science for two hours, with my titanic project."*
- *"How do I connect to it?"* · *"Add an hour."* · *"Stop it."*
- *"What did we spend on GPUs this month?"*

It uses an API key from your team and runs on your own computer: the key stays in your assistant's settings.

## 1. Create an API key

Dashboard → **API keys** → **Create key**:

| Key | The assistant can |
|---|---|
| **Look only** | See GPUs and prices, your machines, spending, projects and endpoints |
| **Launch and stop** | Also launch, extend, keep running and stop machines, and create endpoints |

Start with **Look only** if you just want answers. The key acts as you and spends your team's credits.

## 2. Add ComputeNexa to your assistant

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) installed; it downloads and runs the server.
Replace `cnxk_...` with your key.

**Claude Code** (in a terminal):

```bash
claude mcp add --env CNX_API_KEY=cnxk_... --transport stdio computenexa -- uvx computenexa-mcp
```

**Claude Desktop:** Settings → Developer → Edit config, then add:

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

Restart Claude Desktop.

**Cursor:** the same `mcpServers` entry in `~/.cursor/mcp.json`.

**VS Code:** in `.vscode/mcp.json` (VS Code asks for the key and keeps it out of the file):

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

Check it works: ask *"Who am I on ComputeNexa?"*

## 3. What happens when it spends money

- **You always see the price first.** Before a launch, an extension or Keep running, the assistant gets a quote
  (*"NVIDIA RTX A6000 with Data Science for 2 h: $1.44 charged now; unused time is refunded if it stops early"*) and
  shows it to you. It goes ahead only after you agree.
- **A limit per request:** anything costing more than **$25** in one go is refused. To change it, add
  `"CNX_MCP_MAX_LAUNCH_USD": "50"` (dollars) next to `CNX_API_KEY`.
- Your team's GPU limit and prepaid balance apply as everywhere else.
- **Stopping** a machine deletes files outside a project; unused time is refunded.

## Questions

- **Is my key sent anywhere else?** No: the server runs on your computer and talks only to the ComputeNexa API.
  It never shows the key in its answers.
- **It says "This API key may only look."** Create a **Launch and stop** key and use it instead.
- **A key leaked?** Revoke it under **API keys** right away; it stops working immediately.

More: [the server on GitHub](https://github.com/computenexa-gpuInfrastructure/computenexa-mcp) ·
[support@computenexa.com](mailto:support@computenexa.com)
