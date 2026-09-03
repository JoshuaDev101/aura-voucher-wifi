from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, jsonify
from functools import wraps
from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit
from pathlib import Path
from threading import Lock
from contextlib import contextmanager
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import base64
import io
import json
import fcntl
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time

from omada_api import OmadaClient, OmadaError


app = Flask(__name__)
# Flask is only reachable through local nginx. Trust its forwarded client IP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("AURA_SECRET_KEY", "dev-only-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)

ADMIN_USER = os.environ.get("AURA_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("AURA_ADMIN_PASSWORD")

if not ADMIN_PASSWORD:
    raise RuntimeError("AURA_ADMIN_PASSWORD is not set.")

MANAGER_MAX_ATTEMPTS = int(os.environ.get("AURA_MANAGER_MAX_ATTEMPTS", "5"))
MANAGER_LOCK_SECONDS = int(os.environ.get("AURA_MANAGER_LOCK_SECONDS", "300"))
MANAGER_SESSION_SECONDS = int(os.environ.get("AURA_MANAGER_SESSION_SECONDS", str(60 * 60 * 12)))
MANAGER_REMEMBER_SECONDS = int(os.environ.get("AURA_MANAGER_REMEMBER_SECONDS", str(60 * 60 * 24 * 30)))
OWNER_SESSION_SECONDS = int(os.environ.get("AURA_OWNER_SESSION_SECONDS", str(60 * 60 * 12)))

DATA_DIR = Path(os.environ.get("AURA_DATA_DIR", "/var/lib/aura"))
DB = DATA_DIR / "aura.db"

def configured_price(env_name):
    raw = str(os.environ.get(env_name, "")).strip()
    if not raw:
        return ""
    try:
        value = float(raw)
        if value < 0:
            return ""
        return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return ""


PLANS = {
    "3h": {"name": "3 Hours", "minutes": 180, "short": "180 minutes", "amount": "3", "unit": "HOURS", "price": configured_price("AURA_PRICE_3H")},
    "7h": {"name": "7 Hours", "minutes": 420, "short": "420 minutes", "amount": "7", "unit": "HOURS", "price": configured_price("AURA_PRICE_7H")},
    "1d": {"name": "1 Day", "minutes": 1440, "short": "24 hours", "amount": "1", "unit": "DAY", "price": configured_price("AURA_PRICE_1D")},
    "3d": {"name": "3 Days", "minutes": 4320, "short": "72 hours", "amount": "3", "unit": "DAYS", "price": configured_price("AURA_PRICE_3D")},
    "7d": {"name": "7 Days", "minutes": 10080, "short": "168 hours", "amount": "7", "unit": "DAYS", "price": configured_price("AURA_PRICE_7D")},
}

PRINTER_MAC = os.environ.get("AURA_PRINTER_MAC", "30:08:26:16:C8:49").strip()
PRINTER_DRIVER = Path(os.environ.get("AURA_PRINTER_DRIVER", "/opt/aura-printer-test/printer.py"))
PRINTER_SCRIPT = Path(__file__).with_name("printer_mx06.py")
BULK_PRINTER_SCRIPT = Path(__file__).with_name("printer_mx06_bulk.py")
AURA_STATUS_URL = os.environ.get("AURA_STATUS_URL", "http://192.168.1.124/status").strip()


def _default_redeem_url():
    """Build a stable local redeem endpoint from the configured status URL."""
    try:
        parsed = urlsplit(AURA_STATUS_URL)
        if parsed.scheme and parsed.netloc:
            return urlunsplit((parsed.scheme, parsed.netloc, "/redeem", "", ""))
    except Exception:
        pass
    return "http://192.168.1.124/redeem"


AURA_REDEEM_URL = os.environ.get("AURA_REDEEM_URL", _default_redeem_url()).strip()
AURA_CAPTIVE_TRIGGER_URL = os.environ.get(
    "AURA_CAPTIVE_TRIGGER_URL",
    "http://connectivitycheck.gstatic.com/generate_204",
).strip() or "http://connectivitycheck.gstatic.com/generate_204"
PRINTER_MIN_AVAILABLE_MB = int(os.environ.get("AURA_PRINTER_MIN_AVAILABLE_MB", "64"))
PRINT_JOB_DIR = DATA_DIR / "print-jobs"
PRINTER_LOCK_FILE = DATA_DIR / "mx06-printer.lock"
_PRINT_LOCK = Lock()

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
            "batch_id": "ALTER TABLE vouchers ADD COLUMN batch_id TEXT",
            "batch_index": "ALTER TABLE vouchers ADD COLUMN batch_index INTEGER",
        }

        for column, sql in migrations.items():
            if column not in existing:
                con.execute(sql)

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_access(
                id INTEGER PRIMARY KEY CHECK(id = 1),
                manager_pin_hash TEXT,
                manager_session_version INTEGER NOT NULL DEFAULT 1,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
            """
        )
        con.execute(
            "INSERT OR IGNORE INTO admin_access(id, manager_session_version, failed_attempts, locked_until) VALUES (1, 1, 0, 0)"
        )


def get_admin_access():
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT manager_pin_hash, manager_session_version, failed_attempts, locked_until, updated_at FROM admin_access WHERE id = 1"
        ).fetchone()
    if not row:
        return {
            "manager_pin_hash": None,
            "manager_session_version": 1,
            "failed_attempts": 0,
            "locked_until": 0,
            "updated_at": None,
        }
    return dict(row)


def manager_pin_set():
    return bool(get_admin_access().get("manager_pin_hash"))


def manager_pin_value(pin):
    # Pepper the low-entropy PIN with Aura's server secret before hashing.
    return f"{pin}:{app.secret_key}"


def hash_manager_pin(pin):
    # PBKDF2 is deliberately low-memory for this small Orange Pi. Online attempts
    # are separately protected by the lockout below.
    return generate_password_hash(
        manager_pin_value(pin),
        method="pbkdf2:sha256:260000",
        salt_length=16,
    )


def verify_manager_pin(pin, stored_hash):
    if not stored_hash:
        return False
    try:
        return check_password_hash(stored_hash, manager_pin_value(pin))
    except Exception:
        return False


def set_manager_pin(pin):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(DB) as con:
        con.execute(
            """
            UPDATE admin_access
               SET manager_pin_hash = ?,
                   manager_session_version = manager_session_version + 1,
                   failed_attempts = 0,
                   locked_until = 0,
                   updated_at = ?
             WHERE id = 1
            """,
            (hash_manager_pin(pin), now),
        )


def disable_manager_pin():
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(DB) as con:
        con.execute(
            """
            UPDATE admin_access
               SET manager_pin_hash = NULL,
                   manager_session_version = manager_session_version + 1,
                   failed_attempts = 0,
                   locked_until = 0,
                   updated_at = ?
             WHERE id = 1
            """,
            (now,),
        )


def record_manager_pin_success():
    with sqlite3.connect(DB) as con:
        con.execute(
            "UPDATE admin_access SET failed_attempts = 0, locked_until = 0 WHERE id = 1"
        )


def record_manager_pin_failure(access):
    attempts = int(access.get("failed_attempts") or 0) + 1
    now = int(time.time())
    if attempts >= MANAGER_MAX_ATTEMPTS:
        locked_until = now + MANAGER_LOCK_SECONDS
        with sqlite3.connect(DB) as con:
            con.execute(
                "UPDATE admin_access SET failed_attempts = 0, locked_until = ? WHERE id = 1",
                (locked_until,),
            )
        return 0, locked_until

    with sqlite3.connect(DB) as con:
        con.execute(
            "UPDATE admin_access SET failed_attempts = ?, locked_until = 0 WHERE id = 1",
            (attempts,),
        )
    return MANAGER_MAX_ATTEMPTS - attempts, 0


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


def _voucher_values(plan, info, batch_id=None, batch_index=None):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return (
        info["code"],
        plan["name"],
        int(plan["minutes"]),
        now,
        derive_status(info.get("start_time"), info.get("end_time")),
        info.get("group_id"),
        info.get("voucher_id"),
        int(info.get("start_time") or 0),
        int(info.get("end_time") or 0),
        now,
        batch_id,
        batch_index,
    )


def save_voucher(plan, info, batch_id=None, batch_index=None):
    with sqlite3.connect(DB) as con:
        cursor = con.execute(
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
                last_sync,
                batch_id,
                batch_index
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _voucher_values(plan, info, batch_id, batch_index),
        )
        return int(cursor.lastrowid)


def save_bulk_vouchers(plan, infos, batch_id):
    """Save one Omada bulk group in a single local SQLite transaction."""
    ids = []
    with sqlite3.connect(DB) as con:
        for index, info in enumerate(infos, start=1):
            cursor = con.execute(
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
                    last_sync,
                    batch_id,
                    batch_index
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _voucher_values(plan, info, batch_id, index),
            )
            ids.append(int(cursor.lastrowid))
    return ids


def get_batch_vouchers(batch_id):
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT * FROM vouchers
             WHERE batch_id = ?
             ORDER BY COALESCE(batch_index, id), id
            """,
            (str(batch_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_recent_bulk_batches(limit=6):
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT batch_id,
                   MIN(plan_name) AS plan_name,
                   MIN(duration_minutes) AS duration_minutes,
                   COUNT(*) AS quantity,
                   MIN(created_at) AS created_at
              FROM vouchers
             WHERE batch_id IS NOT NULL AND batch_id <> ''
             GROUP BY batch_id
             ORDER BY MAX(id) DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["created_display"] = format_created(item.get("created_at") or "")
        result.append(item)
    return result


def voucher_redeem_url(code):
    """Return the local QR redeem URL for one voucher code."""
    code = str(code or "").strip()
    separator = "&" if "?" in AURA_REDEEM_URL else "?"
    return f"{AURA_REDEEM_URL}{separator}{urlencode({'code': code})}"


def voucher_qr_data_uri(code):
    """Create a local QR that opens Aura redeem with this code pre-filled."""
    try:
        import qrcode
    except ImportError:
        return ""

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=2,
    )
    qr.add_data(voucher_redeem_url(code))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("L")
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def get_voucher(voucher_id):
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM vouchers WHERE id = ?", (int(voucher_id),)).fetchone()
    return dict(row) if row else None


def price_for_voucher(voucher):
    minutes = int(voucher.get("duration_minutes") or 0)
    for plan in PLANS.values():
        if int(plan["minutes"]) == minutes:
            return plan.get("price") or ""
    return ""


def available_memory_mb():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def get_printer_status():
    configured = bool(PRINTER_MAC and PRINTER_SCRIPT.exists() and PRINTER_DRIVER.exists())
    return {
        "configured": configured,
        "bulk_ready": bool(configured and BULK_PRINTER_SCRIPT.exists()),
        "name": "MX06",
        "mac": PRINTER_MAC,
        "mode": "On-demand BLE",
        "driver": "Ready" if PRINTER_DRIVER.exists() else "Driver missing",
    }


@contextmanager
def printer_process_lock():
    """Cross-process lock shared by single and bulk MX06 printing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with PRINTER_LOCK_FILE.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Printer is busy. Wait for the current print to finish.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _job_path(job_id, suffix="status"):
    safe = str(job_id or "")
    if len(safe) != 20 or any(ch not in "0123456789abcdef" for ch in safe):
        return None
    PRINT_JOB_DIR.mkdir(parents=True, exist_ok=True)
    return PRINT_JOB_DIR / f"{safe}.{suffix}.json"


def _write_job_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def get_print_job(job_id):
    path = _job_path(job_id, "status")
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def start_bulk_print_job(batch_id):
    vouchers = get_batch_vouchers(batch_id)
    if not vouchers:
        raise RuntimeError("Bulk voucher batch not found.")
    if not get_printer_status().get("bulk_ready"):
        raise RuntimeError("MX06 bulk printer helper is not installed.")

    available = available_memory_mb()
    if available and available < PRINTER_MIN_AVAILABLE_MB:
        raise RuntimeError(
            f"Printing paused: only {available} MB RAM is available. Try again after memory pressure drops."
        )

    job_id = secrets.token_hex(10)
    payload_path = _job_path(job_id, "payload")
    status_path = _job_path(job_id, "status")
    payload = {
        "job_id": job_id,
        "batch_id": str(batch_id),
        "quantity": len(vouchers),
        "vouchers": [
            {
                "code": str(v.get("code") or ""),
                "validity": str(v.get("plan_name") or "Voucher"),
                "price": price_for_voucher(v),
                "qr_url": voucher_redeem_url(v.get("code") or ""),
            }
            for v in vouchers
        ],
    }
    _write_job_json(payload_path, payload)
    _write_job_json(
        status_path,
        {
            "ok": True,
            "state": "queued",
            "job_id": job_id,
            "batch_id": str(batch_id),
            "quantity": len(vouchers),
            "message": f"Preparing {len(vouchers)} vouchers for the MX06.",
        },
    )

    command = [
        sys.executable,
        str(BULK_PRINTER_SCRIPT),
        "--payload", str(payload_path),
        "--status-file", str(status_path),
        "--mac", PRINTER_MAC,
        "--driver", str(PRINTER_DRIVER),
        "--lock-file", str(PRINTER_LOCK_FILE),
    ]
    try:
        subprocess.Popen(
            command,
            cwd=str(BULK_PRINTER_SCRIPT.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as exc:
        _write_job_json(
            status_path,
            {
                "ok": False,
                "state": "error",
                "job_id": job_id,
                "batch_id": str(batch_id),
                "quantity": len(vouchers),
                "message": f"Could not start bulk printing: {exc}",
            },
        )
        raise RuntimeError(f"Could not start bulk printing: {exc}") from exc

    return job_id, len(vouchers)


def print_voucher_receipt(voucher):
    if not get_printer_status()["configured"]:
        raise RuntimeError("MX06 printer driver is not configured.")

    available = available_memory_mb()
    if available and available < PRINTER_MIN_AVAILABLE_MB:
        raise RuntimeError(
            f"Printing paused: only {available} MB RAM is available. Try again after memory pressure drops."
        )

    if not _PRINT_LOCK.acquire(blocking=False):
        raise RuntimeError("Printer is busy. Wait for the current print to finish.")

    try:
        with printer_process_lock():
            price = price_for_voucher(voucher)
            command = [
                sys.executable,
                str(PRINTER_SCRIPT),
                "--code", str(voucher.get("code") or ""),
                "--validity", str(voucher.get("plan_name") or "Voucher"),
                "--price", price,
                "--status-url", AURA_STATUS_URL,
                "--mac", PRINTER_MAC,
                "--driver", str(PRINTER_DRIVER),
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "Unknown printer error").strip().splitlines()
                raise RuntimeError(detail[-1] if detail else "Printer failed.")
            return True
    finally:
        _PRINT_LOCK.release()


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


def format_epoch_ms(value):
    try:
        ms = int(value or 0)
        if ms <= 0:
            return "—"
        return datetime.fromtimestamp(ms / 1000).astimezone().strftime(
            "%b %d · %I:%M %p"
        ).replace(" 0", " ")
    except Exception:
        return "—"


def format_remaining_seconds(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds <= 0:
        return "Expired"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    if hours:
        return f"{hours:02d}h {minutes:02d}m"
    return f"{minutes:02d}m {secs:02d}s"


def prepare_client_sessions(sessions, limit=120):
    """Create a tiny display snapshot from the hotspot data we already fetch.

    No extra Omada request is made for the Clients page. This deliberately
    reuses the same cached hotspot-client response used for voucher syncing.
    """
    now_ms = int(time.time() * 1000)
    prepared = []

    for row in sessions or []:
        voucher_code = str(row.get("voucherCode") or "").strip()
        if not voucher_code:
            continue

        start_ms = int(row.get("start") or 0)
        end_ms = int(row.get("end") or 0)
        valid = bool(row.get("valid")) and (end_ms <= 0 or end_ms > now_ms)
        remaining_seconds = max(0, (end_ms - now_ms) // 1000) if end_ms > 0 else 0

        device_name = str(row.get("name") or "").strip()
        mac = str(row.get("mac") or "").strip()
        ip = str(row.get("ip") or "").strip()

        prepared.append({
            "name": device_name or mac or ip or "Unknown device",
            "mac": mac or "—",
            "ip": ip or "—",
            "ssid": str(row.get("ssid") or "Aura Voucher WiFi"),
            "voucher_code": voucher_code,
            "status": "active" if valid else "expired",
            "valid": valid,
            "download": human_bytes(row.get("download") or 0),
            "upload": human_bytes(row.get("upload") or 0),
            "download_bytes": int(row.get("download") or 0),
            "upload_bytes": int(row.get("upload") or 0),
            "elapsed": format_uptime(row.get("duration") or 0),
            "remaining": format_remaining_seconds(remaining_seconds) if valid else "Expired",
            "remaining_seconds": int(remaining_seconds),
            "start": format_epoch_ms(start_ms),
            "end": format_epoch_ms(end_ms),
            "start_ms": start_ms,
            "end_ms": end_ms,
        })

    # Active first, newest sessions first. Keep the cached object intentionally small.
    prepared.sort(
        key=lambda row: (
            1 if row["status"] == "active" else 0,
            row["start_ms"],
            row["end_ms"],
        ),
        reverse=True,
    )
    return prepared[: max(1, int(limit))]


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
        "clients": {
            "active": [],
            "recent": [],
            "active_count": 0,
            "session_count": 0,
            "other_connected": "—",
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

            client_rows = prepare_client_sessions(sessions)
            active_rows = [row for row in client_rows if row["status"] == "active"]

            # Deduplicate current sessions by device so one phone is one card.
            active_by_device = {}
            for row in active_rows:
                key = row["mac"] if row["mac"] != "—" else row["ip"]
                previous = active_by_device.get(key)
                if previous is None or row["start_ms"] > previous["start_ms"]:
                    active_by_device[key] = row

            active_rows = list(active_by_device.values())
            active_rows.sort(key=lambda row: row["start_ms"], reverse=True)

            snapshot["clients"]["active"] = active_rows[:24]
            snapshot["clients"]["recent"] = client_rows[:100]
            snapshot["clients"]["active_count"] = len(active_rows)
            snapshot["clients"]["session_count"] = len(client_rows)

            try:
                connected = int(live.get("connected_clients") or 0)
                snapshot["clients"]["other_connected"] = max(0, connected - len(active_rows))
            except (TypeError, ValueError):
                snapshot["clients"]["other_connected"] = "—"
        except Exception as exc:
            controller["error"] = str(exc)
            live["error"] = str(exc)
            snapshot["sync_error"] = "Live controller data is temporarily unavailable."

        snapshot["updated_at"] = datetime.now().astimezone().strftime("%I:%M:%S %p").lstrip("0")
        _ADMIN_CACHE["data"] = snapshot
        _ADMIN_CACHE["expires"] = time.monotonic() + _ADMIN_CACHE_SECONDS
        return snapshot


def prepare_vouchers(limit=50, client_rows=None):
    vouchers = get_vouchers(limit)
    latest_by_code = {}

    for row in client_rows or []:
        code = str(row.get("voucher_code") or "").strip()
        if not code:
            continue
        previous = latest_by_code.get(code)
        if previous is None or int(row.get("start_ms") or 0) > int(previous.get("start_ms") or 0):
            latest_by_code[code] = row

    for voucher in vouchers:
        voucher["remaining"] = remaining_text(voucher)
        voucher["created_display"] = format_created(voucher.get("created_at", ""))
        used = latest_by_code.get(voucher.get("code"))
        voucher["used_by"] = used.get("name") if used else None
        voucher["used_mac"] = used.get("mac") if used else None
        voucher["used_status"] = used.get("status") if used else None
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


def _admin_session_valid():
    if not session.get("admin_logged_in"):
        return False

    expires_at = int(session.get("admin_expires_at") or 0)
    if expires_at and int(time.time()) >= expires_at:
        return False

    role = session.get("admin_role")
    if role == "owner":
        return True

    if role == "manager":
        access = get_admin_access()
        if not access.get("manager_pin_hash"):
            return False
        return int(session.get("manager_session_version") or 0) == int(
            access.get("manager_session_version") or 0
        )

    return False


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _admin_session_valid():
            session.clear()
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _admin_session_valid():
            session.clear()
            return redirect(url_for("admin_owner_login"))
        if session.get("admin_role") != "owner":
            flash("Owner access is required for that page.", "warning")
            return redirect(url_for("admin_dashboard"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_admin_identity():
    return {
        "admin_role": session.get("admin_role", ""),
        "is_owner": session.get("admin_role") == "owner",
        "is_manager": session.get("admin_role") == "manager",
    }


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


@app.get("/redeem")
def redeem_voucher():
    """Open a printed QR voucher with the least-visible captive bridge possible.

    An already-authorized client goes straight to /status, so it never lands on
    the captive-trigger site. An unauthenticated client stores the scanned code
    briefly, then opens a plain-HTTP connectivity check which Omada intercepts.
    The matching custom portal reads the code and auto-submits it.
    """
    code = str(request.args.get("code", "")).strip()
    if not code or len(code) > 64 or any(ch.isspace() for ch in code):
        return redirect(url_for("customer_status"))

    # If this phone is already authorized, never send it to the captive trigger.
    # This also fixes the old behavior where an already-connected test phone
    # could visibly end up on NeverSSL.
    try:
        customer = format_customer_status(request.remote_addr)
        if customer.get("state") == "active":
            return redirect(url_for("customer_status"), code=302)
    except Exception:
        # If status detection fails, still allow the normal captive flow below.
        pass

    separator = "&" if "?" in AURA_CAPTIVE_TRIGGER_URL else "?"
    target = f"{AURA_CAPTIVE_TRIGGER_URL}{separator}{urlencode({'aura_voucher': code})}"
    response = redirect(target, code=302)
    response.set_cookie(
        "aura_voucher_code",
        code,
        max_age=10 * 60,
        path="/",
        secure=False,
        httponly=False,
        samesite="Lax",
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/health")
def health():
    return {"status": "ok", "app": "Aura Voucher WiFi"}


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    access = get_admin_access()
    pin_configured = bool(access.get("manager_pin_hash"))
    now = int(time.time())
    locked_for = max(0, int(access.get("locked_until") or 0) - now)

    if request.method == "POST":
        require_csrf()
        ajax = request.headers.get("X-Aura-Login-UI") == "1"
        pin = request.form.get("pin", "").strip()
        remember = request.form.get("remember") == "1"

        def login_response(ok, message, status=200, redirect_to=None, remaining=None, locked_seconds=0):
            if ajax:
                payload = {
                    "ok": ok,
                    "message": message,
                    "redirect": redirect_to or "",
                    "remaining": remaining,
                    "locked_seconds": int(locked_seconds or 0),
                }
                return jsonify(payload), status
            if ok:
                return redirect(redirect_to or url_for("admin_dashboard"))
            flash(message, "error")
            return None

        if not pin_configured:
            response = login_response(False, "Manager PIN is not set yet. Ask the owner to set it in Settings.", 403)
            if response is not None:
                return response
        elif locked_for > 0:
            mins = max(1, (locked_for + 59) // 60)
            response = login_response(False, f"Too many attempts. Try again in about {mins} minute(s).", 429, locked_seconds=locked_for)
            if response is not None:
                return response
        elif len(pin) != 6 or not pin.isdigit():
            response = login_response(False, "Enter your 6-digit manager PIN.", 400)
            if response is not None:
                return response
        elif verify_manager_pin(pin, access.get("manager_pin_hash")):
            record_manager_pin_success()
            session.clear()
            session["admin_logged_in"] = True
            session["admin_role"] = "manager"
            session["manager_session_version"] = int(access.get("manager_session_version") or 1)
            session["csrf_token"] = secrets.token_urlsafe(32)
            duration = MANAGER_REMEMBER_SECONDS if remember else MANAGER_SESSION_SECONDS
            session["admin_expires_at"] = int(time.time()) + duration
            session.permanent = remember
            response = login_response(True, "PIN accepted. Opening Aura Admin…", 200, url_for("admin_dashboard"))
            if response is not None:
                return response
        else:
            remaining, locked_until = record_manager_pin_failure(access)
            if locked_until:
                message = "Too many incorrect PIN attempts. Manager login is locked for 5 minutes."
                response = login_response(False, message, 429, remaining=0, locked_seconds=MANAGER_LOCK_SECONDS)
            else:
                word = "attempt" if remaining == 1 else "attempts"
                response = login_response(False, f"Incorrect PIN. {remaining} {word} remaining.", 401, remaining=remaining)
            if response is not None:
                return response

    return render_template(
        "login.html",
        manager_pin_set=pin_configured,
        locked_for=locked_for,
    )


@app.route("/admin/owner-login", methods=["GET", "POST"])
def admin_owner_login():
    if request.method == "POST":
        require_csrf()
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["admin_logged_in"] = True
            session["admin_role"] = "owner"
            session["csrf_token"] = secrets.token_urlsafe(32)
            session["admin_expires_at"] = int(time.time()) + OWNER_SESSION_SECONDS
            session.permanent = False
            return redirect(url_for("admin_dashboard"))

        flash("Invalid owner username or password.", "error")

    return render_template("owner_login.html")


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
    recent = prepare_vouchers(5, snapshot["clients"]["recent"])
    return render_template(
        "generate.html",
        plans=PLANS,
        recent=recent,
        counts=get_voucher_counts(),
        printer=get_printer_status(),
        active_page="generate",
        **snapshot,
    )


@app.get("/admin/bulk")
@login_required
def admin_bulk():
    snapshot = get_admin_snapshot()
    return render_template(
        "bulk.html",
        plans=PLANS,
        batches=get_recent_bulk_batches(8),
        printer=get_printer_status(),
        active_page="generate",
        **snapshot,
    )


@app.post("/admin/bulk/generate")
@login_required
def admin_bulk_generate():
    require_csrf()
    plan_key = str(request.form.get("plan") or "").strip().lower()
    try:
        quantity = int(request.form.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0

    plan = PLANS.get(plan_key)
    allowed_quantities = {10, 20, 30, 40, 50}
    if not plan:
        flash("Choose a valid voucher duration.", "error")
        return redirect(url_for("admin_bulk"))
    if quantity not in allowed_quantities:
        flash("Bulk quantity must be 10, 20, 30, 40, or 50 vouchers.", "error")
        return redirect(url_for("admin_bulk"))

    batch_id = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
    try:
        infos = OmadaClient().create_vouchers(plan["name"], plan["minutes"], quantity)
        if not infos:
            raise OmadaError("Omada returned no voucher codes.")
        save_bulk_vouchers(plan, infos, batch_id)
        invalidate_admin_snapshot()
        if len(infos) != quantity:
            flash(
                f"Omada returned {len(infos)} of {quantity} requested vouchers. The available codes were saved.",
                "warning",
            )
        else:
            flash(f"{quantity} {plan['name']} vouchers created. Batch is ready for the MX06.", "success")
        return redirect(url_for("admin_bulk_batch", batch_id=batch_id))
    except OmadaError as exc:
        flash(f"Bulk voucher generation failed: {exc}", "error")
    except Exception as exc:
        flash(f"Bulk voucher generation failed: {exc}", "error")
    return redirect(url_for("admin_bulk"))


@app.get("/admin/bulk/<batch_id>")
@login_required
def admin_bulk_batch(batch_id):
    vouchers = get_batch_vouchers(batch_id)
    if not vouchers:
        abort(404)

    for voucher in vouchers:
        voucher["price"] = price_for_voucher(voucher)
        voucher["qr_data_uri"] = voucher_qr_data_uri(voucher.get("code") or "")

    first = vouchers[0]
    snapshot = get_admin_snapshot()
    return render_template(
        "bulk_batch.html",
        vouchers=vouchers,
        batch_id=batch_id,
        plan_name=first.get("plan_name") or "Voucher",
        price=price_for_voucher(first),
        quantity=len(vouchers),
        printer=get_printer_status(),
        active_page="generate",
        **snapshot,
    )


@app.get("/admin/bulk/<batch_id>/sheet")
@login_required
def admin_bulk_sheet(batch_id):
    # Backward-compatible URL from v14 short-bond sheets.
    return redirect(url_for("admin_bulk_batch", batch_id=batch_id))


@app.post("/admin/bulk/<batch_id>/print")
@login_required
def admin_bulk_print(batch_id):
    require_csrf()
    try:
        job_id, quantity = start_bulk_print_job(batch_id)
        return jsonify(
            ok=True,
            job_id=job_id,
            quantity=quantity,
            status_url=url_for("admin_print_job_status", job_id=job_id),
            message=f"Printing {quantity} vouchers on the MX06.",
        )
    except Exception as exc:
        return jsonify(ok=False, message=str(exc)), 500


@app.get("/admin/print-jobs/<job_id>")
@login_required
def admin_print_job_status(job_id):
    job = get_print_job(job_id)
    if not job:
        return jsonify(ok=False, state="error", message="Print job not found."), 404
    return jsonify(job)


@app.get("/admin/vouchers")
@login_required
def admin_vouchers():
    snapshot = get_admin_snapshot()
    return render_template(
        "vouchers.html",
        vouchers=prepare_vouchers(100, snapshot["clients"]["recent"]),
        counts=get_voucher_counts(),
        active_page="vouchers",
        **snapshot,
    )


@app.get("/admin/clients")
@login_required
def admin_clients():
    snapshot = get_admin_snapshot(force=request.args.get("refresh") == "1")
    return render_template(
        "clients.html",
        active_page="clients",
        **snapshot,
    )


@app.get("/admin/system")
@owner_required
def admin_system():
    snapshot = get_admin_snapshot(force=request.args.get("refresh") == "1")
    return render_template(
        "system.html",
        system=get_system_stats(),
        printer=get_printer_status(),
        active_page="system",
        **snapshot,
    )


@app.get("/admin/settings")
@owner_required
def admin_settings():
    snapshot = get_admin_snapshot()
    access = get_admin_access()
    return render_template(
        "settings.html",
        manager_access=access,
        owner_username=ADMIN_USER,
        active_page="settings",
        **snapshot,
    )


@app.post("/admin/settings/manager-pin")
@owner_required
def admin_set_manager_pin():
    require_csrf()
    pin = request.form.get("pin", "").strip()
    confirm = request.form.get("confirm_pin", "").strip()

    if len(pin) != 6 or not pin.isdigit():
        flash("Manager PIN must be exactly 6 digits.", "error")
    elif pin != confirm:
        flash("PIN confirmation does not match.", "error")
    else:
        set_manager_pin(pin)
        flash("Manager PIN saved. Existing manager sessions were signed out.", "success")

    return redirect(url_for("admin_settings"))


@app.post("/admin/settings/manager-pin/disable")
@owner_required
def admin_disable_manager_pin():
    require_csrf()
    disable_manager_pin()
    flash("Manager PIN access disabled. Existing manager sessions were signed out.", "success")
    return redirect(url_for("admin_settings"))


def print_ui_request():
    return request.headers.get("X-Aura-Print-UI") == "1"


@app.post("/admin/generate/<plan_key>")
@login_required
def generate(plan_key):
    require_csrf()
    plan = PLANS.get(plan_key)

    if not plan:
        if print_ui_request():
            return jsonify(ok=False, message="Unknown voucher plan."), 400
        flash("Unknown plan.", "error")
        return redirect(url_for("admin_generate"))

    print_after = request.form.get("print_after") == "1"

    try:
        info = OmadaClient().create_voucher(plan["name"], plan["minutes"])
        voucher_id = save_voucher(plan, info)
        invalidate_admin_snapshot()

        if print_after:
            voucher = get_voucher(voucher_id)
            try:
                print_voucher_receipt(voucher)
                if print_ui_request():
                    return jsonify(
                        ok=True,
                        printed=True,
                        code=info["code"],
                        plan=plan["name"],
                        message="Voucher printed successfully.",
                    )
                flash(f"Voucher {info['code']} created + printed · {plan['name']}", "success")
            except Exception as print_exc:
                if print_ui_request():
                    return jsonify(
                        ok=False,
                        created=True,
                        code=info["code"],
                        plan=plan["name"],
                        message=f"Voucher created, but printing failed: {print_exc}",
                        retry_url=url_for("print_voucher", voucher_id=voucher_id),
                    ), 500
                flash(f"Voucher {info['code']} was created, but printing failed: {print_exc}", "warning")
        else:
            flash(f"Voucher {info['code']} created · {plan['name']}", "success")
    except OmadaError as exc:
        if print_after and print_ui_request():
            return jsonify(ok=False, message=f"Voucher generation failed: {exc}"), 502
        flash(f"Voucher generation failed: {exc}", "error")
    except Exception as exc:
        if print_after and print_ui_request():
            return jsonify(ok=False, message=f"Voucher generation failed: {exc}"), 500
        flash(f"Voucher generation failed: {exc}", "error")

    return redirect(url_for("admin_generate"))


@app.post("/admin/print/<int:voucher_id>")
@login_required
def print_voucher(voucher_id):
    require_csrf()
    voucher = get_voucher(voucher_id)
    if not voucher:
        if print_ui_request():
            return jsonify(ok=False, message="Voucher not found."), 404
        abort(404)

    try:
        print_voucher_receipt(voucher)
        if print_ui_request():
            return jsonify(
                ok=True,
                printed=True,
                code=voucher["code"],
                plan=voucher.get("plan_name") or "Voucher",
                message="Voucher printed successfully.",
            )
        flash(f"Voucher {voucher['code']} sent to MX06.", "success")
    except Exception as exc:
        if print_ui_request():
            return jsonify(
                ok=False,
                code=voucher.get("code"),
                message=f"Print failed: {exc}",
                retry_url=url_for("print_voucher", voucher_id=voucher_id),
            ), 500
        flash(f"Print failed: {exc}", "error")

    destination = request.form.get("return_to", "generate")
    if destination == "vouchers":
        return redirect(url_for("admin_vouchers"))
    return redirect(url_for("admin_generate"))


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8790, debug=False)
