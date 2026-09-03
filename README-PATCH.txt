Aura Web Admin v14.2 — Resumable MX06 Bulk Printing

Changes from v14.1:
- Bulk MX06 printing is now one voucher at a time instead of one giant strip job.
- Live progress: current voucher + N / total + percentage.
- Pause button: pauses safely between vouchers so the paper roll can be changed.
- Resume button: continues the same running batch after a pause.
- Stop button: stops between vouchers and preserves a "Resume from #N" point.
- If a voucher print fails, Aura shows the exact failed voucher number.
- Resume from failed voucher prints only the remaining existing codes; it does NOT
  create a new Omada batch.
- Reprint Previous prints only the voucher immediately before the failure.
- After reprinting the previous voucher, Continue Batch resumes at the failed one.
- Existing single-voucher printer_mx06.py is NOT included or overwritten.
- Existing manager/owner auth, branding, plans, and voucher data remain unchanged.
- Shared flock still prevents a single-voucher job and a bulk job from using MX06
  at the same time.

Hardware limitation:
- MX06 paper-out/battery status is still not verified/exposed to Aura.
- Progress means the BLE driver accepted each voucher print command. If the paper
  physically runs out, inspect the last voucher and use Pause/Resume/Reprint as
  needed.

Deploy these files:
  aura-web/app.py
  aura-web/printer_mx06_bulk.py
  aura-web/templates/bulk_batch.html
  aura-web/static/style.css

No database migration or new Python dependency is required.
