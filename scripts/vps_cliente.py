# Llamadas HTTP puras a la API real de SGMAS. No cachea ni decide nada --
# eso lo hacen servicio_sync_autorizados.py (llama a descargar_autorizados)
# y servicio_eventos.py (llama a enviar_evento_http), cada uno en su propio
# proceso.

import json
import os

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CONFIG = os.path.join(BASE_DIR, "config", "api_config.json")


def _config():
    with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def descargar_autorizados():
    """Devuelve la lista de /autorizados. Lanza excepcion si falla (la
    llamante decide que hacer, normalmente seguir con la cache anterior)."""
    cfg = _config()
    url = f"{cfg['base_url']}/{cfg['id_barrera']}/autorizados"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {cfg['token']}", "Accept": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    respuesta = resp.json()
    # El VPS real envuelve la lista: {"resultado","barrera","generado_en","total","autorizados":[...]}.
    return respuesta.get("autorizados", []) if isinstance(respuesta, dict) else respuesta


def enviar_evento_http(payload):
    """True si el VPS confirmo el evento (200/201), False en cualquier otro caso."""
    cfg = _config()
    url = f"{cfg['base_url']}/{cfg['id_barrera']}/eventos"
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {cfg['token']}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True
        print(f"[vps_cliente] /eventos respondio {resp.status_code}: {resp.text[:300]}")
        return False
    except requests.RequestException as e:
        print(f"[vps_cliente] error de red al enviar evento: {e}")
        return False
