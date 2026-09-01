from flask import Flask, render_template, request, redirect, url_for, session, flash
from functools import wraps
from datetime import datetime
from pathlib import Path
import os, secrets, sqlite3
from omada_api import OmadaClient, OmadaError

app=Flask(__name__)
app.secret_key=os.environ.get("AURA_SECRET_KEY","dev-only-change-me")
ADMIN_USER=os.environ.get("AURA_ADMIN_USER","admin")
ADMIN_PASSWORD=os.environ.get("AURA_ADMIN_PASSWORD")
if not ADMIN_PASSWORD: raise RuntimeError("AURA_ADMIN_PASSWORD is not set.")

DATA_DIR=Path(os.environ.get("AURA_DATA_DIR","/var/lib/aura"))
DB=DATA_DIR/"aura.db"
PLANS={"1d":{"name":"1 Day","minutes":1440},"3d":{"name":"3 Days","minutes":4320},"7d":{"name":"7 Days","minutes":10080}}

def init_db():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS vouchers(
          id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
          plan_name TEXT NOT NULL, duration_minutes INTEGER NOT NULL,
          created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unused',
          omada_group_id TEXT, omada_voucher_id TEXT)""")

def rows():
    with sqlite3.connect(DB) as c:
        c.row_factory=sqlite3.Row
        return [dict(x) for x in c.execute("SELECT * FROM vouchers ORDER BY id DESC LIMIT 50")]

def save(plan,info):
    with sqlite3.connect(DB) as c:
        c.execute("""INSERT OR REPLACE INTO vouchers
          (code,plan_name,duration_minutes,created_at,status,omada_group_id,omada_voucher_id)
          VALUES (?,?,?,?,?,?,?)""",(info["code"],plan["name"],plan["minutes"],
          datetime.now().astimezone().isoformat(timespec="seconds"),"unused",info.get("group_id"),info.get("voucher_id")))

def required(f):
    @wraps(f)
    def w(*a,**k):
        if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
        return f(*a,**k)
    return w

@app.get("/")
def home(): return render_template("customer.html")

@app.get("/health")
def health(): return {"status":"ok","app":"Aura Voucher WiFi","omada":OmadaClient().probe()}

@app.route("/admin/login",methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        if secrets.compare_digest(request.form.get("username",""),ADMIN_USER) and secrets.compare_digest(request.form.get("password",""),ADMIN_PASSWORD):
            session["admin_logged_in"]=True; return redirect(url_for("admin_dashboard"))
        flash("Invalid username or password.","error")
    return render_template("login.html")

@app.post("/admin/logout")
@required
def logout(): session.clear(); return redirect(url_for("admin_login"))

@app.get("/admin/")
@required
def admin_dashboard():
    v=rows(); p=OmadaClient().probe()
    mock={"controller_online":p["online"],"controller_version":p["version"],"controller_api_version":p["api_version"],
          "controller_id":p["omadac_id"],"controller_error":p["error"],"ap_online":"—","ap_total":"—",
          "connected_clients":"—","printer_name":"MX06","printer_battery":"Unknown","printer_connected":False}
    counts={"active":sum(x["status"]=="active" for x in v),"unused":sum(x["status"]=="unused" for x in v),
            "expired":sum(x["status"]=="expired" for x in v),"total":len(v)}
    return render_template("dashboard.html",mock=mock,vouchers=v[:12],counts=counts,plans=PLANS)

@app.post("/admin/generate/<plan_key>")
@required
def generate(plan_key):
    plan=PLANS.get(plan_key)
    if not plan: flash("Unknown plan.","error"); return redirect(url_for("admin_dashboard"))
    try:
        info=OmadaClient().create_voucher(plan["name"],plan["minutes"]); save(plan,info)
        flash(f"REAL Omada voucher {info['code']} created for {plan['name']}.","success")
    except Exception as e:
        flash(f"Voucher generation failed: {e}","error")
    return redirect(url_for("admin_dashboard"))

if __name__=="__main__":
    init_db()
    app.run(host="127.0.0.1",port=8790,debug=False)
