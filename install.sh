#!/bin/bash
# wifi-config installer
# Supports: Raspberry Pi OS Bullseye/Bookworm (Debian 11/12) with NetworkManager
# Run as the pi user (not root). Will use sudo where needed.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="$(whoami)"
APP_UID="$(id -u)"
HOSTNAME="$(hostname)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo ""
echo "================================================"
echo "  wifi-config installer"
echo "================================================"
echo ""

# ── Phase 1: Compatibility checks ────────────────────────────────────────────

info "Checking system compatibility..."

if [ "$APP_UID" -eq 0 ]; then
    fail "Do not run this script as root. Run as your normal user (e.g. pi)."
fi

if ! grep -q 'ID=debian\|ID_LIKE=debian' /etc/os-release 2>/dev/null; then
    fail "This script requires Debian or Raspberry Pi OS."
fi

VERSION_ID=$(grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '"')
VERSION_MAJOR="${VERSION_ID%%.*}"
case "$VERSION_MAJOR" in
    11)
        success "OS: Debian 11 Bullseye"
        ;;
    12)
        success "OS: Debian 12 Bookworm"
        ;;
    *)
        fail "Requires Debian 11 or 12. Found VERSION_ID=$VERSION_ID"
        ;;
esac

info "Ensuring NetworkManager is installed and active..."
if ! command -v nmcli &>/dev/null; then
    warn "NetworkManager is not installed. Installing now..."
    sudo apt-get update
    sudo apt-get install -y network-manager
fi

sudo systemctl enable NetworkManager >/dev/null 2>&1 || true
sudo systemctl start NetworkManager

if ! systemctl is-active --quiet NetworkManager; then
    fail "NetworkManager failed to start. Check: sudo systemctl status NetworkManager"
fi
success "NetworkManager is active"

if systemctl is-active --quiet dhcpcd 2>/dev/null; then
    warn "dhcpcd is active and conflicts with NetworkManager. Disabling dhcpcd..."
    sudo systemctl disable --now dhcpcd
fi

if systemctl is-enabled --quiet dhcpcd 2>/dev/null; then
    warn "dhcpcd is enabled at boot. Disabling it..."
    sudo systemctl disable dhcpcd
fi

if systemctl is-active --quiet dhcpcd 2>/dev/null; then
    fail "dhcpcd is still active after disable attempt.
    Stop it manually with: sudo systemctl disable --now dhcpcd"
fi
success "dhcpcd is not active"

WLAN0_NM_STATE=$(nmcli -t -f DEVICE,STATE device status | awk -F: '$1=="wlan0"{print $2}')
if [ -z "$WLAN0_NM_STATE" ]; then
    fail "wlan0 not visible to NetworkManager. Is Wi-Fi hardware present and enabled?"
fi

if [ "$WLAN0_NM_STATE" = "unmanaged" ]; then
    warn "wlan0 is unmanaged. Updating NetworkManager config for ifupdown..."
    sudo mkdir -p /etc/NetworkManager/conf.d
    sudo tee /etc/NetworkManager/conf.d/10-wifi-config-managed.conf >/dev/null <<'EOF'
[ifupdown]
managed=true
EOF
    sudo systemctl restart NetworkManager
    sleep 1
    WLAN0_NM_STATE=$(nmcli -t -f DEVICE,STATE device status | awk -F: '$1=="wlan0"{print $2}')
fi

if [ "$WLAN0_NM_STATE" = "unmanaged" ]; then
    fail "wlan0 is still unmanaged by NetworkManager.
    Check: nmcli device status"
fi
success "wlan0 is managed by NetworkManager (state: $WLAN0_NM_STATE)"

if ! ip link show wlan0 &>/dev/null; then
    fail "wlan0 interface not found. Is Wi-Fi hardware present and enabled?"
fi
success "wlan0 exists"

if rfkill list wifi 2>/dev/null | grep -q "Hard blocked: yes"; then
    fail "Wi-Fi is hard-blocked (hardware switch). Unblock it first: rfkill unblock wifi"
fi
if rfkill list wifi 2>/dev/null | grep -q "Soft blocked: yes"; then
    warn "Wi-Fi is soft-blocked. Unblocking now..."
    sudo rfkill unblock wifi
fi
success "Wi-Fi is not rfkill-blocked"

if [ ! -S /run/dbus/system_bus_socket ]; then
    fail "D-Bus system socket not found at /run/dbus/system_bus_socket"
fi
success "D-Bus socket exists"

if ! command -v docker &>/dev/null; then
    fail "Docker is not installed.

    Install it with:
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    then log out and back in, and re-run this installer."
fi
if ! systemctl is-active --quiet docker; then
    fail "Docker is installed but not running. Start it: sudo systemctl start docker"
fi
if ! docker info &>/dev/null; then
    fail "Cannot connect to Docker. Make sure your user is in the docker group:
    sudo usermod -aG docker $USER
    then log out and back in."
fi
success "Docker is running and accessible"

if ! command -v pkaction &>/dev/null; then
    fail "polkit is not installed."
fi
POLKIT_VERSION=$(pkaction --version 2>&1 | grep -oP '\d+' | head -1)
if [ "$POLKIT_VERSION" -lt 105 ]; then
    fail "polkit version $POLKIT_VERSION is too old. Need >= 0.105."
fi
success "polkit version $POLKIT_VERSION"

# Verify repo is complete
for f in \
    "$SCRIPT_DIR/docker-compose.yml" \
    "$SCRIPT_DIR/app/Dockerfile" \
    "$SCRIPT_DIR/app/requirements.txt" \
    "$SCRIPT_DIR/app/app.py" \
    "$SCRIPT_DIR/app/templates/index.html" \
    "$SCRIPT_DIR/polkit/10-wifi-config.rules"
do
    if [ ! -f "$f" ]; then
        fail "Missing file: $f
    The repo appears to be incomplete. Try: git clone again."
    fi
done
success "Repo is complete"

echo ""
info "All compatibility checks passed."
echo ""

# ── Phase 2: Credentials ──────────────────────────────────────────────────────

ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    info ".env file already exists. Skipping credential setup."
    info "To change credentials, edit $ENV_FILE and run: docker compose up -d"
else
    echo "================================================"
    echo "  Set up web UI credentials"
    echo "================================================"
    echo ""
    echo "  These are the username and password you will use"
    echo "  to log into the Wi-Fi configuration web interface."
    echo ""

    read -p "  Web UI username [pi]: " WIFI_USER
    WIFI_USER="${WIFI_USER:-pi}"

    while true; do
        read -s -p "  Web UI password: " WIFI_PASS
        echo ""
        read -s -p "  Confirm password: " WIFI_PASS2
        echo ""
        if [ "$WIFI_PASS" = "$WIFI_PASS2" ]; then
            break
        fi
        echo "  Passwords do not match. Try again."
    done

    SECRET_KEY=$(tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 48)

    cat > "$ENV_FILE" << ENVEOF
SECRET_KEY=${SECRET_KEY}
WIFI_USER=${WIFI_USER}
WIFI_PASS=${WIFI_PASS}
HOSTNAME_DISPLAY=${HOSTNAME}
ENVEOF

    chmod 600 "$ENV_FILE"
    success "Credentials saved to .env (chmod 600)"
fi

# ── Phase 3: Record hostname for UI display ───────────────────────────────────

echo ""
info "Recording hostname for UI: $HOSTNAME"

if grep -q '^HOSTNAME_DISPLAY=' "$ENV_FILE"; then
    sed -i "s/^HOSTNAME_DISPLAY=.*/HOSTNAME_DISPLAY=${HOSTNAME}/" "$ENV_FILE"
else
    echo "HOSTNAME_DISPLAY=${HOSTNAME}" >> "$ENV_FILE"
fi
success "Hostname recorded in .env"

# ── Phase 4: Install polkit rule ──────────────────────────────────────────────

echo ""
info "Installing polkit rule for user: $APP_USER"

sudo mkdir -p /etc/polkit-1/rules.d
sed "s/subject.user === \"pi\"/subject.user === \"${APP_USER}\"/" \
    "$SCRIPT_DIR/polkit/10-wifi-config.rules" \
    | sudo tee /etc/polkit-1/rules.d/10-wifi-config.rules > /dev/null

sudo systemctl restart polkit
sleep 1

if systemctl is-active --quiet polkit; then
    success "polkit rule installed and polkit restarted"
else
    fail "polkit failed to restart. Check: sudo systemctl status polkit"
fi

# ── Phase 5: Fix permissions on existing Wi-Fi connections ────────────────────

echo ""
info "Setting ownership on existing Wi-Fi connection profiles..."

WIFI_CONNS=$(nmcli -t -f NAME,TYPE connection show | grep '802-11-wireless' | cut -d: -f1 || true)

if [ -z "$WIFI_CONNS" ]; then
    info "No existing Wi-Fi connections found. Nothing to update."
else
    while IFS= read -r CONN_NAME; do
        info "  Setting permissions on: $CONN_NAME"
        sudo nmcli connection modify "$CONN_NAME" connection.permissions "user:${APP_USER}"
        success "  Done: $CONN_NAME"
    done <<< "$WIFI_CONNS"
fi

# ── Phase 6: Build and start container ───────────────────────────────────────

echo ""
info "Building Docker image..."
cd "$SCRIPT_DIR"
docker compose build

echo ""
info "Starting container..."
docker compose up -d

sleep 3
if curl -sf http://127.0.0.1:8090/health > /dev/null; then
    success "Container is running and healthy"
else
    warn "Container started but health check failed. Check logs:"
    echo "  docker compose logs wifi-config"
fi

# ── Phase 7: Summary ──────────────────────────────────────────────────────────

PI_IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+\.\d+\.\d+\.\d+' | head -1)
if [ -z "$PI_IP" ]; then
    PI_IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet\s)\d+\.\d+\.\d+\.\d+' | head -1)
fi
if [ -z "$PI_IP" ]; then
    PI_IP="<PI_IP>"
fi

WIFI_USER_DISPLAY=$(grep WIFI_USER "$ENV_FILE" | cut -d= -f2)

echo ""
echo "================================================"
echo -e "  ${GREEN}Installation complete!${NC}"
echo "================================================"
echo ""
echo "  Web UI:  http://${PI_IP}:8090"
echo "  User:    ${WIFI_USER_DISPLAY}"
echo "  Health:  http://${PI_IP}:8090/health"
echo ""
echo "  To view logs:   docker compose logs wifi-config"
echo "  To stop:        docker compose down"
echo "  To restart:     docker compose up -d"
echo "  To update:      git pull && bash install.sh"
echo ""
echo "  ── Dashboard entries ────────────────────────"
echo ""
echo "  Dashy:"
echo "    title: Wi-Fi Config"
echo "    description: \"Login: ${WIFI_USER_DISPLAY} / ****\""
echo "    url: http://${PI_IP}:8090"
echo "    icon: fas fa-wifi"
echo ""
echo "  Fenrus:"
echo "    Name: Wi-Fi Config"
echo "    URL:  http://${PI_IP}:8090"
echo "    Icon: (upload a wifi icon manually)"
echo ""
echo "  Flame:"
echo "    Name: Wi-Fi Config"
echo "    URL:  http://${PI_IP}:8090"
echo "    Icon: wifi"
echo ""
echo "  Heimdall:"
echo "    Name:   Wi-Fi Config"
echo "    URL:    http://${PI_IP}:8090"
echo "    Colour: #1565c0"
echo "    Icon:   (select Generic)"
echo ""
echo "  Homarr:"
echo "    Name: Wi-Fi Config"
echo "    URL:  http://${PI_IP}:8090"
echo "    Icon: https://cdn.jsdelivr.net/npm/@mdi/svg/svg/wifi.svg"
echo ""
echo "  Homepage:"
echo "    - Wi-Fi Config:"
echo "        href: http://${PI_IP}:8090"
echo "        description: Manage wlan0 Wi-Fi connections"
echo "        icon: mdi-wifi"
echo ""
echo "  Homer:"
echo "    - name: Wi-Fi Config"
echo "      subtitle: Manage wlan0 Wi-Fi connections"
echo "      url: http://${PI_IP}:8090"
echo "      icon: fas fa-wifi"
echo ""
echo "  Organizr:"
echo "    Tab Name:  Wi-Fi Config"
echo "    Tab URL:   http://${PI_IP}:8090"
echo "    Tab Image: (search 'wifi' in icon picker)"
echo ""
