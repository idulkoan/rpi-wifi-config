#!/bin/bash
# wifi-config uninstaller

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo ""
echo "================================================"
echo "  wifi-config uninstaller"
echo "================================================"
echo ""
echo "  This will:"
echo "  - Stop and remove the Docker container"
echo "  - Remove the polkit rule"
echo "  - Remove your .env credentials file"
echo ""
read -p "  Continue? [y/N]: " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "  Aborted."
    exit 0
fi
echo ""

# Stop and remove container
if [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    info "Stopping container..."
    cd "$SCRIPT_DIR"
    docker compose down 2>/dev/null && success "Container stopped and removed" || warn "Container was not running"
fi

# Remove polkit rule
if [ -f /etc/polkit-1/rules.d/10-wifi-config.rules ]; then
    info "Removing polkit rule..."
    sudo rm /etc/polkit-1/rules.d/10-wifi-config.rules
    sudo systemctl restart polkit
    success "polkit rule removed"
else
    info "polkit rule not found — skipping"
fi

# Remove credentials
if [ -f "$SCRIPT_DIR/.env" ]; then
    info "Removing .env..."
    rm "$SCRIPT_DIR/.env"
    success ".env removed"
fi

echo ""
echo "================================================"
echo -e "  ${GREEN}Uninstall complete.${NC}"
echo "================================================"
echo ""
echo "  The repo files are intact and ready to reinstall."
echo "  To reinstall: bash install.sh"
echo "  To fully remove: cd .. && rm -rf wifi-config/"
echo ""
