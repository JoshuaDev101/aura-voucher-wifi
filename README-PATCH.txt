Aura Web Admin v14.4 - Single Print Matches Bulk QR

This patch only replaces:
  aura-web/printer_mx06.py

It keeps app.py, Omada API, bulk printer, auth, branding and portal logic untouched.

Single print now uses the same 384px compact ticket design as bulk:
- AURA VOUCHER WIFI black header
- large code
- validity
- price
- 118px QR
- dashed CUT line
- QR points to /redeem?code=<voucher>

Deploy from repo root:
  cp aura-web/printer_mx06.py /opt/aura-voucher-wifi/aura-web/printer_mx06.py
  chmod +x /opt/aura-voucher-wifi/aura-web/printer_mx06.py
  /opt/aura-voucher-wifi/.venv/bin/python -m py_compile /opt/aura-voucher-wifi/aura-web/printer_mx06.py
  systemctl restart aura-web
