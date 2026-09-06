# wifi-config

A lightweight web interface for managing Wi-Fi connections on a Raspberry Pi running Raspberry Pi OS Bookworm (Debian 12) with NetworkManager.

Runs as a Docker container. Communicates with NetworkManager over D-Bus. Does not require any privileged container flags.

---

## Features

- Current Wi-Fi status: SSID, signal strength, frequency, IP address
- Scan for nearby networks
- Connect to a network or update credentials for a saved one
- Add a new network manually (including hidden SSIDs)
- Remove a saved connection
- Automatic rollback if a connection attempt fails — ethernet is never affected

---

## Requirements

The following must be true on the target Pi before running the installer:

- Raspberry Pi OS Bookworm (Debian 12)
- NetworkManager managing networking — not dhcpcd, not systemd-networkd
- Docker and Docker Compose installed
- `wlan0` present and not rfkill-blocked

To verify your Pi is compatible, run:

```bash
cat /etc/os-release
systemctl is-active NetworkManager
systemctl is-active dhcpcd
ip link show wlan0
rfkill list wifi
```

Expected: OS is Bookworm, NetworkManager is `active`, dhcpcd returns not found, wlan0 exists, wifi is not blocked.

> Older Raspberry Pi OS versions (Bullseye and earlier) used dhcpcd by default and are **not supported**. Check with `cat /etc/os-release` before proceeding.

---

## Installation

```bash
git clone https://github.com/yourusername/wifi-config.git
cd wifi-config
bash install.sh
```

The installer will:

1. Verify system compatibility and that the repo is complete
2. Prompt for web UI credentials and save them to `.env`
3. Stamp the Pi's hostname into the web UI
4. Install the polkit rule (substituting your actual username)
5. Set ownership on any existing Wi-Fi connection profiles
6. Build the Docker image and start the container
7. Print ready-to-paste entries for common self-hosted dashboards

To update after a `git pull`:

```bash
git pull
bash install.sh
```

The installer is idempotent — credentials are preserved if `.env` already exists.

---

## Docker

Docker must be installed and your user must be in the `docker` group:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# log out and back in, then run install.sh
```

---

## Credentials

The installer prompts for a username and password, saved to `.env` (chmod 600). These are used for HTTP Basic Auth on the web UI.

To change credentials after installation:

```bash
nano ~/wifi-config/.env
cd ~/wifi-config && docker compose up -d
```

---

## Accessing the UI

```
http://<PI_IP>:8090
```

The installer prints the exact URL at the end, along with ready-to-paste entries for Dashy, Fenrus, Flame, Heimdall, Homarr, Homepage, Homer, and Organizr.

---

## Uninstalling

```bash
bash uninstall.sh
```

Stops the container, removes the polkit rule, removes `.env`, and restores `index.html` to its placeholder state. The repo is left intact and ready to reinstall.

To fully remove everything:

```bash
cd .. && rm -rf wifi-config/
```

---

## How it works

The app is a Flask server running inside a Docker container as uid 1000 (non-root). It communicates with NetworkManager on the host via the D-Bus system socket, which is bind-mounted into the container at `/run/dbus/system_bus_socket`.

A polkit rule grants the Pi user permission to call NetworkManager D-Bus methods without a password prompt. This is the only host-level privilege required — no `--privileged` flag, no `sudo` inside the container.

When a connection attempt is made, the app snapshots the current active connection first. If the new connection fails to activate within 30 seconds, it automatically reactivates the previous one.

---

## Security notes

- Web UI is protected by HTTP Basic Auth
- Wi-Fi passphrases are passed directly to NetworkManager over D-Bus and are never logged
- The container runs as uid 1000 (non-root)
- No `--privileged` flag
- Only the D-Bus socket is mounted from the host
- The app never touches eth0 or any non-wireless connection
- Only one connection attempt can run at a time (threading lock)
- Port 8090 is bound to `0.0.0.0` — reachable on your LAN. Do not forward this port through your router.

---

## File structure

```
wifi-config/
├── install.sh                      # Run to install or update
├── uninstall.sh                    # Run to remove
├── README.md
├── docker-compose.yml
├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py
│   └── templates/
│       └── index.html              # Contains HOSTNAME_PLACEHOLDER — stamped by installer
└── polkit/
    └── 10-wifi-config.rules        # Contains "pi" placeholder — substituted by installer
```

`.env` is created by the installer and is excluded from git.

---

## Troubleshooting

**"D-Bus error: AccessDenied" on connect/update**

The existing connection profile is not owned by the pi user. Run:

```bash
sudo nmcli connection modify "<connection name>" connection.permissions "user:pi"
```

The installer does this automatically for connections that exist at install time. This only occurs for connections added afterwards via another method.

**"WIFI_PASS environment variable not set" (503 error)**

The `.env` file is missing or `WIFI_PASS` is empty:

```bash
cat ~/wifi-config/.env
cd ~/wifi-config && docker compose up -d
```

**Scan returns no results**

NetworkManager rate-limits scan requests. Wait 30 seconds and try again.

**Connection attempt always fails / rolls back**

- Verify the passphrase is correct by testing on another device first
- Check logs: `docker compose logs wifi-config`
- Check wlan0 is not rfkill-blocked: `rfkill list wifi`

**App not reachable after Pi reboot**

The container is set to `restart: unless-stopped` and should start automatically:

```bash
docker compose ps
docker compose up -d
```

---

## Differences on Raspberry Pi 4B

The Pi 4B uses the same Bookworm OS and NetworkManager setup, so this works without modification provided the OS is Bookworm (not Bullseye or earlier). The wireless interface is also called `wlan0`.

---

## API

All endpoints require Basic Auth except `/health`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Current wlan0 status |
| GET | `/api/scan` | Scan for nearby networks |
| GET | `/api/connections` | List saved Wi-Fi connections |
| POST | `/api/connect` | Connect or update a network |
| DELETE | `/api/connections/<uuid>` | Remove a saved connection |
| GET | `/health` | Health check (no auth) |

**POST /api/connect**

```json
{ "ssid": "MyNetwork", "passphrase": "mypassword" }
```

Leave `passphrase` empty or omit it for open networks.
