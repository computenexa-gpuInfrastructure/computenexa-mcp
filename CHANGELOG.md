# Changelog

## 0.1.0

- First release: a stdio MCP server named "ComputeNexa" on the `computenexa` SDK (0.3.0+).
- Read tools: `whoami`, `list_gpus`, `list_apps`, `list_machines`, `get_machine`, `get_usage`, `list_receipts`,
  `list_projects`, `list_endpoints`.
- Tools that spend or change (launch-and-stop keys only): `launch_machine`, `extend_machine`, `set_keep_running`,
  `stop_machine`, `create_endpoint`, `get_endpoint_key`.
- Quote, then confirm with the quoted total, for everything that costs money; a per-call limit
  (`CNX_MCP_MAX_LAUNCH_USD`, default $25); structured results with output schemas; read-only keys see only the read
  tools.
