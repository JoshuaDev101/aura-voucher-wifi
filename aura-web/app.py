from flask import Flask, render_template, request, redirect, url_for, session, flash
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
import os
import secrets
import sqlite3
import time

from omada_api import OmadaClient, OmadaError


app = Flask(__name__)
app.secret_key = os.environ.get("AURA_SECRET_KEY", "dev-only-change-me")

ADMIN_USER = os.environ.get("AURA_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("AURA_ADMIN_PASSWORD")

if not ADMIN_PASSWORD:
    raise RuntimeError("AURA_ADMIN_PASSWORD is not set.")

DATA_DIR = Path(os.environ.get("AURA_DATA_DIR", "/var/lib/aura"))
DB = DATA_DIR / "aura.db"

PLANS = {
    "1d": {"name": "1 Day", "minutes": 1440},
    "3d": {"name": "3 Days", "minutes": 4320},
    "7d": {"name": "7 Days", "minutes": 10080},
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

    # Omada uses a very large value for "no finite end yet" on unused vouchers.
    if 0 < end < 9_000_000_000_000_000_000 and now_ms >= end:
        return "expired"

    return "active"


def sync_voucher_statuses(limit=25):
    vouchers = get_vouchers(limit)
    candidates = [
        v for v in vouchers
        if v.get("omada_group_id") and v.get("status") != "expired"
    ]

    if not candidates:
        return None

    client = OmadaClient()

    try:
        client.login()
    except Exception as exc:
        return str(exc)

    with sqlite3.connect(DB) as con:
        for item in candidates:
            try:
                live = client.get_voucher_from_group(
                    item["omada_group_id"],
                    code=item.get("code"),
                    voucher_id=item.get("omada_voucher_id"),
                )

                if not live:
                    continue

                start_time = int(live.get("startTime") or 0)
                end_time = int(live.get("endTime") or 0)
                status = derive_status(start_time, end_time)

                con.execute(
                    """
                    UPDATE vouchers
                    SET status = ?,
                        start_time = ?,
                        end_time = ?,
                        last_sync = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        start_time,
                        end_time,
                        datetime.now().astimezone().isoformat(timespec="seconds"),
                        item["id"],
                    ),
                )
            except Exception:
                # One failed voucher lookup should not break the dashboard.
                continue

    return None


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


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


@app.get("/")
def home():
    return render_template("customer.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": "Aura Voucher WiFi",
        "omada": OmadaClient().probe(),
    }


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if (
            secrets.compare_digest(
                request.form.get("username", ""),
                ADMIN_USER,
            )
            and secrets.compare_digest(
                request.form.get("password", ""),
                ADMIN_PASSWORD,
            )
        ):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.post("/admin/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin/")
@login_required
def admin_dashboard():
    sync_error = sync_voucher_statuses(limit=25)

    vouchers = get_vouchers(50)
    for voucher in vouchers:
        voucher["remaining"] = remaining_text(voucher)

    probe = OmadaClient().probe()

    controller = {
        "online": probe["online"],
        "version": probe["version"],
        "api_version": probe["api_version"],
        "omadac_id": probe["omadac_id"],
        "error": probe["error"],
    }

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
        sync_error=sync_error,
    )


@app.post("/admin/generate/<plan_key>")
@login_required
def generate(plan_key):
    plan = PLANS.get(plan_key)

    if not plan:
        flash("Unknown plan.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        info = OmadaClient().create_voucher(
            plan["name"],
            plan["minutes"],
        )
        save_voucher(plan, info)
        flash(
            f"REAL Omada voucher {info['code']} created for {plan['name']}.",
            "success",
        )
    except OmadaError as exc:
        flash(f"Voucher generation failed: {exc}", "error")
    except Exception as exc:
        flash(f"Voucher generation failed: {exc}", "error")

    return redirect(url_for("admin_dashboard"))


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8790, debug=False)
