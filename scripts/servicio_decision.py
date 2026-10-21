# Servicio "decision": el unico que compara matricula/RFID contra la
# lista y el unico que dispara el rele para las lecturas automaticas
# (RFID/LPR). Lee del almacen compartido lo que hayan dejado lector-rfid
# y receptor-lpr, decide, dispara el rele si corresponde, y deja el
# evento listo para que servicio_eventos.py lo mande al VPS -- este
# servicio nunca llama el mismo al VPS, asi que si el VPS esta caido,
# decision sigue funcionando igual (el rele se dispara igual).
#
# Deduplicado de lecturas repetidas: un coche pasando por delante de la
# camara LPR genera la misma matricula varias veces (visto en produccion: 4
# eventos por coche, a ~1 s entre si), y uno parado delante del lector UHF
# genera el mismo EPC una y otra vez. Hay dos ventanas, ambas en memoria del
# propio proceso (si el servicio se reinicia, como mucho se deja pasar -- y
# se registra -- una lectura de mas, no pasa nada):
#
# - VENTANA_DEDUP_SEGUNDOS: si la misma (origen, valor) ya se proceso hace
#   menos de eso, NO se escribe en registro_accesos.csv ni se encola el
#   evento para el VPS -- para no inundar el log ni el servidor.
# - VENTANA_PULSO_SEGUNDOS: tras pulsar el rele para una (origen, valor),
#   no se vuelve a pulsar para esa misma lectura hasta que pase esta
#   ventana. Por defecto es 0 = desactivado: el rele se pulsa en CADA
#   lectura, tambien las repetidas. Es lo que funciona cuando la barrera
#   esta SIN memoria de senales de apertura (LP04=0): los pulsos no se
#   acumulan y mientras la camara siga viendo el coche la barrera no llega
#   a cerrarse del todo. Con la memoria activada (LP04=1) cada pulso cuenta
#   como un coche mas y la barrera hace un ciclo abrir-cerrar por pulso
#   (la pantalla muestra INxx); lo correcto es desactivarla en la barrera,
#   y SOLO si la placa no deja (firmware anterior a sept-2019, ver manual,
#   apendice IV) se pone aqui un valor > 0 para mandar un solo pulso por
#   coche. Se mide desde el ULTIMO PULSO, no desde la ultima lectura, y un
#   pulso que FALLO no cuenta: la siguiente lectura lo reintenta.

import json
import time
import uuid
from datetime import datetime, timedelta

import almacen
import reglas
import rele_dt_r002
from logger import registrar

INTERVALO_SEGUNDOS = 0.5
VENTANA_DEDUP_SEGUNDOS = 60
VENTANA_PULSO_SEGUNDOS = 4

_ultimas_lecturas = {}  # (origen, valor) -> datetime de la ultima vez procesada
_ultimos_pulsos = {}  # (origen, valor) -> datetime del ultimo pulso al rele que SI llego
_seq_pantalla = 0


def _pulso_reciente(origen, valor, ahora):
    # Misma purga que en _es_lectura_repetida (y por la misma razon: sin ella
    # el diccionario crece sin limite). Al purgar lo que se sale de la
    # ventana, que la clave siga en el diccionario ya significa "reciente".
    limite = ahora - timedelta(seconds=VENTANA_PULSO_SEGUNDOS)
    for clave_vieja in [k for k, v in _ultimos_pulsos.items() if v < limite]:
        del _ultimos_pulsos[clave_vieja]
    return (origen, valor) in _ultimos_pulsos


def _es_lectura_repetida(origen, valor, ahora):
    # Purga primero lo que ya se salio de la ventana de dedup: pasados
    # VENTANA_DEDUP_SEGUNDOS ya no sirven para nada (ver mas abajo), y sin
    # esto el diccionario crece sin limite mientras el servicio este vivo
    # -- cada (origen, valor) que se ve una vez se queda ahi para siempre.
    # Con el lector UHF mandando varias lecturas por segundo, eso se nota
    # en minutos, no en dias (memoria RSS subiendo sin parar hasta que el
    # proceso muere -- visto en produccion: 900+ MB en 8 minutos).
    limite = ahora - timedelta(seconds=VENTANA_DEDUP_SEGUNDOS)
    for clave_vieja in [k for k, v in _ultimas_lecturas.items() if v < limite]:
        del _ultimas_lecturas[clave_vieja]

    clave = (origen, valor)
    anterior = _ultimas_lecturas.get(clave)
    _ultimas_lecturas[clave] = ahora
    return anterior is not None and (ahora - anterior).total_seconds() < VENTANA_DEDUP_SEGUNDOS


def _avisar_pantalla(resultado, motivo):
    # Lo que muestra el monitor junto a la barrera (servicio_pantalla.py).
    # Si falla, la barrera tiene que seguir funcionando igual.
    global _seq_pantalla
    _seq_pantalla += 1
    try:
        almacen.set_estado("pantalla", json.dumps({
            "seq": _seq_pantalla,
            "ts": time.time(),
            "resultado": resultado,
            "motivo": motivo,
        }))
    except Exception as e:
        print(f"[decision] no se pudo avisar a la pantalla: {e}")


def procesar(id_lectura, timestamp, origen, valor):
    if origen == "RFID":
        registro = reglas.buscar_por_rfid(valor)
        if registro and registro.get("rfid"):
            # Match encontrado: EPC completo (24 hex) confirmado por la
            # lista de autorizados -- logs/eventos/terminal muestran el
            # mismo identificador que hay en config/autorizados_cache.json.
            valor = registro["rfid"].strip().upper()
        elif reglas.es_epc_completo(valor):
            # Sin match en autorizados, pero el lector ya mando el EPC
            # completo (24 hex) en vez del decimal truncado de fabrica --
            # el identificador SI se conoce del todo, solo que ese tag no
            # esta autorizado. Se muestra tal cual en vez de como huella o
            # lectura invalida (evaluar(), mas abajo, sigue decidiendo
            # DENEGADO/NO_AUTORIZADO igual, esto es solo para el texto).
            valor = str(valor).strip().upper()
        else:
            # Sin match y sin EPC completo: no hay forma de saber el EPC
            # completo (el decimal es irreversible, ver reglas.py) -- se
            # muestran en hex los 4 bytes que el lector SI transmite, en
            # vez del decimal crudo, para poder comprobar a simple vista
            # que el formato se esta leyendo bien aunque el tag no este
            # dado de alta.
            huella = reglas.fingerprint_rfid(valor)
            # huella es None si `valor` no era un decimal valido (trama
            # corrupta/a medias) -- se deja rastro en vez de guardar la
            # cadena "None" o reventar el bucle con la excepcion de int().
            valor = huella if huella is not None else f"LECTURA_RFID_INVALIDA:{valor}"
    else:
        valor = reglas.normalizar_matricula(valor)
        registro = reglas.buscar_por_matricula(valor)

    repetida = _es_lectura_repetida(origen, valor, datetime.now())
    etiqueta = f"{origen} {valor}{reglas.describir_registro(registro)}"

    resultado, motivo = reglas.evaluar(registro)

    respuesta_rele = ""
         # BORRAR LA LINEA DE ABAJO PARA DEJAR PASAR SOLO A LOS REGISTRADOS
    #resultado = "PERMITIDO"

    # Antes del rele y antes del return de las repetidas: la pantalla se
    # refresca con cada lectura, tambien las repetidas, para que siga
    # encendida mientras el coche este delante de la camara.
    _avisar_pantalla(resultado, motivo)

    if resultado == "PERMITIDO":
        if _pulso_reciente(origen, valor, datetime.now()):
            respuesta_rele = f"OMITIDO (ya se pulso hace menos de {VENTANA_PULSO_SEGUNDOS}s)"
            print(f"[decision] {etiqueta} -> rele no pulsado, ya se pulso hace menos de {VENTANA_PULSO_SEGUNDOS}s")
        else:
            try:
                respuesta_rele = rele_dt_r002.abrir_pulso()
                _ultimos_pulsos[(origen, valor)] = datetime.now()
                print(f"[decision] respuesta del rele: {respuesta_rele!r}")
            except Exception as e:
                respuesta_rele = f"ERROR: {e}"
                print(f"[decision] ERROR al pulsar el rele: {e}")

    if repetida:
        almacen.marcar_lectura_procesada(id_lectura)
        print(f"[decision] {etiqueta} -> {resultado} {motivo} | repetida (<{VENTANA_DEDUP_SEGUNDOS}s), sin log ni evento")
        return

    accion_log = registrar(origen, valor, resultado == "PERMITIDO", respuesta_rele)

    evento = {
        "id_evento": str(uuid.uuid4()),
        "fecha_hora": datetime.now().astimezone().isoformat(timespec="seconds"),
        "origen": origen,
        "identificador": valor,
        "resultado": resultado,
        "motivo": motivo,
        "usuario": "",
        "tipo_fuente": registro.get("tipo_fuente", "") if registro else "",
        "ref_id_origen": registro.get("ref_id_origen", "") if registro else "",
    }
    almacen.encolar_evento(evento)
    almacen.set_estado("ultimo_evento", evento["fecha_hora"])
    almacen.marcar_lectura_procesada(id_lectura)

    print(f"[decision] {etiqueta} -> {resultado} {motivo} | {accion_log}")


def main():
    print("[decision] esperando lecturas pendientes...")
    while True:
        for id_lectura, timestamp, origen, valor in almacen.lecturas_pendientes():
            procesar(id_lectura, timestamp, origen, valor)
        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
