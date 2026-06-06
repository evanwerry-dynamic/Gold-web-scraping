#!/usr/bin/env bash
# Mad Scientist — one-command setup for Ubuntu 22.04 / Debian 12 VPS
# Usage: curl -fsSL <raw_url>/setup.sh | bash
set -euo pipefail

REPO="https://github.com/evanwerry-dynamic/Gold-web-scraping.git"
BRANCH="claude/twitter-post-feasibility-7gYpb"
INSTALL_DIR="/opt/mad-scientist"
GREEN="\033[0;32m"; RED="\033[0;31m"; NC="\033[0m"

info()  { echo -e "${GREEN}[+]${NC} $*"; }
error() { echo -e "${RED}[!]${NC} $*" >&2; }

# ── 1. Docker ──────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  info "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

if ! docker compose version &>/dev/null; then
  info "Installing Docker Compose plugin..."
  apt-get install -y docker-compose-plugin
fi

info "Docker $(docker --version | cut -d' ' -f3) ready"

# ── 2. Clone repo ──────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  info "Repo already cloned — pulling latest..."
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull origin "$BRANCH"
else
  info "Cloning repo to $INSTALL_DIR..."
  git clone --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── 3. .env setup ─────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  info "Created .env from example — edit it before going live:"
  info "  nano $INSTALL_DIR/.env"
  echo ""
  echo "  Required for paper trading (safe, no funds needed):"
  echo "    PAPER_TRADING=true          (already set)"
  echo "    INITIAL_BANKROLL=500        (simulated balance)"
  echo ""
  echo "  Required only for live trading:"
  echo "    POLYGON_PRIVATE_KEY=0x...   (your wallet private key)"
  echo "    CLOB_API_KEY=...            (from polymarket.com/api)"
  echo "    CLOB_SECRET=..."
  echo "    CLOB_PASS_PHRASE=..."
  echo ""
fi

# ── 4. Build & start ──────────────────────────────────────────────────────
info "Building Docker images (takes ~3 min first time)..."
docker compose build

info "Starting Mad Scientist in paper trading mode..."
docker compose up -d

# ── 5. Status ─────────────────────────────────────────────────────────────
sleep 5
PUBLIC_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "your-server-ip")

echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  Mad Scientist is running!             ${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo ""
echo "  Dashboard:   http://$PUBLIC_IP"
echo "  Bot logs:    docker compose -C $INSTALL_DIR logs -f bot"
echo "  All logs:    docker compose -C $INSTALL_DIR logs -f"
echo "  Stop:        docker compose -C $INSTALL_DIR down"
echo ""
echo "  ⚠️  Running in PAPER TRADING mode (no real orders)"
echo "  To go live: edit .env → set credentials → docker compose restart bot"
echo ""
docker compose ps
