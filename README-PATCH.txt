Aura Web Admin v12.1 - Print Progress UI

Adds a low-load print progress modal for Generate + Print and Print/Reprint actions.

Flow:
1. Click Generate + Print / Print
2. Modal shows Printing voucher with spinner
3. Request runs normally on Aura backend
4. Success shows Done printing + voucher code + Close
5. Error shows a clear message + Close

No background daemon or polling is added. The modal exists only in the browser while a print request is active.

Files changed:
- aura-web/app.py
- aura-web/templates/admin_base.html
- aura-web/templates/generate.html
- aura-web/templates/vouchers.html
- aura-web/static/style.css

This patch intentionally does NOT include printer_mx06.py, so your current larger receipt-size tuning is preserved.
