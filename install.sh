#!/usr/bin/env bash
set -euo pipefail

# =========================================================
# Aura Voucher WiFi Installer
# Orange Pi / Ubuntu Server / Debian ARM64
# =========================================================

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo:"
  echo "  sudo ./install.sh"
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/aura-voucher-wifi"
CONFIG_DIR="/etc/aura"
ENV_FILE="$CONFIG_DIR/aura.env"

echo
echo "======================================"
echo "       AURA VOUCHER WIFI"
echo "======================================"
echo

# ---------------------------------------------------------
# 1. Storage safety check
# ---------------------------------------------------------

echo "[1/10] Checking filesystem..."

ROOT_OPTS="$(findmnt -no OPTIONS / || true)"

if [[ ",${ROOT_OPTS}," == *",ro,"* ]]; then
  echo
  echo "ERROR: Root filesystem is READ-ONLY."
  echo "Installation stopped to protect the SD card."
  exit 2
fi

touch /tmp/aura-write-test
rm -f /tmp/aura-write-test

echo "Filesystem: OK"

# ---------------------------------------------------------
# 2. Hostname
# ---------------------------------------------------------

echo "[2/10] Setting hostname..."

hostnamectl set-hostname aura || true

# ---------------------------------------------------------
# 3. Packages
# ---------------------------------------------------------

echo "[3/10] Installing system packages..."

apt-get update

apt-get install -y \
  nginx \
  avahi-daemon \
  curl \
  git \
  python3 \
  python3-venv \
  python3-pip \
  bluez

# ---------------------------------------------------------
# 4. Docker
# ---------------------------------------------------------

echo "[4/10] Checking Docker..."

if ! command -v docker >/dev/null 2>&1; then
  echo
  echo "ERROR: Docker is not installed."
  echo "Install Docker CE first, then run this installer again."
  exit 3
fi

systemctl enable --now docker

echo "Docker: $(docker --version)"

# ---------------------------------------------------------
# 5. Copy Aura source
# ---------------------------------------------------------

echo "[5/10] Installing Aura files..."

mkdir -p "$INSTALL_DIR"

if [[ "$SRC_DIR" != "$INSTALL_DIR" ]]; then
  cp -a "$SRC_DIR/." "$INSTALL_DIR/"
fi

# Local config
if [[ ! -f "$INSTALL_DIR/config/config.local.json" ]]; then
  cp \
    "$INSTALL_DIR/config/config.example.json" \
    "$INSTALL_DIR/config/config.local.json"

  chmod 600 "$INSTALL_DIR/config/config.local.json"
fi

# ---------------------------------------------------------
# 6. Python virtual environment
# ---------------------------------------------------------

echo "[6/10] Creating Python environment..."

if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi

"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip

"$INSTALL_DIR/.venv/bin/pip" install \
  -r "$INSTALL_DIR/requirements.txt"

# ---------------------------------------------------------
# 7. Aura secrets
# ---------------------------------------------------------

echo "[7/10] Configuring Aura..."

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

if [[ ! -f "$ENV_FILE" ]]; then

  echo
  read -r -p "Aura admin username [admin]: " AURA_USER
  AURA_USER="${AURA_USER:-admin}"

  while true; do
    read -r -s -p "Aura admin password: " AURA_PASS
    echo

    if [[ ${#AURA_PASS} -lt 8 ]]; then
      echo "Password must be at least 8 characters."
      continue
    fi

    read -r -s -p "Confirm admin password: " AURA_PASS2
    echo

    if [[ "$AURA_PASS" != "$AURA_PASS2" ]]; then
      echo "Passwords do not match."
      continue
    fi

    break
  done

  read -r -p \
    "Omada controller URL [https://127.0.0.1:8043]: " \
    OMADA_URL

  OMADA_URL="${OMADA_URL:-https://127.0.0.1:8043}"

  SECRET_KEY="$(
    python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
  )"

  # Escape values for systemd EnvironmentFile
  esc() {
    printf '%s' "$1" \
      | sed 's/\\/\\\\/g; s/"/\\"/g'
  }

  cat > "$ENV_FILE" <<EOF
AURA_ADMIN_USER="$(esc "$AURA_USER")"
AURA_ADMIN_PASSWORD="$(esc "$AURA_PASS")"
AURA_SECRET_KEY="$(esc "$SECRET_KEY")"
AURA_OMADA_URL="$(esc "$OMADA_URL")"
AURA_OMADA_VERIFY_TLS="false"
EOF

  chmod 600 "$ENV_FILE"

else
  echo "Existing Aura credentials found. Keeping them."
fi

# ---------------------------------------------------------
# 8. Omada directories
# ---------------------------------------------------------

echo "[8/10] Preparing Omada..."

mkdir -p /opt/omada/data
mkdir -p /opt/omada/logs
mkdir -p /opt/omada/work

# ---------------------------------------------------------
# 9. nginx + systemd
# ---------------------------------------------------------

echo "[9/10] Installing services..."

cp \
  "$INSTALL_DIR/systemd/aura-web.service" \
  /etc/systemd/system/aura-web.service

cp \
  "$INSTALL_DIR/nginx/aura.conf" \
  /etc/nginx/sites-available/aura

ln -sf \
  /etc/nginx/sites-available/aura \
  /etc/nginx/sites-enabled/aura

rm -f /etc/nginx/sites-enabled/default

nginx -t

systemctl daemon-reload

systemctl enable --now \
  aura-web \
  nginx \
  avahi-daemon \
  bluetooth

# ---------------------------------------------------------
# 10. Status
# ---------------------------------------------------------

echo "[10/10] Checking services..."

echo
echo "Aura Web:"
systemctl --no-pager --full status aura-web | head -n 10 || true

echo
echo "nginx:"
systemctl is-active nginx || true

echo
echo "Avahi:"
systemctl is-active avahi-daemon || true

echo
echo "Bluetooth:"
systemctl is-active bluetooth || true

echo
echo "======================================"
echo " Installation complete"
echo "======================================"
echo
echo "Customer:"
echo "  http://aura.local/"
echo
echo "Admin:"
echo "  http://aura.local/admin/login"
echo
echo "Direct IP also works:"
echo "  http://<ORANGE-PI-IP>/"
echo

if docker compose version >/dev/null 2>&1; then
  echo "Docker Compose detected."
  echo
  echo "Start Omada with:"
  echo "  cd $INSTALL_DIR"
  echo "  docker compose up -d omada"
else
  echo "NOTE: Docker Compose plugin not detected."
fi

echo
echo "Aura credentials are stored locally in:"
echo "  $ENV_FILE"
echo
echo "Do NOT upload that file to GitHub."