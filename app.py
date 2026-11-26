import os, re, sqlite3, secrets, time, threading, urllib.parse, base64, hashlib, json
from datetime import datetime, timezone, timedelta, date
from functools import wraps
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, session, redirect, render_template, abort, jsonify, g
from werkzeug.security import check_password_hash, generate_password_hash
from cryptography.fernet import Fernet
import psycopg
from psycopg.rows import dict_row
import hodle, whatsapp

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SESSION_SECRET") or os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("Configure APP_SESSION_SECRET nas variáveis da Vercel.")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")) or os.environ.get("FORCE_HTTPS") == "1")
DB = os.environ.get("DB_PATH", "cripto_pix_v2.db")
DATABASE_URL = os.environ.get("DATABASE_URL")
LIVE = False
BASE = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
MAX = float(os.environ.get("MAX_BRL", 2000)); MIN_SUB = float(os.environ.get("MIN_SUB_BRL", 100))
ADDR = {"Base": r"^0x[a-fA-F0-9]{40}$", "Ethereum": r"^0x[a-fA-F0-9]{40}$", "Solana": r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", "Tron": r"^T[1-9A-HJ-NP-Za-km-z]{33}$"}
ASSET = "USDC"; NET = "Base"; WALLET = ""; WALLET_OK = False
BR = timezone(timedelta(hours=-3))
def today(): return datetime.now(BR).date()
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, customer TEXT, whatsapp TEXT, service TEXT, brl REAL, asset TEXT, network TEXT, address TEXT, pix_code TEXT, status TEXT, provider_id TEXT UNIQUE, txid TEXT, note TEXT, sub_id INTEGER, notified INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS subs(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, whatsapp TEXT, brl REAL, day INTEGER, next_date TEXT, active INTEGER DEFAULT 1, created TEXT);
CREATE TABLE IF NOT EXISTS events(provider_id TEXT, status TEXT, UNIQUE(provider_id,status));
CREATE TABLE IF NOT EXISTS audit(ts TEXT, actor TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS app_config(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL, encrypted_settings TEXT NOT NULL, created_at TEXT NOT NULL);
"""
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(id BIGSERIAL PRIMARY KEY, code TEXT UNIQUE, customer TEXT, whatsapp TEXT, service TEXT, brl DOUBLE PRECISION, asset TEXT, network TEXT, address TEXT, pix_code TEXT, status TEXT, provider_id TEXT UNIQUE, txid TEXT, note TEXT, sub_id BIGINT, notified INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS subs(id BIGSERIAL PRIMARY KEY, name TEXT, whatsapp TEXT, brl DOUBLE PRECISION, day INTEGER, next_date TEXT, active INTEGER DEFAULT 1, created TEXT);
CREATE TABLE IF NOT EXISTS events(provider_id TEXT, status TEXT, UNIQUE(provider_id,status));
CREATE TABLE IF NOT EXISTS audit(ts TEXT, actor TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS app_config(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL, encrypted_settings TEXT NOT NULL, created_at TEXT NOT NULL);
"""
class PgCompat:
    def __init__(self, raw): self.raw = raw
    def execute(self, sql, params=()): return self.raw.execute(sql.replace("?", "%s"), params)
    def commit(self): self.raw.commit()
    def close(self): self.raw.close()
def conn():
    if DATABASE_URL:
        return PgCompat(psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10))
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; return c
def init_db():
    c = conn()
    try:
        schema = POSTGRES_SCHEMA if DATABASE_URL else SQLITE_SCHEMA
        for statement in schema.split(";"):
            if statement.strip(): c.execute(statement)
        c.commit()
    finally: c.close()
def db():
    if "db" not in g: g.db = conn()
    return g.db
@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()
def audit(c, actor, action, detail=""): c.execute("INSERT INTO audit VALUES(?,?,?,?)", (now(), actor, action, detail)); c.commit()
def _fernet():
    raw = os.environ.get("APP_ENCRYPTION_KEY")
    if not raw: raise RuntimeError("Configure APP_ENCRYPTION_KEY nas variáveis da Vercel.")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)
def get_saved_config():
    c = conn()
    try: row = c.execute("SELECT username,password_hash,encrypted_settings FROM app_config WHERE id=1").fetchone()
    finally: c.close()
    if not row: return None
    payload = _fernet().decrypt(row["encrypted_settings"].encode()).decode()
    return {"username": row["username"], "password_hash": row["password_hash"], **json.loads(payload)}
def apply_settings(cfg):
    global BASE, LIVE, MAX, MIN_SUB, ASSET, NET, WALLET, WALLET_OK
    if not cfg: return
    BASE = cfg.get("base_url") or BASE
    LIVE = False
    MAX = float(os.environ.get("MAX_BRL", 2000)); MIN_SUB = float(os.environ.get("MIN_SUB_BRL", 100))
    ASSET = cfg.get("wallet_asset", "USDC").upper()
    NET = cfg.get("wallet_network", "Base")
    WALLET = cfg.get("wallet_address", "").strip()
    WALLET_OK = ASSET in ("USDC", "USDT") and NET in ADDR and bool(re.match(ADDR[NET], WALLET))
    hodle.configure(cfg.get("hodle_api_key", ""), cfg.get("hodle_workspace_id", ""))
    whatsapp.configure(cfg.get("wa_token", ""), cfg.get("wa_phone_id", ""), cfg.get("wa_template", "cobranca_mensal"), cfg.get("wa_lang", "pt_BR"))
def has_setup():
    c = conn()
    try: return c.execute("SELECT 1 FROM app_config WHERE id=1").fetchone() is not None
    finally: c.close()
init_db()
apply_settings(get_saved_config())
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
# Em produção serverless, cobranças recorrentes devem usar Cron/Workflow.
if not os.environ.get("VERCEL"):
    threading.Thread(target=loop, daemon=True).start()

@app.before_request
def refresh_saved_settings():
    apply_settings(get_saved_config())

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
    configured = has_setup()
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("_csrf", ""), session.get("csrf", "x")):
            abort(400, "Sessão inválida. Atualize a página e tente novamente.")
        if not configured:
            username = request.form.get("user", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("password_confirm", "")
            wallet_asset = request.form.get("wallet_asset", "USDC").upper()
            wallet_network = request.form.get("wallet_network", "Base")
            wallet_address = request.form.get("wallet_address", "").strip()
            api_key = request.form.get("hodle_api_key", "").strip()
            workspace_id = request.form.get("hodle_workspace_id", "").strip()
            if not username or len(password) < 10 or password != confirm:
                err = "Informe um usuário, uma senha com pelo menos 10 caracteres e confirme a senha."
            elif wallet_asset not in ("USDC", "USDT") or wallet_network not in ADDR or not re.match(ADDR[wallet_network], wallet_address):
                err = "Confira a moeda, a rede e o endereço público da carteira."
            elif not api_key or not workspace_id:
                err = "Informe a chave API e o ID do Workspace da Hodle."
            else:
                payload = {
                    "wallet_asset": wallet_asset, "wallet_network": wallet_network, "wallet_address": wallet_address,
                    "hodle_api_key": api_key, "hodle_workspace_id": workspace_id,
                    "wa_token": request.form.get("wa_token", "").strip(),
                    "wa_phone_id": request.form.get("wa_phone_id", "").strip(),
                    "wa_template": request.form.get("wa_template", "cobranca_mensal").strip() or "cobranca_mensal",
                    "wa_lang": request.form.get("wa_lang", "pt_BR").strip() or "pt_BR",
                    "base_url": request.url_root.rstrip("/")
                }
                encrypted = _fernet().encrypt(json.dumps(payload).encode()).decode()
                c = conn()
                try:
                    if c.execute("SELECT 1 FROM app_config WHERE id=1").fetchone():
                        abort(409, "A configuração inicial já foi concluída.")
                    c.execute("INSERT INTO app_config(id,username,password_hash,encrypted_settings,created_at) VALUES(1,?,?,?,?)",
                              (username, generate_password_hash(password), encrypted, now()))
                    c.commit()
                finally: c.close()
                apply_settings(get_saved_config())
                session.clear(); session["admin"] = True; session["csrf"] = secrets.token_hex(16)
                audit(db(), "admin", "configuracao_inicial", "Administrador único configurado")
                return redirect("/admin")
        else:
            ip = request.remote_addr; t = [x for x in tries.get(ip, []) if time.time() - x < 600]
            if len(t) >= 5: return "Muitas tentativas. Aguarde 10 minutos.", 429
            cfg = get_saved_config()
            if cfg and request.form.get("user", "").strip() == cfg["username"] and check_password_hash(cfg["password_hash"], request.form.get("password", "")):
                session.clear(); session["admin"] = True; audit(db(), "admin", "login", ip); return redirect("/admin")
            tries[ip] = t + [time.time()]; err = "Usuário ou senha incorretos."
    session.setdefault("csrf", secrets.token_hex(16))
    return render_template("login.html", err=err, setup=not configured)
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
    if not o or d.execute("INSERT INTO events VALUES(?,?) ON CONFLICT DO NOTHING", (ev["provider_id"], ev["status"])).rowcount == 0: return "", 200  # idempotencia
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
