"""Adaptador da Hodle. Os dados são carregados do armazenamento criptografado do app.
Os endpoints de cobrança/conversão precisam ser implementados com a documentação oficial.
"""
KEY = ""
WORKSPACE_ID = ""
WSECRET = ""
NOT_IMPL = "Integração Hodle ainda não implementada com a documentação oficial."

def configure(api_key="", workspace_id="", webhook_secret=""):
    global KEY, WORKSPACE_ID, WSECRET
    KEY = api_key or ""
    WORKSPACE_ID = workspace_id or ""
    WSECRET = webhook_secret or ""

def create_pix(ref, amount_brl, description):
    raise NotImplementedError(NOT_IMPL)

def convert_and_send(ref, amount_brl, asset, network, address):
    raise NotImplementedError(NOT_IMPL)

def verify_webhook(headers, body):
    return None
