# mcp-tools

Optional supplementary MCP tool scripts (e.g. `current-datetime.js`, `mcp-remote`).

This folder is **optional**. The installer will skip tools it cannot find here and log a notice.

## current-datetime

A minimal Node.js MCP server that returns the current date/time. If you want this tool:

```bash
cd mcp-tools
npm init -y
npm install @modelcontextprotocol/sdk
# then place your current-datetime.js here
```

## mcp-remote

Optional proxy for remote MCP servers (e.g. Tavily web search). Install with:

```bash
cd mcp-tools
npm install mcp-remote
```

Then add your API key to `~/.lmstudio/mcp.json` under the `tavily-remote` entry.

Neither tool is required for core RAG + agent functionality.
