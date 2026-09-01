Aura Web Admin v4 - Live Voucher Status
=======================================

What changes:
- REAL voucher generation remains enabled.
- Existing Aura SQLite DB is migrated automatically.
- Adds start_time, end_time, last_sync columns.
- Dashboard refresh syncs up to 25 non-expired vouchers from Omada.
- Status is derived from Omada startTime/endTime:
    startTime == 0 -> Unused
    started and endTime still in future -> Active
    endTime reached -> Expired
- Shows remaining time for active vouchers.
- Does NOT yet add live AP/client counts.
- MX06 printer remains the next milestone.

Merge into the existing repo:
  aura-web/app.py
  aura-web/omada_api.py
  aura-web/templates/dashboard.html

Then on Orange Pi:
  cd /root/aura-voucher-wifi
  git pull

  cp aura-web/app.py /opt/aura-voucher-wifi/aura-web/
  cp aura-web/omada_api.py /opt/aura-voucher-wifi/aura-web/
  cp aura-web/templates/dashboard.html /opt/aura-voucher-wifi/aura-web/templates/

  systemctl restart aura-web
  sleep 2
  systemctl is-active aura-web
  curl -s http://127.0.0.1:8790/health

Open:
  http://192.168.1.124/admin/

Use "Refresh Status" after a voucher is first used.
