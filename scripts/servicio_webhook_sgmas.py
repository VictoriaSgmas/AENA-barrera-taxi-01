# Servicio "webhook-sgmas" (opcional): recibe el aviso de SG-Taxi cuando
# cambia la lista de autorizados, verifica la firma HMAC-SHA256 y le pide
# a servicio_sync_autorizados.py (via almacen.insertar_comando) que
# sincronice ya, sin esperar a los 5 minutos.
#
# PENDIENTE: el WebhookSecretoHmac real (config/webhook_config.json trae
# un valor de relleno) y la URL publica en el VPS que reenvia esto hasta
# aqui por el tunel VPN (el VPS hace de proxy inverso hacia la Pi).

import hashlib
import hmac
import json
import os

from flask import Flask, request

import almacen

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CONFIG = os.path.join(BASE_DIR, "config", "webhook_config.json")

with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
    CFG = json.load(f)

app = Flask(__name__)


def _firma_valida(cuerpo_bytes, firma_recibida):
    esperada = hmac.new(CFG["secreto_hmac"].encode("utf-8"), cuerpo_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma_recibida or "")


@app.route("/", methods=["GET"])
def home():
    return "Servicio webhook-sgmas en marcha.", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    firma = request.headers.get("X-SGMAS-Webhook-Signature")
    if not _firma_valida(request.get_data(), firma):
        print("[webhook-sgmas] firma invalida, evento ignorado")
        return {"ok": False, "motivo": "firma invalida"}, 401

    datos = request.get_json(silent=True) or {}
    print(f"[webhook-sgmas] cambio recibido: {datos}")
    almacen.insertar_comando("sync_ahora", origen="WEBHOOK")
    return {"ok": True}, 200


if __name__ == "__main__":
    print(f"[webhook-sgmas] escuchando en el puerto {CFG['puerto']}, ruta /webhook ...")
    app.run(host="0.0.0.0", port=CFG["puerto"])
