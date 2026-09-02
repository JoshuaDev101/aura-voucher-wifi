Aura Web Admin v11 - MX06 Print Integration (low-load)
=====================================================

What changed
------------
- Physically verified MX06 BLE printing path is now wired into Aura.
- Generate page has Generate and Generate + Print.
- Recent vouchers and Vouchers page have Print/Reprint actions.
- Receipt is 384px monochrome and contains only:
  AURA WIFI VOUCHER / code / validity / price / QR.
- QR points to AURA_STATUS_URL (default http://192.168.1.124/status).
- Printing is one-shot and event-driven: render -> BLE connect -> print -> disconnect -> exit.
- Pillow/qrcode are loaded in a short-lived child process only, so idle Aura RAM stays almost unchanged.
- A low-memory safety guard refuses a print if MemAvailable is below 64 MB (configurable).
- No BLE scanner, battery poller, or permanent printer worker was added.

Before deploying
----------------
The tested Cat-Printer driver must exist at:
  /opt/aura-printer-test/printer.py

This was already physically verified on MX06 using:
  /opt/aura-voucher-wifi/.venv/bin/python printer.py aura-test.pbm -s "4,30:08:26:16:C8:49" -0 -q 2

Install only the receipt-rendering dependencies:
  /opt/aura-voucher-wifi/.venv/bin/pip install -r /opt/aura-voucher-wifi/aura-web/requirements-printer.txt

Recommended /etc/aura/aura.env values
-------------------------------------
Set YOUR real prices before production printing:
  AURA_PRICE_1D=""
  AURA_PRICE_3D=""
  AURA_PRICE_7D=""

Printer defaults (change only if needed):
  AURA_PRINTER_MAC="30:08:26:16:C8:49"
  AURA_PRINTER_DRIVER="/opt/aura-printer-test/printer.py"
  AURA_STATUS_URL="http://192.168.1.124/status"
  AURA_PRINTER_MIN_AVAILABLE_MB="64"

Deploy files
------------
Copy:
  aura-web/app.py
  aura-web/printer_mx06.py
  aura-web/requirements-printer.txt
  aura-web/templates/admin_base.html
  aura-web/templates/generate.html
  aura-web/templates/vouchers.html
  aura-web/templates/system.html
  aura-web/static/style.css

Then:
  /opt/aura-voucher-wifi/.venv/bin/python -m py_compile \
    /opt/aura-voucher-wifi/aura-web/app.py \
    /opt/aura-voucher-wifi/aura-web/printer_mx06.py
  systemctl restart aura-web
  systemctl is-active aura-web

First production test
---------------------
1. Set real plan prices in /etc/aura/aura.env.
2. Restart aura-web.
3. Open /admin/generate.
4. Generate a temporary 1-day voucher using Generate + Print.
5. Verify code, validity, price, QR and paper feed.
6. Re-run free -h and process RSS after the print to confirm memory returned.
