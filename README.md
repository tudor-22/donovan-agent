# Donovan Agent

![Donovan Agent GitHub Banner](donovan2.png)

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](#windows-notes)

**Donovan Agent** is a terminal-native agentic assistant for developers. It can chat with an LLM provider, inspect and edit files, run shell commands, execute local Python, search the web, use browser automation, connect MCP servers, remember project context, manage checkpoints, and help work through real coding tasks from your command line.

<p align="center">
  <a href="https://buymeacoffee.com/tudor22">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?style=for-the-badge&logo=buymeacoffee" alt="Buy Me a Coffee" />
  </a>
  <a href="https://ko-fi.com/donovanai">
    <img src="https://img.shields.io/badge/Ko--fi-support-ff5e5b?style=for-the-badge&logo=kofi&logoColor=white" alt="Ko-fi" />
  </a>
</p>

## As Seen On

<p align="center"> <a href="https://launchllama.co?utm_source=badge&utm_medium=referral" target="_blank" rel="noopener"> <img src="https://speaktechenglish.com/wp-content/uploads/2026/04/Screenshot_2026-04-09_at_17.40.44-removebg-preview.png" alt="Featured on Launch Llama" height="40" /> </a> &nbsp;&nbsp;&nbsp; </a> &nbsp;&nbsp;&nbsp; <a href="https://news.google.com/" target="_blank" rel="noopener"> <img src="https://upload.wikimedia.org/wikipedia/commons/d/da/Google_News_icon.svg" alt="Google News" height="40" /> </a> &nbsp;&nbsp;&nbsp; </a> &nbsp;&nbsp;&nbsp; <a href="https://backlinklog.com/listing/tudoriustin.com?utm_source=backlinklog&utm_medium=badge" target="_blank" rel="noopener"> <img src="https://backlinklog.com/badge/tudoriustin.com.svg" alt="Listed on BacklinkLog" height="40" /> </a> &nbsp;&nbsp;&nbsp; <a href="https://www.shipit.buzz/products/donovan-agent?ref=badge" target="_blank" rel="noopener noreferrer"> <img src="https://www.shipit.buzz/api/products/donovan-agent/badge?theme=light" alt="Featured on Shipit" height="40" /> </a> </p> <br>


## Quick Install

macOS, Linux, Git Bash, or WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/tudor-22/donovan-agent/main/install.sh | bash
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/tudor-22/donovan-agent/main/install.ps1 | iex"
```

The scripts check Python, clone the repo if needed, create a virtual environment, install Donovan, add `donovan` to your user `PATH`, optionally install browser support, and run first-time setup.

## Quick Start

Launch the interactive agent:

```bash
donovan
```

Ask one question:

```bash
donovan chat "Explain this repository's structure."
```

Run a task:

```bash
donovan run "Inspect this project and summarize the main risks."
```

Search the web when Tavily is configured:

```bash
donovan search "latest Python release"
```

Check your environment:

```bash
donovan doctor
```

## Highlights

- Terminal-first interactive agent built with Rich and prompt_toolkit
- OpenAI-compatible provider support, plus Anthropic, DeepSeek, Qwen, LM Studio, and Ollama
- Local file tools for reading, writing, patching, listing, and searching
- Shell execution with platform-aware command handling
- Local Python execution for scripts and analysis
- Tavily web search integration
- Browser automation with optional Playwright support
- Browser Companion for working with already-open Chrome, Edge, Brave, Vivaldi, Opera, Arc, Chromium, and Firefox tabs without remote debugging
- MCP integration for external tools, resources, and prompts
- SQLite-backed sessions, messages, tool calls, audit logs, memories, and learned skills
- Planning, thinking summaries, scheduled tasks, checkpoints, subagents, and activity stream
- Product workflows for contracts, recipes, sandboxes, evals, workspace graphs, impact checks, PR summaries, watchers, inbox triage, routing, stats, handoffs, and recovery
- Natural-language auto-configuration for users who do not want to edit config files by hand
- Approval gates for risky tool use

## Providers

Donovan supports:

- OpenAI
- OpenAI-compatible APIs
- Ollama
- Anthropic
- DeepSeek
- Qwen
- LM Studio
- Custom local or hosted `/v1/chat/completions` endpoints

Configure providers during setup:

```bash
donovan setup
```

Or update later:

```bash
donovan model set
```

## Security Defaults

Donovan can read files, edit files, run commands, execute code, and connect tools. Its default security model is designed to keep those capabilities visible and permissioned.

- Workspaces must be approved before file tools are useful.
- Shell commands require approval outside full autonomy mode.
- Destructive commands are risk-classified.
- Writes and patches are audited.
- Tool calls and results are recorded in SQLite for traceability.
- Sensitive config display masks API keys.
- Blocked system paths protect sensitive OS locations.

Avoid `full_autonomy` mode on machines or folders where you do not want broad tool execution.

## Permission Modes

```text
readonly       Read approved files only; writes and execution are blocked.
review         Reads are allowed; writes, shell, and code require approval.
workspace      Review-style permissions optimized for approved project work.
full_autonomy  Fewer prompts, while destructive actions still need care.
```

Change mode inside the interactive agent:

```text
/mode workspace
```

Or with config:

```bash
donovan config set app.permission_mode workspace
```

## Core Commands

```text
donovan                         Launch interactive CLI agent
donovan setup                   Run first-time setup
donovan doctor                  Check environment and services
donovan model                   Show current provider and model
donovan model set               Change provider, API key env, base URL, and model
donovan config show             Show sanitized config
donovan config set KEY VALUE    Update config values
donovan tools                   List tools and enabled state
donovan tools enable TOOL       Enable a tool
donovan tools disable TOOL      Disable a tool
donovan sessions                List saved sessions
donovan skills                  List learned skills
donovan chat "prompt"           Run a one-shot prompt
donovan run "task"              Run an agent task
donovan search "query"          Run Tavily search directly
donovan permissions             Show approved paths and mode
donovan permissions add PATH    Grant folder access
donovan mcp list                List MCP servers
donovan mcp add <name>          Add an MCP server
donovan update                  Show update instructions
```

## Interactive Commands

```text
/help
/model
/model set
/tools
/search query
/workspace
/workspace add PATH
/workspace remove PATH
/mode readonly|review|workspace|full_autonomy
/new
/sessions
/skills
/history
/resume
/clear
/doctor
/config
/memory
/recall query
/context
/plan
/think
/activity
/backend
/browser
/checkpoint
/schedule
/subagents
/skill
/mcp
/timeline
/recipe
/sandbox
/profile
/contract
/eval
/graph
/impact
/pr
/watch
/inbox
/marketplace
/recover
/router
/stats
/handoff
/doctor-ai
/workspace-profile
/agent-test
/exit
```

## Docs By Goal

| Goal | Start here |
| --- | --- |
| Install Donovan | [Quick Install](#quick-install) |
| Configure a model | [Providers](#providers) |
| Learn the command surface | [Core Commands](#core-commands) |
| Understand permissions | [Security Defaults](#security-defaults) |
| Add external tools | [MCP Integration](#mcp-integration) |
| Run browser automation | [Browser Automation](#browser-automation) |
| Work from source | [Development Setup](#development-setup) |
| Debug environment issues | [Doctor](#doctor) |

## MCP Integration

Donovan supports the [Model Context Protocol](https://modelcontextprotocol.io) for connecting external servers that expose tools, resources, and prompts.

Useful commands:

```bash
donovan mcp list
donovan mcp add my-server --transport stdio -- node server.js
donovan mcp doctor
```

Full docs: [docs/mcp.md](docs/mcp.md)

Interactive MCP commands:

```text
/mcp
/mcp connect <name>
/mcp tools
/mcp resources
/mcp prompts
/mcp logs <name>
```

## Browser Automation

Donovan supports two browser workflows.

For users who want Donovan to work with the browser they already have open, use Browser Companion:

```text
/browser companion setup [chrome|edge|brave|vivaldi|opera|arc|chromium|firefox]
/browser companion start
/browser companion active
/browser companion snapshot
/browser companion tabs
/browser companion use <tab>
/browser companion click <selector>
/browser companion type <selector> <text>
/browser companion screenshot
```

The setup command generates local WebExtension folders and opens the browser extension page. After the extension is loaded once, Donovan can inspect and interact with active Chrome, Edge, Brave, Vivaldi, Opera, Arc, Chromium, and Firefox tabs without launching a new browser or requiring remote debugging flags. When Donovan is actively working in a browser, it brings the tab/window forward so the user can see what is happening; when the browser work is finished, it minimizes the browser again. Safari requires a separately packaged signed Safari Web Extension, so it is not supported by the unpacked companion yet.

For dedicated automation sessions, browser tools are optional and use Playwright. Install browser support with:

```bash
pip install -e ".[browser]"
python -m playwright install chromium
```

Then use browser commands inside Donovan:

```text
/browser open https://example.com
/browser text
/browser screenshot
/browser minimize
/browser close
```

When browser work starts, Donovan brings the browser forward. When browser work is complete, Donovan minimizes the browser instead of closing it or leaving it visibly open.

## Product Workflows

Donovan includes higher-level workflows for users who want results without learning every technical detail first:

- Contracts define a goal, allowed files, success criteria, and rollback expectations.
- Recipes capture reusable workflows and can be run later.
- Sandboxes let Donovan stage commands and changes before promotion.
- Profiles save workspace preferences and can be locked for repeatable project behavior.
- Evals and agent tests check whether Donovan is behaving correctly on repeatable tasks.
- Workspace graphs, impact checks, and PR summaries help understand code changes before publishing.
- Watchers and inbox triage keep track of follow-up work.
- Router, stats, handoff, recovery, and doctor-ai commands help Donovan configure and explain itself.

You can use slash commands directly, or describe what you want in normal language. For example:

```text
create a contract for fixing the browser companion setup
set up a recipe named release-check that runs tests and summarizes risks
configure router automatically for this project
```

## Memory, Skills, And Project Context

Donovan stores long-running state in SQLite:

- Sessions and message history
- Tool call records
- Audit logs
- Learned skills
- Memory records
- Project context
- Scheduled tasks
- Activity events

Learned skills can be listed with:

```bash
donovan skills
```

User skill files live inside the workspace at:

```text
.DonovanAgent/skills/
```

## Configuration

Show sanitized config:

```bash
donovan config show
```

Set a config value:

```bash
donovan config set search.enabled true
```

Minimal provider-related settings are managed by:

```bash
donovan model set
```

Config and data locations depend on the platform. Run `donovan doctor` to see the exact paths Donovan is using.

## Doctor

Run:

```bash
donovan doctor
```

Doctor checks Python, OS, terminal encoding, config, writable data directories, SQLite, provider connectivity, Tavily, shell backend, Git, ripgrep, Node, workspace existence, approved paths, browser automation, checkpoints, skills, and registered tools.

## Development Setup

Clone the repo:

```bash
git clone https://github.com/tudor-22/donovan-agent.git
cd donovan-agent
```

Create an environment and install in editable mode:

```bash
python -m venv .venv
```

macOS, Linux, Git Bash, or WSL:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Run tests:

```bash
python -m pytest
```

## Project Layout

```text
src/donovanagent/
  agent/          Agent loop, prompts, planning, compaction, tool protocol
  activity/       Activity stream events and rendering
  browser/        Browser service and browser tool support
  checkpoints/    Workspace checkpoint management
  config/         Config schema, paths, setup wizard, config manager
  execution/      Local, Docker, and SSH execution backends
  mcp/            MCP config, transports, registry, security, UI
  memory/         SQLite database, recall, summaries, learned skills
  planning/       Plan models and manager
  providers/      LLM provider implementations
  scheduler/      Scheduled task models and service
  security/       Path permissions and command risk detection
  skills/         Skill manager, learner, ranker, models
  subagents/      Subagent models, roles, manager
  tools/          Built-in tools and tool registry
  ui/             Rich rendering and prompt UI
  utils/          Shell, platform, logging, JSON, errors, web search helpers
tests/            Unit and integration-style tests
docs/             Project documentation
```

## Windows Notes

Donovan supports native Windows. Shell resolution tries:

1. Git Bash
2. WSL
3. PowerShell or PowerShell Core
4. `cmd.exe`

Do not assume Unix commands exist unless Git Bash or WSL is selected.

## macOS And Linux Notes

Donovan uses your default shell when available, then Bash, Zsh, or Sh fallbacks.

Typical config paths:

```text
macOS: ~/Library/Application Support/DonovanAgent
Linux: ~/.config/DonovanAgent
```

Run `donovan doctor` for the exact paths on your machine.

## Security Warning

Donovan is intentionally capable. It can edit files, run commands, execute code, and connect external tools. Review tool approvals carefully, keep secrets out of prompts, and use workspace permissions thoughtfully.

## Known Limitations

- Python execution is timeout-limited but not a full security sandbox.
- Patch editing uses explicit search and replace, so the target text must be findable.
- Tool-calling turns use non-streaming provider calls when needed so tool calls can be parsed reliably.
- Browser automation requires optional Playwright installation.

## Community

Contributions, bug reports, and ideas are welcome.

- Open issues in the GitHub repo.
- Run tests before submitting changes.
- Keep security-sensitive behavior explicit and auditable.

## License

Apache License 2.0. See [LICENSE](LICENSE).

Copyright 2026 Donovan AI.
