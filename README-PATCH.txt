Aura Web Admin v12.2 — Print Retry + Sound/Vibration Feedback

Changes:
- Print modal shows Printing -> Done or Error.
- Error state offers Retry Print when it is safe to retry the SAME voucher.
- Generate+Print failures return a safe reprint URL so retry never creates a duplicate voucher.
- Success/error chimes generated in-browser with Web Audio API (no audio files).
- Android vibration patterns via navigator.vibrate when supported.
- No new daemon, service, or background polling.
- printer_mx06.py is unchanged to preserve the tuned receipt sizing.
