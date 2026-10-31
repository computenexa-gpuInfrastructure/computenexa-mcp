# Security policy

## Reporting a vulnerability

Please report security issues privately to **support@computenexa.com**, with "Security" in the subject.
Don't open a public GitHub issue for a security problem.

Include what you found, how to reproduce it, and what an attacker could do with it. We aim to acknowledge reports
within 3 business days and will keep you updated until it's resolved. We're grateful for responsible disclosure
and are happy to credit you once a fix is released, if you'd like.

This covers this MCP server (the `computenexa-mcp` package) and the ComputeNexa API it talks to
(`https://api.computenexa.com`).

## Supported versions

Security fixes go into the latest release. `uvx computenexa-mcp` runs the latest one; to refresh a cached copy:

```bash
uvx --refresh computenexa-mcp --version
```

## Keys and assistants

- Give an assistant the least it needs: a **Look only** API key unless it should launch and stop machines.
- The server never puts its API key in a tool result, and refuses any single launch, extension or renewal above
  `CNX_MCP_MAX_LAUNCH_USD` (default $25). Your team's GPU limit and prepaid balance apply on top.
- If a key may have been exposed (committed, pasted in a chat, shared in a log), revoke it right away in the
  dashboard under **API keys** and create a new one. Revoking takes effect immediately.
