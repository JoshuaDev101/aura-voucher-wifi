Aura Web Admin v12 - 3H + 7H Access Plans

Adds two low-load voucher durations without adding any background service or polling:
- 3 Hours = 180 minutes
- 7 Hours = 420 minutes
- Existing 1 Day / 3 Days / 7 Days remain unchanged

Pricing variables in /etc/aura/aura.env:
AURA_PRICE_3H=""
AURA_PRICE_7H=""
AURA_PRICE_1D="20"
AURA_PRICE_3D="50"
AURA_PRICE_7D="80"

The Generate page now shows five responsive plan cards and stays mobile-friendly.
The larger compact MX06 receipt tuning is also preserved in this package.

Deploy changed files:
  aura-web/app.py
  aura-web/printer_mx06.py
  aura-web/templates/generate.html
  aura-web/templates/dashboard.html
  aura-web/templates/admin_base.html
  aura-web/static/style.css

After deploy:
  python -m py_compile app.py printer_mx06.py
  systemctl restart aura-web
