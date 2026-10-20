# Servicio "eventos": la unica parte del sistema que habla POST /eventos
# con el VPS. Vacia la cola que deja decision.py. Si el VPS esta caido, el
# evento se queda en la cola (en logs/barrera.db) y se reintenta en el
# siguiente ciclo, sin perderse -- y sin que decision, lector-rfid ni
# receptor-lpr se enteren de que hay un problema.

import time

import almacen
import vps_cliente

INTERVALO_SEGUNDOS = 5


def main():
    print("[eventos] esperando eventos en cola...")
    while True:
        for id_fila, payload in almacen.eventos_pendientes():
            if vps_cliente.enviar_evento_http(payload):
                almacen.eliminar_evento(id_fila)
                print(f"[eventos] enviado: {payload['id_evento']}")
            else:
                print(f"[eventos] no se pudo enviar {payload['id_evento']}, se reintenta luego")
        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
