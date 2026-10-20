# Simula la app (o el panel de SGMAS) publicando una orden de apertura
# manual por MQTT, para probar servicio_mqtt_cliente.py sin tener la app
# real ni el VPS real.
#
# En produccion la app se conecta por WSS con las credenciales de
# app_sgmas (distintas de las de la Pi); aqui, para la prueba en local,
# usamos el mismo Mosquitto de pruebas con una conexion TCP normal -- lo
# que importa para la prueba es que el topic y el payload sean identicos
# a los reales, no el transporte exacto que use la app.
#
# Uso:
#   python scripts/simulador_app.py                 (accion=abrir)
#   python scripts/simulador_app.py mantener_abierta
#   python scripts/simulador_app.py cerrar

import json
import os
import sys
import time
import uuid

import paho.mqtt.client as mqtt

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CONFIG = os.path.join(BASE_DIR, "config", "mqtt_config.json")

with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
    CFG = json.load(f)

TOPIC_CMD = f"barreras/{CFG['id_barrera']}/cmd"
TOPIC_ACK = f"barreras/{CFG['id_barrera']}/ack"
TOPIC_ESTADO = f"barreras/{CFG['id_barrera']}/estado"

accion = sys.argv[1] if len(sys.argv) > 1 else "abrir"
id_peticion = str(uuid.uuid4())


def on_connect(client, userdata, connect_flags, reason_code, properties=None):
    print(f"[prueba-app] conectada ({reason_code})")
    client.subscribe(TOPIC_ACK)
    client.subscribe(TOPIC_ESTADO)

    payload = {"accion": accion, "usuario": "prueba_local", "id_peticion": id_peticion}
    print(f"[prueba-app] publicando en {TOPIC_CMD}: {payload}")
    client.publish(TOPIC_CMD, json.dumps(payload), qos=1)


def on_message(client, userdata, msg):
    print(f"[prueba-app] recibido en {msg.topic}: {msg.payload.decode('utf-8')}")


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="prueba_app_local")
    if CFG.get("tls", True):
        client.tls_set(ca_certs=os.path.join(BASE_DIR, CFG["cafile"]))
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(CFG["host"], CFG["port"], keepalive=30)
    client.loop_start()

    print("[prueba-app] esperando ack/estado 5 segundos...")
    time.sleep(5)

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
