Aura Web Admin v2 - Omada health integration

Merge into your existing repo, replacing matching files.

PowerShell:
  python -m pip install -r requirements.txt

  $env:AURA_ADMIN_USER="admin"
  $env:AURA_ADMIN_PASSWORD="your-local-admin-password"
  $env:AURA_SECRET_KEY="change-this-local-secret"
  $env:AURA_OMADA_URL="https://192.168.1.124:8043"

  python .\aura-web\app.py

Then:
  http://127.0.0.1:8790/health
  http://127.0.0.1:8790/admin/

The Omada controller status/version/ID are now probed from /api/info.
Voucher generation, clients and AP stats remain mock until live 5.15
authenticated endpoints are verified.
