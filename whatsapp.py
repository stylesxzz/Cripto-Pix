"""Envio opcional pela API oficial do WhatsApp Cloud (Meta)."""
import requests
TOKEN = ""
PHONE_ID = ""
TEMPLATE = "cobranca_mensal"
LANG = "pt_BR"

def configure(token="", phone_id="", template="cobranca_mensal", lang="pt_BR"):
    global TOKEN, PHONE_ID, TEMPLATE, LANG
    TOKEN = token or ""
    PHONE_ID = phone_id or ""
    TEMPLATE = template or "cobranca_mensal"
    LANG = lang or "pt_BR"

def configured():
    return bool(TOKEN and PHONE_ID)

def send_charge(to, nome, valor, link):
    ps = [{"type": "text", "text": str(x)} for x in (nome, valor, link)]
    r = requests.post(f"https://graph.facebook.com/v20.0/{PHONE_ID}/messages", timeout=20,
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"messaging_product": "whatsapp", "to": to, "type": "template",
              "template": {"name": TEMPLATE, "language": {"code": LANG}, "components": [{"type": "body", "parameters": ps}]}})
    r.raise_for_status()
