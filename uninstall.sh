#!/usr/bin/env bash
set -euo pipefail
systemctl disable --now aura-web 2>/dev/null || true
rm -f /etc/systemd/system/aura-web.service
rm -f /etc/nginx/sites-enabled/aura /etc/nginx/sites-available/aura
systemctl daemon-reload
nginx -t && systemctl reload nginx || true
echo "Kept /opt/aura-voucher-wifi and /opt/omada intentionally."
