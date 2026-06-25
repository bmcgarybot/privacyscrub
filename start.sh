#!/usr/bin/env bash
# ============================================================================
# PrivacyScrub — Linux/macOS Launcher
# Self-Hosted Privacy Removal Platform
#
# Usage:
#   chmod +x start.sh
#   ./start.sh
#
# Options:
#   PORT=8080 ./start.sh        — Run on custom port
#   FLASK_DEBUG=1 ./start.sh    — Enable debug mode
# ============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/venv"
REQUIREMENTS="${SCRIPT_DIR}/requirements.txt"
PORT="${PORT:-5000}"
HOST="${HOST:-0.0.0.0}"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║          🛡️  PrivacyScrub v1.0.0                     ║"
echo "  ║          Self-Hosted Privacy Removal Platform        ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check Python version
echo -e "${YELLOW}[1/4] Checking Python...${NC}"
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo -e "${RED}ERROR: Python 3.10+ is required but not found.${NC}"
    echo "Install Python from https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo -e "${RED}ERROR: Python 3.10+ required. Found: $PY_VERSION${NC}"
    exit 1
fi
echo -e "  Found Python ${PY_VERSION} ✓"

# Create virtual environment if needed
echo -e "${YELLOW}[2/4] Setting up virtual environment...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
    echo -e "  Virtual environment created ✓"
else
    echo -e "  Virtual environment exists ✓"
fi

# Activate venv
source "${VENV_DIR}/bin/activate"

# Install/upgrade dependencies
echo -e "${YELLOW}[3/4] Installing dependencies...${NC}"
pip install --upgrade pip --quiet 2>/dev/null
pip install -r "$REQUIREMENTS" --quiet 2>/dev/null
echo -e "  Dependencies installed ✓"

# Create required directories
echo -e "${YELLOW}[4/4] Preparing directories...${NC}"
mkdir -p "${SCRIPT_DIR}/templates"
mkdir -p "${SCRIPT_DIR}/static"
mkdir -p "${SCRIPT_DIR}/legal_templates/state_specific"
mkdir -p "${SCRIPT_DIR}/reports"
echo -e "  Directories ready ✓"

# Start the server
echo ""
echo -e "${GREEN}Starting PrivacyScrub...${NC}"
echo -e "  Dashboard:  ${CYAN}http://localhost:${PORT}${NC}"
echo -e "  API:        ${CYAN}http://localhost:${PORT}/api/health${NC}"
echo -e "  API Docs:   ${CYAN}http://localhost:${PORT}/api-docs${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the server.${NC}"
echo ""

cd "$SCRIPT_DIR"
$PYTHON app.py
