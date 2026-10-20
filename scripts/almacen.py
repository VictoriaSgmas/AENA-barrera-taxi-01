# Almacen local compartido entre todos los servicios de la Raspberry Pi.
# Cada servicio lee/escribe aqui y no necesita saber nada de los demas: si
# uno se cae, los otros siguen leyendo/escribiendo sin enterarse. Es
# SQLite en un fichero (logs/barrera.db), en modo WAL para que varios
# procesos puedan leer y escribir a la vez sin bloquearse entre si.

import contextlib
import json
import os
import sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_DB = os.path.join(BASE_DIR, "logs", "barrera.db")


@contextlib.contextmanager
def _conectar():
    # `with con:` (sqlite3.Connection como context manager) solo hace
    # commit/rollback de la transaccion -- NUNCA cierra la conexion. Como
    # cada funcion de este modulo abre una conexion nueva por llamada (y
    # hay varios servicios en procesos separados llamando a estas
    # funciones varias veces por segundo, p.ej. decision.py en su bucle de
    # 0.5s), sin cerrar explicitamente las conexiones se van acumulando
    # descriptores de fichero hasta agotar el limite del proceso -- visto
    # en produccion: memoria subiendo sin parar durante varios minutos y
    # luego sqlite3.OperationalError: unable to open database file en
    # _conectar(), porque el sistema ya no podia abrir ni un descriptor
    # mas. Envolver _conectar() con @contextlib.contextmanager (en vez de
    # devolver la conexion tal cual) permite garantizar el close() con un
    # finally sin tener que tocar ninguno de los "with _conectar() as
    # con:" que ya hay en este fichero -- la forma de llamarlo no cambia.
    os.makedirs(os.path.dirname(RUTA_DB), exist_ok=True)
    con = sqlite3.connect(RUTA_DB, timeout=10)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        with con:
            yield con
    finally:
        con.close()


def inicializar():
    with _conectar() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS lecturas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                origen TEXT NOT NULL,
                valor TEXT NOT NULL,
                procesado INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS eventos_pendientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creado_en TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comandos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                accion TEXT NOT NULL,
                origen TEXT NOT NULL,
                id_peticion TEXT,
                usuario TEXT,
                procesado INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS estado (
                clave TEXT PRIMARY KEY,
                valor TEXT
            );
        """)


# --- lecturas (RFID / LPR), las escribe lector-rfid / receptor-lpr, las lee decision ---

def insertar_lectura(origen, valor):
    with _conectar() as con:
        con.execute(
            "INSERT INTO lecturas (timestamp, origen, valor) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), origen, valor),
        )


def lecturas_pendientes():
    with _conectar() as con:
        cur = con.execute("SELECT id, timestamp, origen, valor FROM lecturas WHERE procesado = 0 ORDER BY id")
        return cur.fetchall()


def marcar_lectura_procesada(id_lectura):
    with _conectar() as con:
        con.execute("UPDATE lecturas SET procesado = 1 WHERE id = ?", (id_lectura,))


# --- eventos pendientes de enviar al VPS, los escribe decision, los lee/vacia eventos ---

def encolar_evento(payload):
    with _conectar() as con:
        con.execute(
            "INSERT INTO eventos_pendientes (creado_en, payload) VALUES (?, ?)",
            (datetime.now().isoformat(timespec="seconds"), json.dumps(payload, ensure_ascii=False)),
        )


def eventos_pendientes():
    with _conectar() as con:
        cur = con.execute("SELECT id, payload FROM eventos_pendientes ORDER BY id")
        return [(id_, json.loads(payload)) for id_, payload in cur.fetchall()]


def eliminar_evento(id_fila):
    with _conectar() as con:
        con.execute("DELETE FROM eventos_pendientes WHERE id = ?", (id_fila,))


# --- comandos (MQTT remoto / webhook de cambio de lista) ---

def insertar_comando(accion, origen, id_peticion=None, usuario=None):
    with _conectar() as con:
        con.execute(
            "INSERT INTO comandos (timestamp, accion, origen, id_peticion, usuario) VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), accion, origen, id_peticion, usuario),
        )


def comandos_pendientes(accion=None):
    with _conectar() as con:
        if accion:
            cur = con.execute(
                "SELECT id, accion, origen, id_peticion, usuario FROM comandos "
                "WHERE procesado = 0 AND accion = ? ORDER BY id",
                (accion,),
            )
        else:
            cur = con.execute(
                "SELECT id, accion, origen, id_peticion, usuario FROM comandos WHERE procesado = 0 ORDER BY id"
            )
        return cur.fetchall()


def marcar_comando_procesado(id_comando):
    with _conectar() as con:
        con.execute("UPDATE comandos SET procesado = 1 WHERE id = ?", (id_comando,))


# --- estado compartido, para que mqtt-cliente publique "estado" sin preguntarle a nadie ---

def set_estado(clave, valor):
    with _conectar() as con:
        con.execute(
            "INSERT INTO estado (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, str(valor)),
        )


def get_estado(clave, por_defecto=None):
    with _conectar() as con:
        cur = con.execute("SELECT valor FROM estado WHERE clave = ?", (clave,))
        fila = cur.fetchone()
        return fila[0] if fila else por_defecto


# --- mantenimiento nocturno (mantenimiento_logs.py) ---

def purgar_antiguos(dias, dry_run=False):
    # Borra lecturas y comandos YA PROCESADOS con mas de `dias` dias. Las
    # filas sin procesar no se tocan nunca (aunque sean viejas, el servicio
    # que las espera todavia las necesita), ni eventos_pendientes: es la
    # cola de lo que aun no se ha podido enviar al VPS, no un log.
    corte = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    borradas = {}
    with _conectar() as con:
        for tabla in ("lecturas", "comandos"):
            where = "procesado = 1 AND timestamp < ?"
            if dry_run:
                cur = con.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {where}", (corte,))
                borradas[tabla] = cur.fetchone()[0]
            else:
                cur = con.execute(f"DELETE FROM {tabla} WHERE {where}", (corte,))
                borradas[tabla] = cur.rowcount
    return borradas


def compactar():
    # DELETE no devuelve el espacio al disco: hace falta VACUUM. Y VACUUM no
    # puede correr dentro de una transaccion, asi que va por una conexion
    # aparte en modo autocommit en vez de por _conectar().
    con = sqlite3.connect(RUTA_DB, timeout=30, isolation_level=None)
    try:
        con.execute("VACUUM")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def resumen():
    # Solo lectura: (filas, fecha mas antigua) de cada tabla y el tamano en
    # disco (fichero + WAL), para que mantenimiento_logs.py explique un "0
    # que borrar".
    tablas = {}
    with _conectar() as con:
        for tabla, columna in (("lecturas", "timestamp"), ("comandos", "timestamp"), ("eventos_pendientes", "creado_en")):
            tablas[tabla] = con.execute(f"SELECT COUNT(*), MIN({columna}) FROM {tabla}").fetchone()
    tam = sum(os.path.getsize(p) for p in (RUTA_DB, RUTA_DB + "-wal") if os.path.exists(p))
    return tablas, tam


inicializar()
