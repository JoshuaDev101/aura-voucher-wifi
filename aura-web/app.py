from flask import Flask, render_template, request, redirect, url_for, session, flash
from functools import wraps
from datetime import datetime, timedelta
import os
import secrets
import string

from omada_api import OmadaClient

app = Flask(__name__)
app.secret_key = os.environ.get("AURA_SECRET_KEY", "dev-only-change-me")

ADMIN_USER = os.environ.get("AURA_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("AURA_ADMIN_PASSWORD")

if not ADMIN_PASSWORD:
    raise RuntimeError(
        "AURA_ADMIN_PASSWORD is not set. "
        "Set it before running."
    )

# UI-development state. Omada controller status is now real;
# client/AP/voucher data stays mock until we verify the 5.15 authenticated API.
mock_base = {
    "ap_online": 1,
    "ap_total": 1,
    "connected_clients": 4,
    "printer_connected": True,
    "printer_name": "MX06",
    "printer_battery": "Unknown",
}

vouchers = []

PLANS = {
    "1d": {"name": "1 Day", "minutes": 1440},
    "3d": {"name": "3 Days", "minutes": 4320},
    "7d": {"name": "7 Days", "minutes": 10080},
}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def make_code(length=6):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_dashboard_state():
    probe = OmadaClient().probe()
    state = dict(mock_base)
    state.update({
        "controller_online": probe["online"],
        "controller_version": probe["version"],
        "controller_api_version": probe["api_version"],
        "controller_id": probe["omadac_id"],
        "controller_error": probe["error"],
    })
    return state


@app.get("/")
def customer_home():
    return render_template("customer.html")


@app.get("/health")
def health():
    probe = OmadaClient().probe()
    return {
        "status": "ok",
        "app": "Aura Voucher WiFi",
        "omada": probe,
    }


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.post("/admin/logout")
@login_required
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin/")
@login_required
def admin_dashboard():
    counts = {
        "active": sum(v["status"] == "active" for v in vouchers),
        "unused": sum(v["status"] == "unused" for v in vouchers),
        "expired": sum(v["status"] == "expired" for v in vouchers),
        "total": len(vouchers),
    }
    return render_template(
        "dashboard.html",
        mock=get_dashboard_state(),
        vouchers=list(reversed(vouchers[-12:])),
        counts=counts,
        plans=PLANS,
    )


@app.post("/admin/generate/<plan_key>")
@login_required
def generate_voucher(plan_key):
    plan = PLANS.get(plan_key)
    if not plan:
        flash("Unknown plan.", "error")
        return redirect(url_for("admin_dashboard"))

    # Still mock. Real Omada voucher creation comes after live 5.15 API capture.
    now = datetime.now()
    voucher = {
        "code": make_code(),
        "plan": plan["name"],
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "unused",
        "expires_at": None,
    }
    vouchers.append(voucher)
    flash(
        f"Mock voucher {voucher['code']} generated for {plan['name']}. "
        "Real Omada generation is the next milestone.",
        "success",
    )
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/mock/activate/<code>")
@login_required
def mock_activate(code):
    for v in vouchers:
        if v["code"] == code and v["status"] == "unused":
            plan = next((p for p in PLANS.values() if p["name"] == v["plan"]), None)
            minutes = plan["minutes"] if plan else 60
            v["status"] = "active"
            v["expires_at"] = (
                datetime.now() + timedelta(minutes=minutes)
            ).strftime("%Y-%m-%d %H:%M:%S")
            flash(f"{code} marked active (demo only).", "success")
            break
    return redirect(url_for("admin_dashboard"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8790, debug=True)
