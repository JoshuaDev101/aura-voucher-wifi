# Aura Voucher WiFi

Lightweight Orange Pi voucher hotspot stack.

## Target
- Orange Pi Zero 3 / ARM64
- Ubuntu Server / Debian
- Docker
- Omada Controller 5.15 ARM64
- nginx + avahi
- Aura Web Admin
- MX06 BLE thermal printer integration

## Planned URLs
- `http://aura.local/` — customer voucher/status page
- `http://aura.local/admin/` — password-protected admin dashboard

## Planned Admin Features
- Generate Omada vouchers: 1 Day / 3 Days / 7 Days
- Voucher history: unused / active / expired
- Connected clients and AP status
- MX06 printer status
- Print / reprint / optional auto-print

## Install
```bash
git clone <YOUR_REPO_URL>
cd aura-voucher-wifi
sudo ./install.sh
```

Secrets and databases are excluded from Git via `.gitignore`.

> The Omada 5.15 voucher API adapter remains disabled until the live 5.15 endpoint/payload is captured and verified.
