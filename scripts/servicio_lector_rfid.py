# Servicio "lector-rfid": solo lee el hardware y entrega la lectura en
# crudo al almacen compartido. No busca en ninguna lista ni decide nada
# -- eso es trabajo de servicio_decision.py, en su propio proceso. Si
# este servicio se cae, receptor-lpr y decision siguen funcionando.
#
# NOTA: usa rfid_reader.py, que en modo "serial" ya habla con el lector
# UHF R16-9DB real por su puerto COM virtual (ver rfid_config.json y el
# comentario al principio de rfid_reader.py). El lector manda un decimal
# truncado (formato de fabrica, no el EPC completo) -- reglas.describir_rfid()
# lo traduce SOLO para este print (a "E280..." si el tag esta en la lista,
# o a su huella de 4 bytes si no); el almacen sigue guardando el valor
# crudo tal cual llega, y la decision real la sigue tomando
# servicio_decision.py con buscar_por_rfid(). Ver README.md ("Formato del
# RFID (resuelto)").
#
# FILTRO ANTI-REPETICION: en modo "Active reading" el lector real manda
# la misma lectura muchas veces por segundo mientras el tag siga en
# rango (medido: ~13/s con el "card filter number" de fabrica) -- sin
# filtrar, cada una de esas lecturas dispara un pulso real al rele en
# servicio_decision.py. Se descartan aqui las repeticiones del mismo
# codigo dentro de INTERVALO_MIN_REPETICION_SEG. Esto es un cinturon de
# seguridad ademas de (no en vez de) ajustar el "card filter number" /
# "search card interval" en el propio lector (pestaña "Prefix suffix
# Data Input" del software DEMO), que es donde deberia resolverse de
# raiz.
#
# FILTRO DE TAGS AJENOS: con el lector ya en modo EPC completo, la antena
# de largo alcance (4-6m) tambien capta tags reales de otras cosas que
# pasen cerca (otros vehiculos, objetos con su propio RFID) -- se
# descartan aqui mismo, antes de tocar el almacen compartido, via
# reglas.es_tag_propio() (mira si el EPC empieza por
# reglas.PREFIJO_EPC_PROPIO). No se tratan como "lectura invalida" ni se
# guardan en ningun sitio: son lecturas validas, solo que no son nuestras.

import time

import almacen
import reglas
from rfid_reader import leer_rfid_bloqueante

INTERVALO_MIN_REPETICION_SEG = 2.0


def main():
    print("[lector-rfid] esperando lecturas...")
    ultimo_codigo = None
    ultimo_timestamp = 0.0
    while True:
        codigo = leer_rfid_bloqueante()
        if not codigo:
            continue
        codigo = codigo.upper().strip()

        if not reglas.es_tag_propio(codigo):
            print(f"[lector-rfid] ignorado (tag ajeno, no empieza por {reglas.PREFIJO_EPC_PROPIO}): {codigo}")
            continue

        ahora = time.monotonic()
        if codigo == ultimo_codigo and (ahora - ultimo_timestamp) < INTERVALO_MIN_REPETICION_SEG:
            continue
        ultimo_codigo, ultimo_timestamp = codigo, ahora

        almacen.insertar_lectura("RFID", codigo)
        print(f"[lector-rfid] leido: {codigo} -> {reglas.describir_rfid(codigo)}")


if __name__ == "__main__":
    main()
