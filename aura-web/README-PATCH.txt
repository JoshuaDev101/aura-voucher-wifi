Aura Web Admin v3 - REAL Omada vouchers

Merge these files into the existing repo:
- aura-web/app.py
- aura-web/omada_api.py
- aura-web/templates/dashboard.html

On Orange Pi, add these LOCAL values to /etc/aura/aura.env:
AURA_OMADA_USER="your Omada username/email"
AURA_OMADA_PASSWORD="your Omada password"
AURA_OMADA_SITE_ID="your site id"
AURA_OMADA_RATE_LIMIT_ID="your rate-limit id"
AURA_DATA_DIR="/var/lib/aura"

Then:
mkdir -p /var/lib/aura
chmod 750 /var/lib/aura
systemctl restart aura-web

Do not commit /etc/aura/aura.env to GitHub.
