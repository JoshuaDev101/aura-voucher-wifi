Aura Web Admin v14.1 — MX06 Bulk Thermal Vouchers

Changes from v14:
- Removes short-bond/Letter bulk-sheet workflow.
- Bulk quantities remain 10 / 20 / 30 / 40 / 50.
- Bulk vouchers print on the existing 57 mm MX06.
- Thermal layout follows the original Aura/TP-Link style requested by the owner:
  header bar, large code on the left, QR on the right, duration and price below.
- Dashed CUT guide after every voucher.
- Bulk printing runs as a short-lived background process, so the web request does not
  need to stay open for a long 20-50 voucher BLE print.
- Browser polls only while a bulk print job is active.
- Bulk errors show Retry Full Batch / Close with sound and vibration feedback.
- Single-voucher printer_mx06.py is NOT included and is NOT overwritten.
- A cross-process printer lock prevents single and bulk print jobs from using MX06
  at the same time.

Deploy these files:
  aura-web/app.py
  aura-web/omada_api.py
  aura-web/printer_mx06_bulk.py
  aura-web/templates/generate.html
  aura-web/templates/bulk.html
  aura-web/templates/bulk_batch.html
  aura-web/static/style.css

The old aura-web/templates/bulk_sheet.html from v14 is no longer used. It can remain
on disk safely or be deleted.

Requirements: existing Aura printer dependencies (Pillow + qrcode) are reused.
