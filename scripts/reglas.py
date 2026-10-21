# Logica pura de negocio: normalizar, buscar en la lista cacheada de
# /autorizados, y decidir PERMITIDO/DENEGADO + motivo. No hace peticiones
# de red -- siempre trabaja sobre la cache en disco que escribe
# servicio_sync_autorizados.py, para poder decidir aunque el VPS este
# caido en ese instante.

import json
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CACHE_AUTORIZADOS = os.path.join(BASE_DIR, "config", "autorizados_cache.json")

# Cabecera EPC + prefijo de empresa fijo de nuestro lote de tags (confirmado
# con lecturas reales -- ver README, "Formato del RFID"). La antena UHF es de
# largo alcance (4-6m) y capta tambien tags ajenos que pasen cerca (otros
# vehiculos, objetos con su propio RFID, etc.) -- esos tienen EPC completo
# valido pero con OTRO prefijo, y es_tag_propio() los reconoce por eso. Si
# algun dia se cambia de lote/proveedor de tags, este es el unico sitio que
# hay que tocar.
PREFIJO_EPC_PROPIO = "E2806894"

# Cache en memoria de autorizados_cache.json, indexada por matricula y por
# rfid, para no releer ni recorrer el fichero entero en cada lectura de
# tag/matricula (con 1400+ registros ya no es gratis hacerlo en cada
# lectura). Se invalida sola comparando el mtime del fichero: lo escribe
# servicio_sync_autorizados.py en un PROCESO APARTE (de forma atomica, con
# os.replace), asi que aqui no hay otra forma de "enterarse" del cambio.
# Si hay varios registros con la misma matricula/rfid (huecos horarios
# distintos, ver "PRECONTRATADO" en el propio JSON), se guardan TODOS y
# _elegir() decide cual aplica en este instante (el que esta activo y en
# ventana; si ninguno, el primero en el orden del fichero).
_cache = {"mtime": None, "lista": [], "por_matricula": {}, "por_rfid_completo": {}, "por_rfid_sufijo": {}}


def normalizar_matricula(texto):
    return "".join(c for c in texto.upper() if c.isalnum())


def _ultimos_4_bytes_hex(epc_hex):
    return epc_hex.strip().upper()[-8:]


def _indexar(lista):
    por_matricula, por_rfid_completo, por_rfid_sufijo = {}, {}, {}
    for registro in lista:
        matricula = registro.get("matricula")
        if matricula:
            por_matricula.setdefault(normalizar_matricula(matricula), []).append(registro)
        rfid = registro.get("rfid")
        if rfid:
            rfid = rfid.strip().upper()
            por_rfid_completo.setdefault(rfid, []).append(registro)
            por_rfid_sufijo.setdefault(_ultimos_4_bytes_hex(rfid), []).append(registro)
    return por_matricula, por_rfid_completo, por_rfid_sufijo


def _elegir(candidatos):
    """Entre los registros de una misma matricula/rfid, el que aplica ahora:
    el primero activo y dentro de su ventana; si no hay ninguno, el primero
    activo (para que evaluar() diga FUERA_HORARIO y no CADUCADO si hay
    huecos de otro dia); y si tampoco, el primero del fichero. None si no
    hay candidatos."""
    if not candidatos:
        return None
    for r in candidatos:
        if r.get("activo", False) and _en_ventana(r):
            return r
    for r in candidatos:
        if r.get("activo", False):
            return r
    return candidatos[0]


def _refrescar_cache():
    try:
        mtime = os.path.getmtime(RUTA_CACHE_AUTORIZADOS)
    except OSError:
        mtime = None

    if mtime == _cache["mtime"]:
        return  # fichero sin cambios desde la ultima vez -- nada que releer

    lista = []
    if mtime is not None:
        try:
            with open(RUTA_CACHE_AUTORIZADOS, "r", encoding="utf-8") as f:
                lista = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            # servicio_sync_autorizados.py escribe con os.replace (atomico), asi
            # que no deberia haber una escritura a medias -- pero si el fichero
            # falla por cualquier otro motivo, mejor seguir con la cache
            # anterior que tirar servicio_decision.py entero.
            print(f"[reglas] no se pudo releer {RUTA_CACHE_AUTORIZADOS} ({e}); se mantiene la cache anterior")
            return

    _cache["mtime"] = mtime
    _cache["lista"] = lista
    _cache["por_matricula"], _cache["por_rfid_completo"], _cache["por_rfid_sufijo"] = _indexar(lista)


def cargar_autorizados():
    _refrescar_cache()
    return _cache["lista"]


def buscar_por_matricula(matricula):
    _refrescar_cache()
    return _elegir(_cache["por_matricula"].get(normalizar_matricula(matricula)))


def es_epc_completo(valor):
    """True si `valor` ya es un EPC completo (24 hex) tal cual lo manda el
    lector, en vez del decimal truncado de fabrica ("10 no. in D (four
    byte)") -- pasa si alguna vez se consigue dejar el lector en modo de
    salida hexadecimal completa (ver README, "Formato del RFID"). En ese
    caso no hace falta huella ni busqueda por cola: el valor leido YA es
    el identificador completo, letra a letra igual que en
    config/autorizados_cache.json.
    """
    v = str(valor).strip().upper()
    return len(v) == 24 and all(c in "0123456789ABCDEF" for c in v)


def es_tag_propio(valor):
    """True solo si `valor` tiene 24 caracteres y empieza por
    PREFIJO_EPC_PROPIO ("E2806894") -- lo unico que servicio_lector_rfid.py
    manda a decision.py; cualquier otra cosa se descarta en el origen,
    antes de tocar el almacen compartido. Ya NO deja pasar el decimal
    truncado de fabrica como red de seguridad (se quitó a proposito: con
    el lector ya confirmado en modo EPC completo, se prefirió la regla
    simple de dos condiciones a mantener ese segundo camino).
    """
    v = str(valor).strip().upper()
    return len(v) == 24 and v.startswith(PREFIJO_EPC_PROPIO)


def fingerprint_rfid(decimal_leido):
    """Convierte el decimal que manda el lector UHF (formato de fabrica
    "10 no. in D (four byte)") a hex de 8 caracteres -- los unicos 4 bytes
    del EPC que el lector realmente transmite (ver buscar_por_rfid). Esto es
    lo maximo que se puede saber de un tag que no esta en la lista de
    autorizados: no es el EPC completo, es solo su "huella" de 4 bytes.

    Devuelve None si `decimal_leido` no es un decimal valido, o si no cabe
    en 4 bytes (0..4294967295) -- una trama corrupta o a medias (p.ej. dos
    lecturas pegadas por un fallo de framing) no debe tumbar el servicio
    ni generar una "huella" de mas de 8 caracteres (ver buscar_por_rfid).
    """
    try:
        numero = int(str(decimal_leido).strip())
    except (TypeError, ValueError):
        return None
    if not (0 <= numero <= 0xFFFFFFFF):
        return None
    return f"{numero:08X}"


def buscar_por_rfid(decimal_leido):
    """`decimal_leido` es el texto que manda el lector UHF por el puerto serie.
    Normalmente es el formato de fabrica ("10 no. in D (four byte)"): un
    decimal de hasta 10 digitos que NO es el EPC completo del tag --
    comprobado con lecturas reales, ese decimal son exactamente los
    ULTIMOS 4 BYTES del EPC (96 bits/12 bytes), en big-endian. Los otros 8
    bytes del EPC se pierden en el propio lector antes de llegar aqui, así
    que no se puede reconstruir el EPC completo -- en su lugar, se compara
    ese mismo "trozo" (últimos 4 bytes) contra cada `rfid` (EPC completo,
    24 hex) de la lista de autorizados. Evita depender de reconfigurar el
    lector (ver README.md).

    Si el lector SI esta mandando el EPC completo (24 hex, ver
    es_epc_completo) -- p.ej. porque se consiguio dejarlo en modo de
    salida hexadecimal completa -- se compara por igualdad directa en vez
    de por huella, sin ninguna ambiguedad.
    """
    _refrescar_cache()
    if es_epc_completo(decimal_leido):
        epc = str(decimal_leido).strip().upper()
        return _elegir(_cache["por_rfid_completo"].get(epc))

    fingerprint = fingerprint_rfid(decimal_leido)
    if fingerprint is None:
        return None
    return _elegir(_cache["por_rfid_sufijo"].get(fingerprint))


def describir_rfid(decimal_leido):
    """Texto legible para logs/terminal a partir de una lectura RFID cruda:
    el EPC completo de 24 caracteres ("E280...", el mismo formato que usa
    config/autorizados_cache.json) siempre que se pueda saber -- porque el
    lector ya lo manda asi (es_epc_completo) o porque el tag esta dado de
    alta y se encontro por huella -- y si no, su huella de 4 bytes en hex,
    que es lo maximo que se puede saber de un tag sin match y sin EPC
    completo (ver fingerprint_rfid).

    Es solo para mostrar: quien decide PERMITIDO/DENEGADO es evaluar(), en
    servicio_decision.py, a partir de buscar_por_rfid() -- no esta funcion.
    """
    if es_epc_completo(decimal_leido):
        return str(decimal_leido).strip().upper()

    registro = buscar_por_rfid(decimal_leido)
    if registro and registro.get("rfid"):
        return registro["rfid"].strip().upper()
    huella = fingerprint_rfid(decimal_leido)
    if huella is None:
        return f"formato no reconocido: {decimal_leido}"
    return f"sin registrar (huella {huella})"


def _en_ventana(registro):
    ahora = datetime.now(timezone.utc)
    desde = datetime.fromisoformat(registro["valido_desde"].replace("Z", "+00:00"))
    hasta = datetime.fromisoformat(registro["valido_hasta"].replace("Z", "+00:00"))
    return desde <= ahora <= hasta


def _licencia_corta(licencia_codigo):
    """"LAS PALMAS DE GRAN CANARIA/111" -> "LPGC111"; "T-173" -> "T173";
    "AGAETE/1" -> "AGAETE1". Prefijo = iniciales de las palabras (sin "DE"/"DEL")
    si tiene varias, o la palabra entera si es una sola."""
    prefijo, _, numero = licencia_codigo.strip().replace("-", "/").rpartition("/")
    palabras = [w for w in prefijo.split() if w.upper() not in ("DE", "DEL")]
    if len(palabras) > 1:
        prefijo = "".join(w[0] for w in palabras)
    return f"{prefijo.replace(' ', '')}{numero}".upper()


def describir_registro(registro):
    """Sufijo para el log de decision: " - <licencia> - <HH:MM,DD/MM>" a partir
    del registro de /autorizados. La hora es el valido_desde en hora local de
    la Pi (solo si el registro tiene un inicio real: los permanentes traen el
    1900-01-01 de relleno y no llevan hora). Vacio si no hubo match.
    """
    if not registro:
        return ""
    partes = []
    licencia = registro.get("licencia_codigo")
    if licencia:
        partes.append(_licencia_corta(licencia))
    try:
        desde = datetime.fromisoformat(registro["valido_desde"].replace("Z", "+00:00"))
        if desde.year > 1900:
            partes.append(desde.astimezone().strftime("%H:%M,%d/%m"))
    except (KeyError, ValueError):
        pass
    return "".join(f" - {p}" for p in partes)


def evaluar(registro):
    """(resultado, motivo) a partir de un registro de /autorizados (o None si no hubo match).

    LICENCIA_DESCONOCIDA no se genera aqui -- no hay forma de distinguirlo
    localmente de NO_AUTORIZADO con los campos de este endpoint (ver README).
    """
    if registro is None:
        return "DENEGADO", "NO_AUTORIZADO"
    if not registro.get("activo", False):
        return "DENEGADO", "CADUCADO"
    if not _en_ventana(registro):
        return "DENEGADO", "FUERA_HORARIO"
    return "PERMITIDO", ""
