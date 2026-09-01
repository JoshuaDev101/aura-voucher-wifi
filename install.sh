#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo ./install.sh"; exit 1; fi

echo "=== Aura Voucher WiFi Installer ==="
ROOT_OPTS="$(findmnt -no OPTIONS / || true)"
if [[ ",${ROOT_OPTS}," == *",ro,"* ]]; then echo "ERROR: root filesystem is read-only. Refusing to install."; exit 2; fi

hostnamectl set-hostname aura || true
apt-get update
apt-get install -y nginx avahi-daemon curl git python3 bluez

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker CE first, then rerun this script."
  exit 3
fi
systemctl enable --now docker

mkdir -p /opt/aura-voucher-wifi
cp -a ./. /opt/aura-voucher-wifi/
if [[ ! -f /opt/aura-voucher-wifi/config/config.local.json ]]; then
  cp /opt/aura-voucher-wifi/config/config.example.json /opt/aura-voucher-wifi/config/config.local.json
  chmod 600 /opt/aura-voucher-wifi/config/config.local.json
fi
mkdir -p /opt/omada/{data,logs,work}
cp /opt/aura-voucher-wifi/systemd/aura-web.service /etc/systemd/system/
cp /opt/aura-voucher-wifi/nginx/aura.conf /etc/nginx/sites-available/aura
ln -sf /etc/nginx/sites-available/aura /etc/nginx/sites-enabled/aura
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable --now aura-web nginx avahi-daemon bluetooth

echo
echo "Aura starter web: http://aura.local/"
echo "Omada is intentionally not auto-started yet."
echo "After storage/power stability is confirmed: docker compose up -d omada"
