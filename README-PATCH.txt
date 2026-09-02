Aura Web Admin v13 — Owner + Manager PIN Access
=================================================

What changed
------------
- Default /admin/login is now a touch-friendly 6-digit Manager PIN keypad.
- Separate Owner sign-in remains available at /admin/owner-login using the existing AURA_ADMIN_USER / AURA_ADMIN_PASSWORD.
- Owner-only Settings page at /admin/settings can set, change, or disable the Manager PIN.
- Manager can use Overview, Generate + Print, Vouchers, and Clients.
- System and Settings are Owner-only.
- Optional "Remember this phone" keeps a manager signed in for up to 30 days.
- Five incorrect Manager PIN attempts trigger a temporary 5-minute lockout.
- Changing/disabling the PIN invalidates all remembered manager sessions.
- PIN is stored only as a PBKDF2 hash, additionally tied to AURA_SECRET_KEY; it is never shown again.
- Manager PIN login has success/error sound + Android vibration when the browser supports them.
- Existing v12.2 printer retry/sound/vibration UI is preserved.
- Existing 3H / 7H / 1D / 3D / 7D plans are preserved.

How to set the Manager PIN
--------------------------
1. Deploy v13 and restart aura-web.
2. Open: http://192.168.1.124/admin/login
3. Tap "Owner sign in".
4. Sign in with the existing owner account.
5. Open Settings.
6. Under Manager Access, enter a new 6-digit PIN twice and press "Set manager PIN".
7. Sign out.
8. Your manager can now use /admin/login with only the 6-digit PIN.

No PIN is added to /etc/aura/aura.env. It is stored securely in /var/lib/aura/aura.db.

Recommended deployment
----------------------
Extract this ZIP at the repository root. Do NOT extract it inside an existing aura-web folder, or you may create aura-web/aura-web.

Copy the changed runtime files, then restart aura-web.
