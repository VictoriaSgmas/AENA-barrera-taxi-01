# Lectura del lector RFID. Tres modos, elegidos en config/rfid_config.json:
#
#   "stdin" -- para desarrollo/pruebas con una terminal a mano (Windows o
#       Linux) y un lector que emula teclado (los antiguos, de baja
#       frecuencia): basta con tener el foco en esta terminal. NO
#       funciona como servicio systemd -- un servicio no tiene ninguna
#       ventana con foco a la que un teclado emulado pueda escribirle.
#
#   "evdev" -- para producción en Linux como servicio, con un lector que
#       emula teclado: lee directamente el dispositivo de entrada
#       (/dev/input/eventX) a bajo nivel, sin depender de ninguna ventana
#       con foco. Necesita:
#         - El paquete "evdev" (solo Linux, ya en requirements.txt).
#         - Averiguar la ruta del dispositivo: `ls /dev/input/by-id/`
#           con el lector conectado (busca uno que acabe en "-event-kbd")
#           y ponerla en "dispositivo_evdev" en config/rfid_config.json.
#         - Que el usuario que ejecuta el servicio pueda leer ese
#           dispositivo: `sudo usermod -aG input <tu_usuario>` (hace
#           falta cerrar sesión y volver a entrar para que se aplique).
#
#   "serial" -- el lector UHF real (R16-9DB). Este lector NO emula
#       teclado: hay que ponerlo en modo "Active reading" (lectura
#       automatica) con la salida por "USB virtual serial port" desde el
#       software DEMO del fabricante (pestaña "Serial port function" --
#       ver manuales/lector rfid uhf R16-9DB/). Configurado asi, el
#       propio lector manda cada lectura como texto por ese puerto COM,
#       una linea por lectura, sin que este script tenga que hablarle
#       ningun protocolo. Necesita:
#         - El paquete "pyserial" (ya en requirements.txt).
#         - El puerto en "puerto_serie" de config/rfid_config.json. En
#           Windows, "COM4". En Linux, mejor la ruta estable de
#           `/dev/serial/by-id/` (con el lector conectado) que un
#           `/dev/ttyUSB0` a secas -- ese numero puede cambiar entre
#           reinicios o si hay otro adaptador USB-serie de por medio
#           (p.ej. el router 4G). El baudrate en "baudios" (9600 es el
#           de fabrica del lector -- coincide con lo que muestre la
#           pestaña "Serial port function" del software DEMO).
#         - En Linux, que el usuario que ejecuta el servicio pueda leer
#           ese dispositivo (pertenece a root:dialout de fabrica):
#           `sudo usermod -aG dialout <tu_usuario>` (cerrar sesion y
#           volver a entrar para que se aplique) -- mismo motivo que el
#           grupo "input" del modo evdev, de arriba.
#       FORMATO -- NO hace falta tocar la pestaña "output format setting"
#       del software DEMO ni reconfigurar el lector: se deja en su
#       formato de fabrica, "10 no. in D (four byte)" (un decimal de
#       hasta 10 digitos). Se intento cambiar a salida hexadecimal del
#       EPC completo, pero el software DEMO del fabricante no resulto
#       fiable para eso (confirmado con capturas USB, ver README.md). En
#       su lugar, el desajuste de formato se resuelve por software: ese
#       decimal son exactamente los ULTIMOS 4 BYTES del EPC (96 bits) en
#       big-endian, y reglas.py (fingerprint_rfid/buscar_por_rfid/
#       describir_rfid) traduce cada lectura al EPC completo (formato
#       "E280...", 24 hex, igual que config/autorizados_cache.json)
#       cuando el tag esta dado de alta. Detalle en reglas.py y
#       README.md ("Formato del RFID (resuelto)").
#
# leer_rfid_bloqueante() mantiene siempre la misma firma (sin argumentos,
# devuelve texto o None) para no tener que tocar el resto del sistema al
# cambiar de modo.

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CONFIG = os.path.join(BASE_DIR, "config", "rfid_config.json")

_dispositivo_evdev = None
_puerto_serie = None


def _config():
    if not os.path.exists(RUTA_CONFIG):
        return {"modo": "stdin"}
    with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def _leer_stdin():
    codigo = input().strip()
    return codigo.upper() if codigo else None


def _leer_evdev():
    global _dispositivo_evdev
    import evdev
    from evdev import ecodes

    if _dispositivo_evdev is None:
        ruta = _config()["dispositivo_evdev"]
        _dispositivo_evdev = evdev.InputDevice(ruta)
        _dispositivo_evdev.grab()  # que las pulsaciones no lleguen tambien al resto del sistema

    buffer = ""
    for evento in _dispositivo_evdev.read_loop():
        if evento.type != ecodes.EV_KEY or evento.value != 1:  # solo "tecla pulsada"
            continue
        tecla = ecodes.KEY.get(evento.code, "")
        if tecla == "KEY_ENTER":
            codigo, buffer = buffer.strip(), ""
            if codigo:
                return codigo.upper()
            continue
        if tecla.startswith("KEY_") and len(tecla) == 5:  # KEY_0.."KEY_9", KEY_A.."KEY_Z"
            buffer += tecla[-1]


def _leer_serial():
    global _puerto_serie
    import serial

    if _puerto_serie is None:
        cfg = _config()
        _puerto_serie = serial.Serial(
            port=cfg["puerto_serie"],
            baudrate=cfg.get("baudios", 9600),
            timeout=cfg.get("timeout_seg", 1),
        )
        # Si al abrir el puerto ya habia bytes a medias esperando en el
        # buffer del sistema operativo (p.ej. la cola de una trama que el
        # lector empezo a mandar antes de que este script se pusiera a
        # escuchar), el primer read_until() de mas abajo los devolveria
        # como si fueran una lectura valida -- normalmente un trozo final
        # tipo "977" en vez del decimal completo. Se descartan aqui, antes
        # de leer nada.
        _puerto_serie.reset_input_buffer()

    # El lector enmarca cada lectura entre STX (0x02) y ETX (0x03), con el
    # separador elegido (aqui, CRLF por "add Enter") metido en medio --
    # pase lo que pase con el formato de salida (decimal/hex, sec.
    # "output format setting" del DEMO). Un readline() a secas corta en
    # el "\n" y deja el STX pegado delante y el ETX sin leer, descuadrando
    # la siguiente lectura -- por eso se lee hasta el ETX y se descartan
    # los dos bytes de marco explicitamente.
    trama = _puerto_serie.read_until(b"\x03")
    # Dos formas de trama incompleta/desincronizada, mismo tratamiento
    # (nada, en vez de un codigo a medias que parezca valido):
    #   - no termina en ETX: timeout a mitad de trama.
    #   - no empieza en STX: se empezo a leer a mitad de una trama ya en
    #     curso (p.ej. dos lecturas que se pisan por el "search card
    #     interval" del lector) -- sin este chequeo, esto colaba un trozo
    #     final del decimal anterior (tipo "977" de "...86977") como si
    #     fuera una lectura nueva y valida.
    if not trama.endswith(b"\x03") or not trama.startswith(b"\x02"):
        return None
    codigo = trama.strip(b"\x02\x03").decode("ascii", errors="ignore").strip()
    return codigo.upper() if codigo else None


def leer_rfid_bloqueante():
    modo = _config().get("modo")
    if modo == "evdev":
        return _leer_evdev()
    if modo == "serial":
        return _leer_serial()
    return _leer_stdin()


if __name__ == "__main__":
    # Prueba rapida del hardware solo, sin tocar el almacen compartido ni
    # el resto del sistema -- para la verificacion de la seccion 9.3 del
    # PDF: pasar unos tags conocidos y comprobar que el codigo impreso
    # coincide con el EPC ("E280...") de config/autorizados_cache.json.
    # Import local (como el de "evdev"/"serial" mas arriba): reglas.py es
    # solo para esta traduccion de pantalla, no hace falta al importar
    # este modulo como libreria (servicio_lector_rfid.py, etc.).
    import reglas

    print(f"[rfid_reader] modo: {_config().get('modo', 'stdin')} -- Ctrl+C para salir")
    while True:
        codigo = leer_rfid_bloqueante()
        if codigo:
            print(f"{codigo} -> {reglas.describir_rfid(codigo)}")
