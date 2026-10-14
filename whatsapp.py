"""Envio automatico pela API oficial do WhatsApp (Meta Cloud API) com mensagem-modelo aprovada."""
import os, requests
TOKEN = os.environ.get("WA_TOKEN", ""); PHONE_ID = os.environ.get("WA_PHONE_ID", "")
TEMPLATE = os.environ.get("WA_TEMPLATE", "cobranca_mensal"); LANG = os.environ.get("WA_LANG", "pt_BR")
def configured(): return bool(TOKEN and PHONE_ID)
def send_charge(to, nome, valor, link):
    """Modelo com 3 variaveis no corpo: {{1}} nome, {{2}} valor, {{3}} link."""
    ps = [{"type": "text", "text": str(x)} for x in (nome, valor, link)]
    r = requests.post(f"https://graph.facebook.com/v20.0/{PHONE_ID}/messages", timeout=20,
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"messaging_product": "whatsapp", "to": to, "type": "template",
              "template": {"name": TEMPLATE, "language": {"code": LANG}, "components": [{"type": "body", "parameters": ps}]}})
    r.raise_for_status()
