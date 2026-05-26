# MCP (Model Context Protocol) Integration

Donovan Agent v0.1.12 supports the [Model Context Protocol](https://modelcontextprotocol.io) — an open standard for connecting AI agents with external tools, data sources, and prompts. This lets you extend Donovan with capabilities from any MCP-compatible server.

## Overview

MCP servers provide three capability types:

- **Tools** — callable functions exposed to the model (e.g., a GitHub issue search, a database query)
- **Resources** — data that can be read and attached to conversations (e.g., documentation files, schemas)
- **Prompts** — reusable prompt templates (e.g., "summarize this project", "generate a report")

Donovan supports three MCP transport types:

| Transport | Type value | How it works |
|-----------|-----------|--------------|
| **stdio** | `stdio` | Server runs as a subprocess; JSON-RPC over stdin/stdout |
| **HTTP / Streamable HTTP** | `http` | Server runs as an HTTP endpoint; POST requests |
| **SSE** (legacy) | `sse` | Deprecated server-sent events transport |

## Configuration

MCP server configs can be stored in three scopes (higher priority overrides lower):

| Scope | File | Use case |
|-------|------|----------|
| **user** | `~/.donovan/mcp.json` | Personal servers used across all projects |
| **project** | `.mcp.json` (in project root) | Shared via version control |
| **local** | `.donovan/mcp.local.json` | Local overrides, not committed |

### JSON format

```json
{
  "mcpServers": {
    "my-server": {
      "type": "stdio",
      "command": "node",
      "args": ["path/to/server.js"],
      "env": {
        "API_KEY": "${MY_API_KEY}",
        "PATH": "/usr/local/bin"
      },
      "enabled": true,
      "trust": "ask",
      "timeout_ms": 60000,
      "max_output_tokens": 25000,
      "description": "My custom MCP server"
    },
    "remote-api": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${API_TOKEN}"
      },
      "enabled": true,
      "trust": "trusted",
      "timeout_ms": 30000
    }
  }
}
```

### Field reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | `stdio`, `http`, `sse` | `stdio` | Transport protocol |
| `command` | string | `""` | Executable path (stdio only) |
| `args` | string[] | `[]` | Command-line arguments (stdio only) |
| `env` | object | `{}` | Environment variables (`${VAR}` expansion supported) |
| `url` | string | `""` | Server URL (HTTP/SSE only) |
| `headers` | object | `{}` | HTTP headers (HTTP/SSE only) |
| `enabled` | bool | `true` | Whether the server is active |
| `trust` | `ask`, `trusted`, `blocked` | `ask` | Initial trust level |
| `timeout_ms` | int | `60000` | Connection/request timeout |
| `max_output_tokens` | int | `25000` | Max output tokens per tool call |
| `description` | string | `""` | Human-readable description |
| `oauth` | object | `null` | OAuth config (reserved for future use) |

## CLI Commands

```
donovan mcp list                              List all configured servers
donovan mcp get <name>                        Show server details
donovan mcp add <name> --command <cmd>        Add a stdio server
donovan mcp add-json <name> <json>            Add a server from JSON config
donovan mcp remove <name>                     Remove a server
donovan mcp enable <name>                     Enable a server
donovan mcp disable <name>                    Disable a server
donovan mcp trust <name>                      Trust a server (skip approval prompts)
donovan mcp block <name>                      Block a server
donovan mcp reset-project-choices             Reset all project-level trust decisions
donovan mcp doctor                            Check MCP configuration and connectivity
```

### Examples

```bash
# Add a Node.js MCP server
donovan mcp add my-server --command node --args "server.js" --env "KEY=${VALUE}"

# Add an HTTP MCP server
donovan mcp add-json remote '{
  "type": "http",
  "url": "https://mcp.example.com",
  "headers": {"Authorization": "Bearer ${TOKEN}"}
}'

# List configured servers
donovan mcp list

# Enable/disable
donovan mcp enable my-server
donovan mcp disable my-server
```

## In-Session `/mcp` Commands

In the interactive Donovan session, use the `/mcp` slash command:

```
/mcp                              Show status of all MCP servers
/mcp list                         Alias for status
/mcp connect <name>               Connect to a server
/mcp disconnect <name>            Disconnect a server
/mcp restart <name>               Restart a server connection
/mcp trust <name>                 Trust a server
/mcp block <name>                 Block a server
/mcp tools [name]                 List tools (all or from a specific server)
/mcp resources [name]             List resources (all or from a specific server)
/mcp prompts [name]               List prompts (all or from a specific server)
/mcp logs <name>                  Show recent log output from a server
/mcp help                         Show MCP help
```

## @ Resource Mentions

In any message to Donovan, you can reference MCP resources using the `@` mention syntax:

```
@server_name:protocol://path
```

Examples:

```
What's the status of @github:issue://123?
Read the docs at @docs:file://api/authentication
Show me the schema for @postgres:schema://users
```

When the server is connected, Donovan fetches the resource content and attaches it to the conversation context. If the server is configured but not connected, a note is shown instead.

## Security Model

### Trust Levels

Each server has a trust level that controls whether it can connect without user approval:

- **ask** (default) — prompt the user on first connection
- **trusted** — automatically connect without prompting
- **blocked** — prevent connection entirely

### Config Change Detection

When a server's configuration changes (command, args, URL, or env/header keys), the trust decision is invalidated. The user must re-trust the server before it can connect. This prevents a modified server config from running without review.

### Risk Classification

MCP tools are classified by risk level based on their name, description, and input schema:

| Risk Level | Label | Examples | Requires Approval |
|-----------|-------|---------|:---:|
| Low | read-only | `list_files`, `search`, `get` | No |
| Medium | write | `create_record`, `update`, `send` | Yes |
| High | destructive | `delete_table`, `drop`, `purge` | Yes |
| High | shell/command | `exec_command`, `run` | Yes |
| High | network | `send_email`, `post_message` | Yes |
| Medium | unknown | Unrecognized patterns | Yes |

### Trust Store

Trust decisions are persisted in two locations:

- **User scope**: `~/.donovan/mcp_trust.json`
- **Project scope**: `.donovan/mcp_trust.local.json`

## Deferred Tool Loading

When more than 30 MCP tools are registered (from all connected servers), Donovan uses deferred loading: only the `search_mcp_tools` built-in tool and the first 5 tools are injected into the model's tool list. The remaining tools are discoverable at runtime through the `search_mcp_tools` tool. The threshold is configurable via `mcp.defer_tools_above` in the Donovan config.

## Configuration Reference

In Donovan's `config.yaml`, the MCP section is:

```yaml
mcp:
  enabled: true                    # Master switch
  tool_search: true                # Enable search_mcp_tools built-in
  defer_tools_above: 30            # Threshold for deferred loading
  always_load_servers: []          # Servers that always load fully
  always_load_tools: []            # Tools always included in schema
```

## Windows Notes

On Windows, stdio servers using `npx`, `npm`, `yarn`, or `pnpm` are automatically wrapped with `cmd /c` for compatibility. If you encounter issues with a stdio server, verify that its command is available in your PATH.

## Troubleshooting

```bash
# Check MCP configuration and connectivity
donovan mcp doctor

# View server logs
# Inside interactive session:
/mcp logs my-server

# Reset all project-level trust decisions (forces re-prompt)
donovan mcp reset-project-choices
```

### Common Issues

- **Server exits immediately** — check that the command and args are correct and the executable is in PATH
- **Connection refused (HTTP)** — verify the URL and network connectivity
- **Trust errors** — if a server config changed, re-trust it with `/mcp trust <name>` or `donovan mcp trust <name>`
- **Windows npx not found** — install Node.js and ensure npx is in your PATH
