from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from functools import wraps
from datetime import datetime
from pathlib import Path
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import secrets
import sqlite3
import time

from omada_api import OmadaClient, OmadaError


app = Flask(__name__)
# Flask is only reachable through local nginx. Trust its forwarded client IP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("AURA_SECRET_KEY", "dev-only-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
)

ADMIN_USER = os.environ.get("AURA_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("AURA_ADMIN_PASSWORD")

if not ADMIN_PASSWORD:
    raise RuntimeError("AURA_ADMIN_PASSWORD is not set.")

DATA_DIR = Path(os.environ.get("AURA_DATA_DIR", "/var/lib/aura"))
DB = DATA_DIR / "aura.db"

PLANS = {
    "1d": {"name": "1 Day", "minutes": 1440, "short": "24 hours"},
    "3d": {"name": "3 Days", "minutes": 4320, "short": "72 hours"},
    "7d": {"name": "7 Days", "minutes": 10080, "short": "168 hours"},
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS vouchers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                plan_name TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unused',
                omada_group_id TEXT,
                omada_voucher_id TEXT
            )
            """
        )

        existing = {
            row[1] for row in con.execute("PRAGMA table_info(vouchers)").fetchall()
        }

        migrations = {
            "start_time": "ALTER TABLE vouchers ADD COLUMN start_time INTEGER",
            "end_time": "ALTER TABLE vouchers ADD COLUMN end_time INTEGER",
            "last_sync": "ALTER TABLE vouchers ADD COLUMN last_sync TEXT",
        }

        for column, sql in migrations.items():
            if column not in existing:
                con.execute(sql)


def get_vouchers(limit=50):
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        result = con.execute(
            "SELECT * FROM vouchers ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()

    return [dict(row) for row in result]


def save_voucher(plan, info):
    with sqlite3.connect(DB) as con:
        con.execute(
            """
            INSERT OR REPLACE INTO vouchers(
                code,
                plan_name,
                duration_minutes,
                created_at,
                status,
                omada_group_id,
                omada_voucher_id,
                start_time,
                end_time,
                last_sync
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                info["code"],
                plan["name"],
                int(plan["minutes"]),
                datetime.now().astimezone().isoformat(timespec="seconds"),
                derive_status(
                    info.get("start_time"),
                    info.get("end_time"),
                ),
                info.get("group_id"),
                info.get("voucher_id"),
                int(info.get("start_time") or 0),
                int(info.get("end_time") or 0),
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )


def derive_status(start_time, end_time):
    start = int(start_time or 0)
    end = int(end_time or 0)
    now_ms = int(time.time() * 1000)

    if start <= 0:
        return "unused"

    # Omada can use a very large value when there is no finite end yet.
    if 0 < end < 9_000_000_000_000_000_000 and now_ms >= end:
        return "expired"

    return "active"


def sync_vouchers_from_sessions(sessions):
    """Update local voucher state from one lightweight hotspot-client fetch."""
    now_ms = int(time.time() * 1000)
    synced_at = datetime.now().astimezone().isoformat(timespec="seconds")

    live_by_code = {}
    for row in sessions or []:
        code = str(row.get("voucherCode") or "").strip()
        if not code:
            continue

        previous = live_by_code.get(code)
        if previous is None or int(row.get("end") or 0) >= int(previous.get("end") or 0):
            live_by_code[code] = row

    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, code, status, start_time, end_time FROM vouchers"
        ).fetchall()

        for voucher in rows:
            live = live_by_code.get(voucher["code"])

            if live:
                start_ms = int(live.get("start") or 0)
                end_ms = int(live.get("end") or 0)
                valid = bool(live.get("valid"))
                status = "active" if valid and (end_ms <= 0 or end_ms > now_ms) else "expired"
                con.execute(
                    """
                    UPDATE vouchers
                    SET status = ?, start_time = ?, end_time = ?, last_sync = ?
                    WHERE id = ?
                    """,
                    (status, start_ms, end_ms, synced_at, voucher["id"]),
                )
                continue

            end_ms = int(voucher["end_time"] or 0)
            if voucher["status"] == "active" and 0 < end_ms <= now_ms:
                con.execute(
                    "UPDATE vouchers SET status = 'expired', last_sync = ? WHERE id = ?",
                    (synced_at, voucher["id"]),
                )


def remaining_text(voucher):
    status = voucher.get("status")

    if status == "unused":
        return "Not started"

    if status == "expired":
        return "Expired"

    end = int(voucher.get("end_time") or 0)
    if end <= 0 or end >= 9_000_000_000_000_000_000:
        return "Active"

    seconds = max(0, (end - int(time.time() * 1000)) // 1000)
    if seconds <= 0:
        return "Expired"

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def format_created(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%b %d · %I:%M %p").replace(" 0", " ")
    except Exception:
        return value


def format_customer_status(client_ip):
    """Return the live Omada hotspot session for the requesting client."""
    clean_ip = (client_ip or "").strip()
    if clean_ip.startswith("::ffff:"):
        clean_ip = clean_ip[7:]

    if not clean_ip or clean_ip in ("127.0.0.1", "::1"):
        return {
            "state": "unknown",
            "message": "Client address unavailable.",
            "remaining_seconds": 0,
        }

    try:
        live = OmadaClient().get_hotspot_client_by_ip(clean_ip)
    except Exception:
        return {
            "state": "error",
            "message": "Controller temporarily unavailable.",
            "remaining_seconds": 0,
        }

    if not live:
        return {
            "state": "not_authenticated",
            "message": "No active voucher session found for this device.",
            "remaining_seconds": 0,
        }

    start_ms = int(live.get("start") or 0)
    end_ms = int(live.get("end") or 0)
    now_ms = int(time.time() * 1000)
    valid = bool(live.get("valid"))

    remaining_seconds = 0
    if end_ms > 0:
        remaining_seconds = max(0, (end_ms - now_ms) // 1000)

    state = "active" if valid and remaining_seconds > 0 else "expired"
    voucher_code = str(live.get("voucherCode") or "").strip()

    if voucher_code:
        try:
            with sqlite3.connect(DB) as con:
                con.execute(
                    """
                    UPDATE vouchers
                    SET status = ?, start_time = ?, end_time = ?, last_sync = ?
                    WHERE code = ?
                    """,
                    (
                        state,
                        start_ms,
                        end_ms,
                        datetime.now().astimezone().isoformat(timespec="seconds"),
                        voucher_code,
                    ),
                )
        except Exception:
            pass

    return {
        "state": state,
        "message": None,
        "voucher_code": voucher_code,
        "ssid": live.get("ssid") or "Aura Voucher WiFi",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "remaining_seconds": int(remaining_seconds),
    }


def render_customer_page():
    customer = format_customer_status(request.remote_addr)
    return render_template("customer.html", customer=customer)


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


def require_csrf():
    expected = session.get("csrf_token", "")
    supplied = request.form.get("csrf_token", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        abort(400)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/")
def home():
    return render_customer_page()


@app.get("/status")
def customer_status():
    return render_customer_page()


@app.get("/health")
def health():
    # Keep the public health endpoint deliberately minimal for guest clients.
    return {"status": "ok", "app": "Aura Voucher WiFi"}


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        require_csrf()
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["admin_logged_in"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            session.permanent = True
            return redirect(url_for("admin_dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.post("/admin/logout")
@login_required
def logout():
    require_csrf()
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin/")
@login_required
def admin_dashboard():
    omada = OmadaClient()
    controller = {
        "online": False,
        "version": "Unknown",
        "api_version": "Unknown",
        "omadac_id": "Unknown",
        "error": None,
    }
    live = {
        "ap_total": "—",
        "ap_online": "—",
        "connected_clients": "—",
        "error": None,
    }
    sync_error = None

    try:
        info = omada.info()
        controller.update(info)
        controller["online"] = True

        # One authenticated Omada session; avoid the old N-vouchers API loop.
        omada.login()
        live.update(omada.get_live_stats())
        sessions = omada.get_hotspot_clients()
        sync_vouchers_from_sessions(sessions)
    except Exception as exc:
        controller["error"] = str(exc)
        live["error"] = str(exc)
        sync_error = "Live sync temporarily unavailable."

    vouchers = get_vouchers(50)
    for voucher in vouchers:
        voucher["remaining"] = remaining_text(voucher)
        voucher["created_display"] = format_created(voucher.get("created_at", ""))

    counts = {
        "active": sum(v["status"] == "active" for v in vouchers),
        "unused": sum(v["status"] == "unused" for v in vouchers),
        "expired": sum(v["status"] == "expired" for v in vouchers),
        "total": len(vouchers),
    }

    return render_template(
        "dashboard.html",
        vouchers=vouchers[:15],
        counts=counts,
        plans=PLANS,
        controller=controller,
        live=live,
        sync_error=sync_error,
    )


@app.post("/admin/generate/<plan_key>")
@login_required
def generate(plan_key):
    require_csrf()
    plan = PLANS.get(plan_key)

    if not plan:
        flash("Unknown plan.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        info = OmadaClient().create_voucher(plan["name"], plan["minutes"])
        save_voucher(plan, info)
        flash(f"Voucher {info['code']} created · {plan['name']}", "success")
    except OmadaError as exc:
        flash(f"Voucher generation failed: {exc}", "error")
    except Exception as exc:
        flash(f"Voucher generation failed: {exc}", "error")

    return redirect(url_for("admin_dashboard"))


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8790, debug=False)
