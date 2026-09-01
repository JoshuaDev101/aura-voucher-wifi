#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo ./update.sh"; exit 1; fi
ROOT_OPTS="$(findmnt -no OPTIONS / || true)"
if [[ ",${ROOT_OPTS}," == *",ro,"* ]]; then echo "ERROR: root filesystem is read-only."; exit 2; fi
cp -a ./. /opt/aura-voucher-wifi/
systemctl restart aura-web
nginx -t && systemctl reload nginx
echo "Aura updated."
