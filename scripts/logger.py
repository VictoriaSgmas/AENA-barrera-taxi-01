# Registro unico de todo lo que lee el sistema: cada matricula (camara LPR)
# y cada tarjeta (RFID) que se lee, este o no en la lista blanca, la
# accion que se tomo, y la respuesta cruda que devolvio el rele DT-R002
# al pulsarlo (columna respuesta_rele) -- eso es lo que confirma que el
# pulso realmente le llego al rele, no solo que lo intentamos.

import contextlib
import csv
import gzip
import os
import re
from datetime import datetime, timedelta

try:
    import fcntl
except ImportError:  # Windows: solo se usa para desarrollo, alli no hace falta
    fcntl = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "logs", "registro_accesos.csv")
LOCK_PATH = os.path.join(BASE_DIR, "logs", ".registro_accesos.lock")
ARCHIVO_DIR = os.path.join(BASE_DIR, "logs", "archivo")

CABECERA = ["timestamp", "origen", "valor_leido", "autorizado", "accion", "respuesta_rele"]


@contextlib.contextmanager
def _bloqueo():
    # registrar() (servicio decision) y archivar_antiguos() (mantenimiento
    # nocturno) toman este mismo candado, asi la poda nunca reescribe el
    # CSV justo mientras llega una fila nueva -- esa fila se perderia.
    if fcntl is None:
        yield
        return
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def registrar(origen, valor, autorizado, respuesta_rele=""):
    accion = "ABIERTO (pulso enviado al rele DT-R002)" if autorizado else "DENEGADO"

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with _bloqueo():
        es_nuevo = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if es_nuevo:
                writer.writerow(CABECERA)
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                origen,
                valor,
                autorizado,
                accion,
                respuesta_rele,
            ])

    return accion


def archivar_antiguos(dias_vivos=30, meses_archivo=3, dry_run=False):
    # Deja en registro_accesos.csv solo los ultimos `dias_vivos` dias. Lo
    # anterior se comprime a logs/archivo/registro_accesos_AAAA-MM.csv.gz
    # (un fichero por mes; ocupa una fraccion de lo que ocupaba en claro) y
    # los archivos de mas de `meses_archivo` meses se borran. Con
    # meses_archivo=0 no se archiva nada: lo antiguo se borra directamente.
    # Devuelve (filas_movidas, archivos_borrados).
    corte = (datetime.now() - timedelta(days=dias_vivos)).isoformat(timespec="seconds")

    with _bloqueo():
        if not os.path.exists(LOG_PATH):
            return 0, 0

        with open(LOG_PATH, "r", newline="", encoding="utf-8") as f:
            filas = list(csv.reader(f))
        if not filas:
            return 0, 0

        cabecera, datos = filas[0], filas[1:]
        recientes, por_mes = [], {}
        for fila in datos:
            # Una fila sin timestamp valido nunca se archiva ni se borra:
            # mejor dejarla en el CSV vivo que perder un registro.
            ts = fila[0] if fila else ""
            if re.match(r"\d{4}-\d{2}-\d{2}T", ts) and ts < corte:
                por_mes.setdefault(ts[:7], []).append(fila)
            else:
                recientes.append(fila)

        movidas = sum(len(v) for v in por_mes.values())
        borrados = _borrar_archivos_viejos(meses_archivo, dry_run)
        if movidas == 0 or dry_run:
            return movidas, borrados

        if meses_archivo > 0:
            os.makedirs(ARCHIVO_DIR, exist_ok=True)
            for mes, filas_mes in por_mes.items():
                ruta = os.path.join(ARCHIVO_DIR, f"registro_accesos_{mes}.csv.gz")
                es_nuevo = not os.path.exists(ruta)
                # "at" en gzip anade un miembro nuevo al final; zcat/gzip.open
                # lo leen como un solo fichero continuo.
                with gzip.open(ruta, "at", newline="", encoding="utf-8") as gz:
                    writer = csv.writer(gz)
                    if es_nuevo:
                        writer.writerow(cabecera)
                    writer.writerows(filas_mes)

        # Primero el archivo y despues el reemplazo del vivo: si algo falla
        # entre medias, la siguiente pasada archiva esas filas otra vez
        # (duplicadas) en vez de perderlas.
        tmp = LOG_PATH + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(cabecera)
            writer.writerows(recientes)
        os.replace(tmp, LOG_PATH)

    return movidas, borrados


def _borrar_archivos_viejos(meses_archivo, dry_run):
    if not os.path.isdir(ARCHIVO_DIR):
        return 0
    hoy = datetime.now()
    # Mes mas antiguo que se conserva, como numero AAAA*12+MM para poder
    # comparar sin aritmetica de fechas.
    limite = hoy.year * 12 + hoy.month - meses_archivo
    borrados = 0
    for nombre in os.listdir(ARCHIVO_DIR):
        m = re.fullmatch(r"registro_accesos_(\d{4})-(\d{2})\.csv\.gz", nombre)
        if m and int(m.group(1)) * 12 + int(m.group(2)) < limite:
            if not dry_run:
                os.remove(os.path.join(ARCHIVO_DIR, nombre))
            borrados += 1
    return borrados


def resumen():
    # Solo lectura: (filas, fecha mas antigua o None, bytes) del CSV vivo,
    # para que mantenimiento_logs.py explique un "0 que archivar".
    if not os.path.exists(LOG_PATH):
        return 0, None, 0
    with open(LOG_PATH, "r", newline="", encoding="utf-8") as f:
        filas = list(csv.reader(f))[1:]
    fechas = [fila[0] for fila in filas if fila and re.match(r"\d{4}-\d{2}-\d{2}T", fila[0])]
    return len(filas), (min(fechas) if fechas else None), os.path.getsize(LOG_PATH)
