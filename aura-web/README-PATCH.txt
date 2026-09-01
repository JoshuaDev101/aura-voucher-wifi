AURA WEB ADMIN v9 — NAVIGATION + SYSTEM DASHBOARD
=================================================

What changed
------------
- Main /admin/ is now overview/stats-first only.
- New owner navbar:
  Overview | Generate | Vouchers | System
- Persistent + Generate action in desktop header.
- Mobile bottom navigation for easy phone use.
- Separate /admin/generate page with 1/3/7-day voucher cards.
- Separate /admin/vouchers page with search, filters and one-tap copy.
- Separate /admin/system page with:
  * Omada online/offline
  * controller/API version
  * AP online count and AP details
  * connected clients
  * Orange Pi CPU load
  * RAM, zram/swap, storage, uptime and temperature when available
  * Aura service summary
- Customer / and /status redesigned to use the same Aura visual system.
- Customer pages still expose no owner/admin navigation.
- All /admin/* management routes remain login-protected.
- Omada admin snapshot is cached for 15 seconds to reduce repeated API load.
- System resource values are read locally; no extra controller polling required.

Files to replace/add
--------------------
aura-web/app.py
aura-web/static/style.css
aura-web/templates/admin_base.html
aura-web/templates/dashboard.html
aura-web/templates/generate.html
aura-web/templates/vouchers.html
aura-web/templates/system.html
aura-web/templates/customer.html
aura-web/templates/login.html

PC / Git
--------
git add .
git commit -m "Add navigation and system dashboard"
git push origin web-admin-v1

Orange Pi deploy
----------------
cd /root/aura-voucher-wifi
git pull origin web-admin-v1

cp aura-web/app.py /opt/aura-voucher-wifi/aura-web/app.py
cp aura-web/static/style.css /opt/aura-voucher-wifi/aura-web/static/style.css
cp aura-web/templates/admin_base.html /opt/aura-voucher-wifi/aura-web/templates/admin_base.html
cp aura-web/templates/dashboard.html /opt/aura-voucher-wifi/aura-web/templates/dashboard.html
cp aura-web/templates/generate.html /opt/aura-voucher-wifi/aura-web/templates/generate.html
cp aura-web/templates/vouchers.html /opt/aura-voucher-wifi/aura-web/templates/vouchers.html
cp aura-web/templates/system.html /opt/aura-voucher-wifi/aura-web/templates/system.html
cp aura-web/templates/customer.html /opt/aura-voucher-wifi/aura-web/templates/customer.html
cp aura-web/templates/login.html /opt/aura-voucher-wifi/aura-web/templates/login.html

/opt/aura-voucher-wifi/.venv/bin/python -m py_compile /opt/aura-voucher-wifi/aura-web/app.py /opt/aura-voucher-wifi/aura-web/omada_api.py
systemctl restart aura-web
systemctl is-active aura-web

Routes
------
Customer: / and /status
Owner overview: /admin/
Generate: /admin/generate
Vouchers: /admin/vouchers
System: /admin/system
