# Cliente HTTP del rele de red Dingtian DT-R002, que da la orden de
# apertura a la central de la barrera (contacto R1 NO/COM).
#
# abrir_pulso() esta CONFIRMADO contra el dispositivo real:
#   GET /relay_cgi.cgi?type=1&relay=0&on=1&time=10&pwd=5597&
#   Cookie: session=9497217
#   Respuesta: &0&1&0&1&10&
#
# mantener_abierto() y cerrar() son la mejor suposicion razonable a partir
# de ese mismo CGI (quitar "time" para que no vuelva solo, y on=0 para
# apagar), pero NO estan confirmadas contra el hardware real -- no hay
# documentacion del CGI del DT-R002 mas alla del ejemplo de arriba.
# Antes de fiarse de ellas en produccion, probarlas con un LED en los
# bornes R1 (sin la barrera conectada) y confirmar que hacen lo que
# deberian. Ojo: como R1 esta cableado a UP, este par solo mantiene o
# suelta el contacto de UP -- no aprieta DOWN. Para un cierre activo por
# DOWN esta cerrar_pulso() (R2/relay_index_down), que todavia no esta
# cableado a la barrera.
#
# IMPORTANTE: el CGI del DT-R002 es sensible al ORDEN de los parametros y
# necesita el "&" final -- "pwd" tiene que ir en ultimo lugar. Si "time"
# va despues de "pwd" (como hace por defecto un dict pasado a
# requests.get(params=...), que aqui añadiamos "time" despues de "pwd"),
# el dispositivo responde 200 pero el rele NO se activa de verdad
# (se detecto porque la respuesta cruda cambiaba de "&0&..." a "&-5&...").
# Por eso aqui se construye la URL a mano, en el mismo orden exacto que el
# curl ya confirmado, en vez de dejar que requests la arme.

import json
import os

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CONFIG = os.path.join(BASE_DIR, "config", "rele_config.json")


def _config():
    with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def _cgi(on, time_100ms=None, relay_index=None):
    cfg = _config()
    if relay_index is None:
        relay_index = cfg["relay_index"]
    query = f"type=1&relay={relay_index}&on={on}&"
    if time_100ms is not None:
        query += f"time={time_100ms}&"
    query += f"pwd={cfg['password']}&"

    resp = requests.get(
        f"http://{cfg['host']}/relay_cgi.cgi?{query}",
        headers={"Cookie": f"session={cfg['session_id']}"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.text


def abrir_pulso():
    cfg = _config()
    return _cgi(on=1, time_100ms=cfg["jogging_time_100ms"])


def mantener_abierto():
    return _cgi(on=1)


def cerrar():
    return _cgi(on=0)


def cerrar_pulso():
    # Pulso sobre R2 (relay_index_down), cableado al contacto DOWN/COM de
    # la barrera -- espejo exacto de abrir_pulso() pero en el otro canal.
    # SIN CABLEAR / SIN CONFIRMAR: hace falta llevar R2-NO a DOWN y
    # puentear R2-COM con R1-COM en la propia regleta del rele antes de
    # poder probar esto contra el hardware (primero con un LED en los
    # bornes de R2, igual que se hizo con abrir_pulso() en su momento).
    cfg = _config()
    return _cgi(
        on=1,
        time_100ms=cfg["jogging_time_100ms"],
        relay_index=cfg["relay_index_down"],
    )
