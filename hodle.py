"""Adaptador da Hodle. NAO tem endpoints inventados: preencha com a documentacao oficial da API deles.
Enquanto estiver vazio, o app roda em modo manual (HODLE_LIVE=0) e o webhook rejeita tudo."""
import os
KEY = os.environ.get("HODLE_API_KEY", ""); WSECRET = os.environ.get("HODLE_WEBHOOK_SECRET", "")
NOT_IMPL = "Implemente com a documentacao da API da Hodle (hodle.py)"

def create_pix(ref, amount_brl, description):
    """Deve criar a cobranca Pix na Hodle e retornar {'id': ..., 'pix_code': 'copia e cola'}."""
    raise NotImplementedError(NOT_IMPL)

def convert_and_send(ref, amount_brl, asset, network, address):
    """Deve comprar `asset` com o BRL recebido e sacar para `address`. Retorna {'txid': ..., 'amount': ...}."""
    raise NotImplementedError(NOT_IMPL)

def verify_webhook(headers, body):
    """Deve validar a assinatura do webhook conforme a doc da Hodle e retornar {'provider_id':..,'status':'paid'|'failed'} ou None.
    Padrao seguro: retorna None (rejeita) ate ser implementado."""
    return None
