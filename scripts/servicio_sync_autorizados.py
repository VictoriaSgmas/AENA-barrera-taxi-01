# Servicio "sync-autorizados": descarga GET /autorizados cada 5 minutos y
# de inmediato si servicio_webhook_sgmas.py deja un comando "sync_ahora".
# Escribe la cache en disco que usa reglas.py para decidir sin depender de
# que el VPS este arriba en ese instante. Si este servicio se cae,
# decision.py sigue funcionando con la ultima cache que haya en disco.

import json
import os
import time

import almacen
import vps_cliente

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CACHE = os.path.join(BASE_DIR, "config", "autorizados_cache.json")

INTERVALO_SEGUNDOS = 900
INTERVALO_COMPROBAR_COMANDOS = 2


def sincronizar():
    try:
        datos = vps_cliente.descargar_autorizados()
        ruta_tmp = RUTA_CACHE + ".tmp"
        with open(ruta_tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(ruta_tmp, RUTA_CACHE)  # atomico: decision.py nunca ve el fichero a medio escribir
        print(f"[sync-autorizados] actualizado: {len(datos)} registros")
    except Exception as e:
        print(f"[sync-autorizados] fallo al descargar ({e}); se mantiene la cache existente")


def main():
    sincronizar()
    ultimo_sync = time.time()

    while True:
        for id_comando, accion, origen, id_peticion, usuario in almacen.comandos_pendientes(accion="sync_ahora"):
            print(f"[sync-autorizados] sync inmediata pedida por {origen}")
            sincronizar()
            almacen.marcar_comando_procesado(id_comando)
            ultimo_sync = time.time()

        if time.time() - ultimo_sync >= INTERVALO_SEGUNDOS:
            sincronizar()
            ultimo_sync = time.time()

        time.sleep(INTERVALO_COMPROBAR_COMANDOS)


if __name__ == "__main__":
    main()
