from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from functools import wraps
from datetime import datetime
from pathlib import Path
from threading import Lock
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import secrets
import shutil
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

_ADMIN_CACHE = {"expires": 0.0, "data": None}
_ADMIN_CACHE_LOCK = Lock()
_ADMIN_CACHE_SECONDS = 15


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


def get_voucher_counts():
    today_prefix = datetime.now().astimezone().date().isoformat() + "%"
    with sqlite3.connect(DB) as con:
        row = con.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
              SUM(CASE WHEN status = 'unused' THEN 1 ELSE 0 END) AS unused,
              SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) AS expired,
              SUM(CASE WHEN created_at LIKE ? THEN 1 ELSE 0 END) AS today
            FROM vouchers
            """,
            (today_prefix,),
        ).fetchone()

    return {
        "total": int(row[0] or 0),
        "active": int(row[1] or 0),
        "unused": int(row[2] or 0),
        "expired": int(row[3] or 0),
        "today": int(row[4] or 0),
    }


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


def human_bytes(value):
    value = float(max(0, value or 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit in ("B", "KB"):
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def format_uptime(seconds):
    seconds = max(0, int(seconds or 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def get_system_stats():
    stats = {
        "cpu_count": os.cpu_count() or 1,
        "load1": 0.0,
        "load5": 0.0,
        "load15": 0.0,
        "load_percent": 0,
        "memory_percent": 0,
        "memory_used": "—",
        "memory_total": "—",
        "swap_percent": 0,
        "swap_used": "—",
        "swap_total": "—",
        "disk_percent": 0,
        "disk_used": "—",
        "disk_total": "—",
        "disk_free": "—",
        "uptime": "—",
        "temperature": None,
    }

    try:
        load1, load5, load15 = os.getloadavg()
        stats.update(load1=load1, load5=load5, load15=load15)
        stats["load_percent"] = min(100, round((load1 / max(1, stats["cpu_count"])) * 100))
    except Exception:
        pass

    try:
        meminfo = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            parts = raw.strip().split()
            if parts:
                meminfo[key] = int(parts[0]) * 1024

        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used = max(0, total - available)
        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)
        swap_used = max(0, swap_total - swap_free)

        stats["memory_percent"] = round((used / total) * 100) if total else 0
        stats["memory_used"] = human_bytes(used)
        stats["memory_total"] = human_bytes(total)
        stats["swap_percent"] = round((swap_used / swap_total) * 100) if swap_total else 0
        stats["swap_used"] = human_bytes(swap_used)
        stats["swap_total"] = human_bytes(swap_total)
    except Exception:
        pass

    try:
        disk = shutil.disk_usage("/")
        stats["disk_percent"] = round((disk.used / disk.total) * 100) if disk.total else 0
        stats["disk_used"] = human_bytes(disk.used)
        stats["disk_total"] = human_bytes(disk.total)
        stats["disk_free"] = human_bytes(disk.free)
    except Exception:
        pass

    try:
        uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
        stats["uptime"] = format_uptime(uptime_seconds)
    except Exception:
        pass

    try:
        temperatures = []
        for temp_file in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
            raw = float(temp_file.read_text().strip())
            celsius = raw / 1000 if raw > 200 else raw
            if 0 < celsius < 120:
                temperatures.append(celsius)
        if temperatures:
            stats["temperature"] = round(temperatures[0])
    except Exception:
        pass

    return stats


def empty_admin_snapshot():
    return {
        "controller": {
            "online": False,
            "version": "Unknown",
            "api_version": "Unknown",
            "omadac_id": "Unknown",
            "error": None,
        },
        "live": {
            "ap_total": "—",
            "ap_online": "—",
            "connected_clients": "—",
            "aps": [],
            "error": None,
        },
        "sync_error": None,
        "updated_at": datetime.now().astimezone().strftime("%I:%M:%S %p").lstrip("0"),
    }


def invalidate_admin_snapshot():
    with _ADMIN_CACHE_LOCK:
        _ADMIN_CACHE["expires"] = 0.0
        _ADMIN_CACHE["data"] = None


def get_admin_snapshot(force=False):
    now = time.monotonic()
    cached = _ADMIN_CACHE.get("data")
    if not force and cached and now < _ADMIN_CACHE.get("expires", 0):
        return cached

    with _ADMIN_CACHE_LOCK:
        now = time.monotonic()
        cached = _ADMIN_CACHE.get("data")
        if not force and cached and now < _ADMIN_CACHE.get("expires", 0):
            return cached

        snapshot = empty_admin_snapshot()
        controller = snapshot["controller"]
        live = snapshot["live"]

        try:
            omada = OmadaClient()
            controller.update(omada.info())
            controller["online"] = True
            omada.login()
            live.update(omada.get_live_stats())
            sessions = omada.get_hotspot_clients()
            sync_vouchers_from_sessions(sessions)
        except Exception as exc:
            controller["error"] = str(exc)
            live["error"] = str(exc)
            snapshot["sync_error"] = "Live controller data is temporarily unavailable."

        snapshot["updated_at"] = datetime.now().astimezone().strftime("%I:%M:%S %p").lstrip("0")
        _ADMIN_CACHE["data"] = snapshot
        _ADMIN_CACHE["expires"] = time.monotonic() + _ADMIN_CACHE_SECONDS
        return snapshot


def prepare_vouchers(limit=50):
    vouchers = get_vouchers(limit)
    for voucher in vouchers:
        voucher["remaining"] = remaining_text(voucher)
        voucher["created_display"] = format_created(voucher.get("created_at", ""))
    return vouchers


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
    snapshot = get_admin_snapshot()
    return render_template(
        "dashboard.html",
        counts=get_voucher_counts(),
        system=get_system_stats(),
        active_page="dashboard",
        **snapshot,
    )


@app.get("/admin/generate")
@login_required
def admin_generate():
    snapshot = get_admin_snapshot()
    recent = prepare_vouchers(5)
    return render_template(
        "generate.html",
        plans=PLANS,
        recent=recent,
        counts=get_voucher_counts(),
        active_page="generate",
        **snapshot,
    )


@app.get("/admin/vouchers")
@login_required
def admin_vouchers():
    snapshot = get_admin_snapshot()
    return render_template(
        "vouchers.html",
        vouchers=prepare_vouchers(100),
        counts=get_voucher_counts(),
        active_page="vouchers",
        **snapshot,
    )


@app.get("/admin/system")
@login_required
def admin_system():
    snapshot = get_admin_snapshot(force=request.args.get("refresh") == "1")
    return render_template(
        "system.html",
        system=get_system_stats(),
        active_page="system",
        **snapshot,
    )


@app.post("/admin/generate/<plan_key>")
@login_required
def generate(plan_key):
    require_csrf()
    plan = PLANS.get(plan_key)

    if not plan:
        flash("Unknown plan.", "error")
        return redirect(url_for("admin_generate"))

    try:
        info = OmadaClient().create_voucher(plan["name"], plan["minutes"])
        save_voucher(plan, info)
        invalidate_admin_snapshot()
        flash(f"Voucher {info['code']} created · {plan['name']}", "success")
    except OmadaError as exc:
        flash(f"Voucher generation failed: {exc}", "error")
    except Exception as exc:
        flash(f"Voucher generation failed: {exc}", "error")

    return redirect(url_for("admin_generate"))


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8790, debug=False)
