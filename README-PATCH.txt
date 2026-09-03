Aura Web Admin v14.5 - QR Auto Redeem Bridge

Only app.py is changed.

Changes:
- /redeem checks whether the requesting phone already has an active voucher.
  If active, it redirects straight to /status.
- Replaces the default NeverSSL captive trigger with Android's plain-HTTP
  connectivity check endpoint.
- Unauthenticated clients still need a plain-HTTP trigger because only Omada's
  dynamic captive portal has the client/AP/session parameters required to
  consume a voucher.
- Matching Omada Portal v2.3 automatically submits the scanned voucher and
  redirects successful QR logins to /status.

Existing environment override AURA_CAPTIVE_TRIGGER_URL still takes precedence.
If /etc/aura/aura.env explicitly contains a NeverSSL value, remove that line or
change it to:
AURA_CAPTIVE_TRIGGER_URL="http://connectivitycheck.gstatic.com/generate_204"
