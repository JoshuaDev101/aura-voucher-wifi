Aura Web Admin v14.3 - QR Redeem / Prefill

Base: v14.1 MX06 Bulk Thermal (no resumable/pause worker).

Changes:
- Bulk thermal QR now contains a local Aura redeem URL instead of plain text code.
- Adds GET /redeem?code=... bridge route.
- The route stores the code for 10 minutes and triggers Omada captive portal.
- Bulk printer payload carries a unique qr_url per voucher.
- Existing printer_mx06.py is NOT included or changed.
- Existing bulk print behavior remains whole-batch v14.1 behavior.

IMPORTANT: Upload/install the matching Omada Portal v2.2 QR Prefill package too.
That portal reads the voucher from the intercepted originUrl or Aura cookie and pre-fills Voucher code.

Default redeem URL is derived from AURA_STATUS_URL.
With the current setup it becomes: http://192.168.1.124/redeem?code=<VOUCHER>
Optional override: AURA_REDEEM_URL=http://your-stable-host/redeem
