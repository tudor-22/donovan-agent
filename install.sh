#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# donovan — Universal Install Script
# Works on macOS, Linux, and Windows (Git Bash / WSL).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/tudor-22/donovan-cli/main/install.sh | bash
#
# Or clone and run locally:
#   git clone https://github.com/tudor-22/donovan-cli.git
#   cd donovan
#   bash install.sh
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${DONOVAN_REPO_URL:-https://github.com/tudor-22/donovan-cli}"
PROJECT="donovan-cli"

# ──────────────────────────────────────────────
# Colors
# ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}==>${NC} $1"; }
err()  { echo -e "${RED}==>${NC} $1"; }

# ──────────────────────────────────────────────
# Platform detection
# ──────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Linux*)   PLATFORM="linux" ;;
  Darwin*)  PLATFORM="macos" ;;
  MINGW*|MSYS*|CYGWIN*)  PLATFORM="windows" ;;
  *)        err "Unsupported OS: $OS. Please install manually: $REPO_URL"; exit 1 ;;
esac

log "Detected platform: $PLATFORM"

# ──────────────────────────────────────────────
# Python check
# ──────────────────────────────────────────────
PYTHON=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" &>/dev/null; then
    PYVER="$($candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    MAJOR="${PYVER%%.*}"
    MINOR="${PYVER#*.}"
    # Check for minimum version: Python 3.11+
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

# ──────────────────────────────────────────────
# Git check — auto-clone if not in a repo dir
# ──────────────────────────────────────────────
if [ ! -f "pyproject.toml" ]; then
  if command -v git &>/dev/null; then
    log "Cloning donovan..."
    git clone "$REPO_URL.git"
    cd "$PROJECT"
  else
    err "Git not found and no local copy detected."
    err "Install git or download the source from $REPO_URL"
    exit 1
  fi
fi

# ──────────────────────────────────────────────
# Create venv & install
# ──────────────────────────────────────────────
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

# ──────────────────────────────────────────────
# Optional browser support
# ──────────────────────────────────────────────
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

# ──────────────────────────────────────────────
# First-run setup
# ──────────────────────────────────────────────
echo ""
log "Running first-time setup wizard..."
echo -e "${YELLOW}You'll be asked to configure:${NC}"
echo "  - LLM provider (OpenAI, Anthropic, DeepSeek, Ollama, etc.)"
echo "  - API keys and model names"
echo "  - Tavily web search (optional)"
echo "  - Workspace folder and permission mode"
echo ""
donovanagent setup

# ──────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     donovan installed successfully!     ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "To activate the environment and start donovan:"
echo ""
case "$PLATFORM" in
  windows)
    echo "  source .venv/Scripts/activate"
    ;;
  *)
    echo "  source .venv/bin/activate"
    ;;
esac
echo "  donovanagent"
echo ""
echo "Or run a one-off command:"
echo "  donovanagent chat \"What can you do?\""
echo ""
