# Servicio "receptor-lpr": solo recibe el HTTP POST de la camara y
# entrega la matricula en crudo al almacen compartido. No busca en
# ninguna lista ni decide nada. Si este servicio se cae, la camara vera
# fallar sus notificaciones, pero lector-rfid y decision siguen
# funcionando sin enterarse.

from flask import Flask, request

import almacen

PUERTO_ESCUCHA = 8080

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    return "Servicio receptor-lpr en marcha. Esperando eventos de la camara por POST en /lpr.", 200


@app.route("/lpr", methods=["POST"])
def lpr_event():
    datos = request.get_json(silent=True) or {}
    matricula = datos.get("license_plate")

    if not matricula:
        print("[receptor-lpr] evento sin campo 'license_plate':", datos)
        return {"ok": False, "motivo": "sin license_plate en el payload"}, 200

    matricula = matricula.upper().replace(" ", "").replace("-", "")
    almacen.insertar_lectura("LPR", matricula)
    print(f"[receptor-lpr] leido: {matricula}")
    return {"ok": True, "matricula": matricula}, 200


if __name__ == "__main__":
    print(f"[receptor-lpr] escuchando en el puerto {PUERTO_ESCUCHA}, ruta /lpr ...")
    app.run(host="0.0.0.0", port=PUERTO_ESCUCHA)
