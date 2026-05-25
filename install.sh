#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# donovan - Universal Install Script
# Works on macOS, Linux, and Windows (Git Bash / WSL).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/tudor-22/donovan-agent/main/install.sh | bash
#
# Or clone and run locally:
#   git clone https://github.com/tudor-22/donovan-agent.git
#   cd donovan
#   bash install.sh
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${DONOVAN_REPO_URL:-https://github.com/tudor-22/donovan-agent}"
PROJECT="donovan-agent"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}==>${NC} $1"; }
err()  { echo -e "${RED}==>${NC} $1"; }

path_contains() {
  case ":$PATH:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

append_line_once() {
  local file="$1"
  local line="$2"

  mkdir -p "$(dirname "$file")"
  touch "$file"
  if ! grep -Fqx "$line" "$file"; then
    printf '\n%s\n' "$line" >> "$file"
  fi
}

add_shell_path() {
  local bin_dir="$1"
  local export_line="export PATH=\"$bin_dir:\$PATH\""

  if ! path_contains "$bin_dir"; then
    export PATH="$bin_dir:$PATH"
  fi

  case "${SHELL:-}" in
    */zsh)
      append_line_once "$HOME/.zshrc" "$export_line"
      ;;
    */bash)
      append_line_once "$HOME/.bashrc" "$export_line"
      append_line_once "$HOME/.bash_profile" "$export_line"
      ;;
    *)
      append_line_once "$HOME/.profile" "$export_line"
      ;;
  esac
}

create_unix_shim() {
  local name="$1"
  local target="$2"
  local bin_dir="${DONOVAN_BIN_DIR:-$HOME/.local/bin}"
  local shim="$bin_dir/$name"

  mkdir -p "$bin_dir"
  cat > "$shim" <<EOF
#!/usr/bin/env sh
exec "$target" "\$@"
EOF
  chmod +x "$shim"
  add_shell_path "$bin_dir"
}

add_windows_user_path() {
  local bin_dir="$1"

  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\
\$dir = [System.IO.Path]::GetFullPath('$bin_dir'); \
\$userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); \
\$parts = @(); \
if (\$userPath) { \$parts = \$userPath -split ';' | Where-Object { \$_ -and \$_.Trim() } }; \
\$alreadySet = \$false; \
foreach (\$part in \$parts) { \
  if ([string]::Equals([System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables(\$part)), \$dir, [System.StringComparison]::OrdinalIgnoreCase)) { \$alreadySet = \$true; break } \
}; \
if (-not \$alreadySet) { \
  \$newPath = if (\$userPath) { \"\$userPath;\$dir\" } else { \$dir }; \
  [Environment]::SetEnvironmentVariable('Path', \$newPath, 'User') \
}" >/dev/null
  else
    warn "Could not update the Windows user PATH because powershell.exe was not found."
  fi
}

create_windows_cmd_shim() {
  local name="$1"
  local target="$2"
  local win_local_appdata="${LOCALAPPDATA:-}"

  if [ -z "$win_local_appdata" ] && command -v powershell.exe >/dev/null 2>&1; then
    win_local_appdata="$(powershell.exe -NoProfile -Command '[Environment]::GetFolderPath("LocalApplicationData")' | tr -d '\r')"
  fi

  if [ -z "$win_local_appdata" ]; then
    warn "Could not find LOCALAPPDATA; skipping PowerShell/cmd launcher for $name."
    return
  fi

  local bin_dir="$win_local_appdata/Programs/donovan/bin"
  local shim="$bin_dir/$name.cmd"
  mkdir -p "$bin_dir"
  printf '@echo off\r\n"%s" %%*\r\n' "$target" > "$shim"
  add_windows_user_path "$bin_dir"
}

OS="$(uname -s)"
case "$OS" in
  Linux*)   PLATFORM="linux" ;;
  Darwin*)  PLATFORM="macos" ;;
  MINGW*|MSYS*|CYGWIN*)  PLATFORM="windows" ;;
  *)        err "Unsupported OS: $OS. Please install manually: $REPO_URL"; exit 1 ;;
esac

log "Detected platform: $PLATFORM"

PYTHON=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYVER="$($candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    MAJOR="${PYVER%%.*}"
    MINOR="${PYVER#*.}"
    if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 11 ]; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  err "Python 3.11+ is required but not found."
  err "Install it from: https://www.python.org/downloads/"
  exit 1
fi
log "Found $($PYTHON --version)"

if [ ! -f "pyproject.toml" ]; then
  if command -v git >/dev/null 2>&1; then
    log "Cloning donovan..."
    git clone "$REPO_URL.git"
    cd "$PROJECT"
  else
    err "Git not found and no local copy detected."
    err "Install git or download the source from $REPO_URL"
    exit 1
  fi
fi

log "Creating virtual environment..."
"$PYTHON" -m venv .venv

case "$PLATFORM" in
  windows)
    source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate
    ;;
  *)
    source .venv/bin/activate
    ;;
esac

log "Installing donovan..."
pip install --upgrade pip
pip install -e .

case "$PLATFORM" in
  windows)
    VENV_BIN="$(pwd -W 2>/dev/null || pwd)/.venv/Scripts"
    create_windows_cmd_shim "donovan" "$VENV_BIN/donovan.exe"
    create_windows_cmd_shim "donovanagent" "$VENV_BIN/donovanagent.exe"
    create_unix_shim "donovan" "$PWD/.venv/Scripts/donovan.exe"
    create_unix_shim "donovanagent" "$PWD/.venv/Scripts/donovanagent.exe"
    ;;
  *)
    create_unix_shim "donovan" "$PWD/.venv/bin/donovan"
    create_unix_shim "donovanagent" "$PWD/.venv/bin/donovanagent"
    ;;
esac

echo ""
echo -e "${CYAN}donovan includes optional browser automation features.${NC}"
echo -e "${CYAN}These require Playwright and a browser engine download (~150 MB).${NC}"
read -r -p "Install browser support? [y/N] " INSTALL_BROWSER
if [[ "$INSTALL_BROWSER" =~ ^[Yy]$ ]]; then
  log "Installing browser dependencies..."
  pip install -e ".[browser]"
  python -m playwright install chromium
  log "Browser support installed."
fi

echo ""
log "Running first-time setup wizard..."
echo -e "${YELLOW}You'll be asked to configure:${NC}"
echo "  - LLM provider (OpenAI, Anthropic, DeepSeek, Ollama, etc.)"
echo "  - API keys and model names"
echo "  - Tavily web search (optional)"
echo "  - Workspace folder and permission mode"
echo ""
donovan setup

echo ""
echo -e "${GREEN}+-----------------------------------------+${NC}"
echo -e "${GREEN}|     donovan installed successfully!     |${NC}"
echo -e "${GREEN}+-----------------------------------------+${NC}"
echo ""
echo "To start donovan:"
echo "  donovan"
echo ""
echo "Or run a one-off command:"
echo "  donovan chat \"What can you do?\""
echo ""
echo "If this terminal was open before installation, restart it if your shell has not refreshed PATH yet."
