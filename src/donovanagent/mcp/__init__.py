"""MCP (Model Context Protocol) integration for Donovan Agent.

This subsystem provides full Model Context Protocol support:
- Multi-scope MCP server configuration (user/project/local)
- stdio, HTTP/Streamable HTTP, and SSE (legacy) transports
- JSON-RPC client for tool/resource/prompt discovery and execution
- Dynamic tool registration with Donovan's tool system
- Trust store and risk classification for security
- In-session /mcp slash command
- @ resource mentions
- MCP prompts as slash commands
"""

from __future__ import annotations
