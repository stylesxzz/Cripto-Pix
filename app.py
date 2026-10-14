import os, re, sqlite3, secrets, time, threading, urllib.parse
from datetime import datetime, timezone, timedelta, date
from functools import wraps
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, session, redirect, render_template, abort, jsonify, g
from werkzeug.security import check_password_hash
import hodle, whatsapp

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
BASE = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.environ.get("FORCE_HTTPS") == "1")
DB = os.environ.get("DB_PATH", "cripto_pix_v2.db"); LIVE = os.environ.get("HODLE_LIVE") == "1"
MAX = float(os.environ.get("MAX_BRL", 2000)); MIN_SUB = float(os.environ.get("MIN_SUB_BRL", 100))
ADDR = {"Base": r"^0x[a-fA-F0-9]{40}$", "Ethereum": r"^0x[a-fA-F0-9]{40}$", "Solana": r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", "Tron": r"^T[1-9A-HJ-NP-Za-km-z]{33}$"}
ASSET = os.environ.get("WALLET_ASSET", "USDC").upper(); NET = os.environ.get("WALLET_NETWORK", "Base"); WALLET = os.environ.get("WALLET_ADDRESS", "").strip()
WALLET_OK = ASSET in ("USDC", "USDT") and NET in ADDR and bool(re.match(ADDR[NET], WALLET))
BR = timezone(timedelta(hours=-3))
def today(): return datetime.now(BR).date()
SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, code TEXT UNIQUE, customer TEXT, whatsapp TEXT, service TEXT, brl REAL, asset TEXT, network TEXT, address TEXT,
 pix_code TEXT, status TEXT, provider_id TEXT UNIQUE, txid TEXT, note TEXT, sub_id INTEGER, notified INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS subs(id INTEGER PRIMARY KEY, name TEXT, whatsapp TEXT, brl REAL, day INTEGER, next_date TEXT, active INTEGER DEFAULT 1, created TEXT);
CREATE TABLE IF NOT EXISTS events(provider_id TEXT, status TEXT, UNIQUE(provider_id,status));
CREATE TABLE IF NOT EXISTS audit(ts TEXT, actor TEXT, action TEXT, detail TEXT);
"""
def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def conn(): c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; return c
def db():
    if "db" not in g: g.db = conn()
    return g.db
@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()
def audit(c, actor, action, detail=""): c.execute("INSERT INTO audit VALUES(?,?,?,?)", (now(), actor, action, detail)); c.commit()
with conn() as c0: c0.executescript(SCHEMA)
def phone(v):
    d = re.sub(r"\D", "", v or ""); return "55" + d if len(d) in (10, 11) else d
def money(v):
    try: return round(float(v.replace(",", ".")), 2)
    except ValueError: abort(400, "Valor inválido")

def make_order(c, customer, wa, service, brl, pix="", sub_id=None):
    """Cria a cobranca (e o Pix na Hodle se LIVE) e avisa no WhatsApp quando a API esta configurada."""
    code = secrets.token_urlsafe(6); pid = None
    if LIVE:
        r = hodle.create_pix(code, brl, service[:60]); pid, pix = str(r["id"]), r["pix_code"]
    c.execute("INSERT INTO orders(code,customer,whatsapp,service,brl,asset,network,address,pix_code,status,provider_id,sub_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (code, customer[:80], wa, service[:60], brl, ASSET, NET, WALLET, pix, "AGUARDANDO_PIX", pid, sub_id, now(), now())); c.commit()
    audit(c, "sistema" if sub_id else "admin", "cobranca", f"{code} R${brl}")
    if sub_id and wa and whatsapp.configured():
        try:
            whatsapp.send_charge(wa, customer, f"{brl:.2f}".replace(".", ","), f"{BASE}/p/{code}")
            c.execute("UPDATE orders SET notified=1 WHERE code=?", (code,)); c.commit()
        except Exception as e: c.execute("UPDATE orders SET note=? WHERE code=?", ("WhatsApp falhou: " + str(e)[:120], code)); c.commit()
    return code

def add_month(d, day):
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, min(day, 28))
def due_job():
    c = conn()
    try:
        for s in c.execute("SELECT * FROM subs WHERE active=1 AND next_date<=?", (str(today()),)).fetchall():
            cur = date.fromisoformat(s["next_date"]); nxt = add_month(cur, s["day"])
            if c.execute("UPDATE subs SET next_date=? WHERE id=? AND next_date=?", (str(nxt), s["id"], s["next_date"])).rowcount != 1: continue  # evita duplicar
            c.commit()
            if (today() - cur).days > 10: audit(c, "sistema", "cobranca_pulada", f'sub {s["id"]} {cur}'); continue  # servidor ficou fora do ar
            try: make_order(c, s["name"], s["whatsapp"], f"Mensalidade {cur.strftime('%m/%Y')}", s["brl"], "", s["id"])
            except Exception as e: audit(c, "sistema", "assinatura_erro", f'sub {s["id"]}: {str(e)[:150]}')
    finally: c.close()
def loop():
    while True:
        try: due_job()
        except Exception as e: print("job:", e)
        time.sleep(60)
threading.Thread(target=loop, daemon=True).start()

def csrf(): session.setdefault("csrf", secrets.token_hex(16)); return session["csrf"]
app.jinja_env.globals["csrf"] = csrf
def admin(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin"): return redirect("/login")
        if request.method == "POST" and not secrets.compare_digest(request.form.get("_csrf", ""), session.get("csrf", "x")): abort(400)
        return f(*a, **k)
    return w
tries = {}
@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    if request.method == "POST":
        ip = request.remote_addr; t = [x for x in tries.get(ip, []) if time.time() - x < 600]
        if len(t) >= 5: return "Muitas tentativas. Aguarde 10 minutos.", 429
        if not os.environ.get("ADMIN_PASS_HASH"): return render_template("login.html", err="ADMIN_PASS_HASH não está no .env. Rode: python gerar_hash.py")
        if request.form.get("user") == os.environ.get("ADMIN_USER") and os.environ.get("ADMIN_PASS_HASH") and check_password_hash(os.environ["ADMIN_PASS_HASH"], request.form.get("password", "")):
            session.clear(); session["admin"] = True; audit(db(), "admin", "login", ip); return redirect("/admin")
        tries[ip] = t + [time.time()]; err = "Usuário ou senha incorretos."
    return render_template("login.html", err=err)
@app.route("/logout")
def logout(): session.clear(); return redirect("/login")
@app.route("/")
def home(): return redirect("/admin")

def ctx(tab):
    d = db(); rows = d.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall(); m = datetime.now(BR).strftime("%Y-%m")
    s = {"month": sum(r["brl"] for r in rows if r["status"] in ("PIX_CONFIRMADO", "ENVIADO") and r["created"].startswith(m)),
         "pending": sum(1 for r in rows if r["status"] == "AGUARDANDO_PIX"), "sent": sum(1 for r in rows if r["status"] == "ENVIADO")}
    last = session.pop("last", None)
    return dict(tab=tab, rows=rows, s=s, subs=d.execute("SELECT * FROM subs ORDER BY id DESC").fetchall(), live=LIVE, base=BASE, wallet_ok=WALLET_OK,
                wallet=f"{ASSET}/{NET} · {WALLET[:8]}…{WALLET[-4:]}" if WALLET_OK else "", wa_auto=whatsapp.configured(), min_sub=MIN_SUB,
                last=d.execute("SELECT * FROM orders WHERE code=?", (last,)).fetchone() if last else None, today=str(today()))
@app.route("/admin")
@admin
def dash(): return render_template("admin.html", **ctx("cobrancas"))
@app.route("/admin/assinaturas")
@admin
def subs_tab(): return render_template("admin.html", **ctx("assinaturas"))

@app.post("/admin/orders")
@admin
def new_order():
    if not WALLET_OK: abort(400, "Configure WALLET_ADDRESS, WALLET_ASSET e WALLET_NETWORK no .env")
    f = request.form; brl = money(f["amount"])
    if not 0 < brl <= MAX: abort(400, f"Fora do limite (máx. R$ {MAX:.0f})")
    try: code = make_order(db(), f["customer"], phone(f.get("whatsapp")), f["service"], brl, f.get("pix_code", "").strip())
    except Exception as e: audit(db(), "sistema", "hodle_erro", str(e)[:200]); abort(502, "Falha ao criar Pix na Hodle")
    session["last"] = code; return redirect("/admin")
@app.post("/admin/orders/<int:i>")
@admin
def order_action(i):
    o = db().execute("SELECT * FROM orders WHERE id=?", (i,)).fetchone() or abort(404); act = request.form.get("act")
    if act == "pix_ok" and o["status"] == "AGUARDANDO_PIX": _set(i, "PIX_CONFIRMADO")
    elif act == "setpix" and o["status"] == "AGUARDANDO_PIX": db().execute("UPDATE orders SET pix_code=? WHERE id=?", (request.form.get("pix_code", "").strip(), i)); db().commit()
    elif act == "sent" and o["status"] == "PIX_CONFIRMADO":
        tx = request.form.get("txid", "").strip() or abort(400, "Informe o TXID")
        db().execute("UPDATE orders SET txid=? WHERE id=?", (tx, i)); _set(i, "ENVIADO")
    elif act == "cancel" and o["status"] == "AGUARDANDO_PIX": _set(i, "CANCELADA")
    else: abort(400, "Ação inválida para o status atual")
    return redirect("/admin")
def _set(i, s): db().execute("UPDATE orders SET status=?, updated=? WHERE id=?", (s, now(), i)); db().commit(); audit(db(), "admin", "status", f"{i}->{s}")

@app.post("/admin/subs")
@admin
def new_sub():
    f = request.form; brl = money(f["amount"]); wa = phone(f.get("whatsapp"))
    if not MIN_SUB <= brl <= MAX: abort(400, f"Valor da assinatura: de R$ {MIN_SUB:.0f} a R$ {MAX:.0f}")
    if len(wa) < 12: abort(400, "WhatsApp inválido (use DDD + número)")
    try: first = date.fromisoformat(f.get("first") or str(today()))
    except ValueError: abort(400, "Data inválida")
    if first < today(): abort(400, "A primeira cobrança não pode ser no passado")
    db().execute("INSERT INTO subs(name,whatsapp,brl,day,next_date,created) VALUES(?,?,?,?,?,?)", (f["name"][:80], wa, brl, min(first.day, 28), str(first), now())); db().commit()
    audit(db(), "admin", "assinatura", f'{f["name"][:40]} R${brl}'); return redirect("/admin/assinaturas")
@app.post("/admin/subs/<int:i>")
@admin
def sub_action(i):
    act = request.form.get("act")
    if act in ("pause", "resume"): db().execute("UPDATE subs SET active=? WHERE id=?", (1 if act == "resume" else 0, i))
    elif act == "delete": db().execute("DELETE FROM subs WHERE id=?", (i,))
    else: abort(400)
    db().commit(); audit(db(), "admin", "assinatura_" + act, str(i)); return redirect("/admin/assinaturas")

@app.route("/p/<code>")
def pay_page(code): return render_template("pay.html", p=db().execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone() or abort(404))
@app.route("/api/status/<code>")
def status(code): return jsonify(status=(db().execute("SELECT status FROM orders WHERE code=?", (code,)).fetchone() or abort(404))["status"])

@app.post("/webhook/hodle")
def webhook():
    ev = hodle.verify_webhook(request.headers, request.get_data())
    if not ev: audit(db(), "webhook", "rejeitado", request.remote_addr); abort(401)
    d = db(); o = d.execute("SELECT * FROM orders WHERE provider_id=?", (ev["provider_id"],)).fetchone()
    if not o or d.execute("INSERT OR IGNORE INTO events VALUES(?,?)", (ev["provider_id"], ev["status"])).rowcount == 0: return "", 200  # idempotencia
    d.commit()
    if ev["status"] == "failed": _set(o["id"], "FALHOU")
    elif ev["status"] == "paid" and o["status"] == "AGUARDANDO_PIX":
        _set(o["id"], "PIX_CONFIRMADO")
        try:
            r = hodle.convert_and_send(o["code"], o["brl"], o["asset"], o["network"], o["address"])  # sempre para a carteira do .env
            d.execute("UPDATE orders SET txid=? WHERE id=?", (str(r["txid"]), o["id"])); _set(o["id"], "ENVIADO")
        except Exception as e: d.execute("UPDATE orders SET note=? WHERE id=?", (str(e)[:200], o["id"])); d.commit(); audit(d, "sistema", "conversao_erro", str(e)[:200])
    return "", 200

@app.template_filter("wa")
def wa_link(o, base):
    name = o["customer"] if "customer" in o.keys() else o["name"]
    msg = f"Olá, {name}! Sua cobrança de R$ {o['brl']:.2f} está disponível: {base}/p/{o['code']}"
    return f"https://wa.me/{o['whatsapp']}?text={urllib.parse.quote(msg)}"
if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
