# Servicio "mqtt-cliente": conecta al broker MQTT del VPS (TLS) para la
# apertura/cierre remotos desde la app, y publica el estado de la Pi.
#
# Para probar en local sin las credenciales/certificado reales, pon
# "tls": false en config/mqtt_config.json y apunta "host" a un Mosquitto
# local (ver README, "Probar MQTT en local"). Con "tls": true (el valor
# real de produccion) hace falta el "cafile" y las credenciales de
# pi_aeropuerto que todavia debe mandar SGMAS -- ver PLAN_INSTALACION.md,
# "Bloqueantes externos".
#
# PENDIENTE de confirmar con SGMAS: si las ordenes recibidas por MQTT
# deben generar tambien un evento en /eventos. Por ahora, siguiendo la
# nota del propio documento ("la Pi no envia evento por la API para
# ordenes recibidas por MQTT, pendiente de confirmacion final"), este
# servicio NO encola nada en almacen.encolar_evento para las ordenes
# manuales -- solo publica el ack. Si se confirma que si hace falta,
# añadir aqui una llamada a almacen.encolar_evento(...) igual que hace
# servicio_decision.py.
#
# Solo "abrir" esta implementada: es la unica confirmada de verdad contra
# el rele real (pulso audible). "mantener_abierta" y "cerrar" (rele on=1
# u on=0 SIN el parametro "time") devuelven "&-5&..." del CGI -- todo
# apunta a que el DT-R002 exige "time" siempre y nuestra suposicion de
# "omite time para que se quede fijo" no es correcta, asi que de momento
# no se llama al rele para esas dos y se responde con un ack de error en
# vez de arriesgarse a mandar algo sin confirmar. Es tambien lo unico que
# hace falta ahora mismo: solo se necesita el pulso de apertura.
#
# Usa la API de callbacks v2 de paho-mqtt (paho-mqtt>=2.0, que es lo que
# instala requirements.txt). Con paho-mqtt 1.x habria que quitar
# callback_api_version y cambiar la firma de on_connect a
# (client, userdata, flags, rc).

import json
import os
import time

import paho.mqtt.client as mqtt

import almacen
import rele_dt_r002

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CONFIG = os.path.join(BASE_DIR, "config", "mqtt_config.json")

INTERVALO_ESTADO_SEGUNDOS = 45

with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
    CFG = json.load(f)

TOPIC_CMD = f"barreras/{CFG['id_barrera']}/cmd"
TOPIC_ACK = f"barreras/{CFG['id_barrera']}/ack"
TOPIC_ESTADO = f"barreras/{CFG['id_barrera']}/estado"

def _ejecutar_accion(accion):
    if accion == "abrir":
        r = rele_dt_r002.abrir_pulso()
        print(f"[mqtt-cliente] respuesta del rele: {r!r}")
        return True, ""
    if accion in ("mantener_abierta", "cerrar"):
        print(f"[mqtt-cliente] '{accion}' pedida pero no implementada todavia (ver comentario arriba)")
        return False, f"{accion} no implementada todavia"
    return False, f"accion desconocida: {accion}"


def on_connect(client, userdata, connect_flags, reason_code, properties=None):
    print(f"[mqtt-cliente] conectado ({reason_code}), suscrito a {TOPIC_CMD}")
    client.subscribe(TOPIC_CMD)


def on_message(client, userdata, msg):
    try:
        orden = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"[mqtt-cliente] mensaje no valido en {msg.topic}: {msg.payload!r}")
        return

    accion = orden.get("accion")
    id_peticion = orden.get("id_peticion")
    print(f"[mqtt-cliente] orden recibida: {orden}")

    try:
        ok, detalle = _ejecutar_accion(accion)
        resultado = "ok" if ok else "error"
    except Exception as e:
        resultado, detalle = "error", str(e)

    client.publish(TOPIC_ACK, json.dumps({
        "id_peticion": id_peticion,
        "resultado": resultado,
        "detalle": detalle,
    }), qos=1)

    if resultado == "ok":
        almacen.set_estado("ultimo_evento", f"MANUAL_APP:{accion}")


def publicar_estado(client):
    estado = {
        "en_linea": True,
        # "mantener_abierta" no esta implementada (ver arriba), asi que el
        # rele nunca queda enclavado -- siempre False es correcto por ahora.
        "rele_activo": False,
        "ultimo_evento": almacen.get_estado("ultimo_evento", ""),
    }
    client.publish(TOPIC_ESTADO, json.dumps(estado), qos=1, retain=True)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pi_{CFG['id_barrera']}")
    client.username_pw_set(CFG["usuario"], CFG["password"])
    if CFG.get("tls", True):
        client.tls_set(ca_certs=os.path.join(BASE_DIR, CFG["cafile"]))
    client.will_set(TOPIC_ESTADO, json.dumps({"en_linea": False}), qos=1, retain=True)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(CFG["host"], CFG["port"], keepalive=60)
    client.loop_start()

    try:
        while True:
            publicar_estado(client)
            time.sleep(INTERVALO_ESTADO_SEGUNDOS)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
