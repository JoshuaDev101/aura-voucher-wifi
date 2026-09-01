Aura Web Admin v5 - Live AP + Connected Client Counts
=====================================================

Verified against this controller:
- Omada Controller 5.15.24.19
- API v3
- EAP110-Outdoor

What changes:
- Keeps REAL 1/3/7-day Omada voucher generation.
- Keeps live voucher status / remaining time.
- Adds live AP online / total count.
- Adds connected Wi-Fi client count.
- Uses the verified Omada endpoint:
    /api/v2/sites/{SITE}/grid/devices
- "Connected Clients" is calculated from clientNum on currently-online APs.
  This is preferable to counting /insight/clients because that endpoint can
  include historical client records.

Merge these files into the repo:
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

Expected with the current test AP/client:
  Connected Clients: 1
  AP Online: 1/1

Next milestone:
  MX06 printer integration.
