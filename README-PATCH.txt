Aura Web Admin v13.1 — Branding Integration

Based on v13 Manager PIN Access.
This is an overlay patch: extract it at the repository root, NOT inside aura-web/.

Adds:
- Aura icon in admin navbar and customer status header
- Full Aura logo on Manager PIN and Owner login pages
- Browser favicon + Apple touch icon + web app manifest
- Aura icon inside the print progress/result modal
- Optimized branding PNGs under aura-web/static/branding/
- No app.py, printer_mx06.py, or auth logic changes

Runtime deployment: copy the changed templates, style.css, and static/branding folder.
