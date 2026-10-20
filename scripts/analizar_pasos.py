# Resume el journal de los servicios en "pasos" (un coche = un grupo de
# lecturas y pulsos al rele seguidos) para comparar, paso a paso, cuantas
# lecturas llegaron y cuantos pulsos salieron hacia el rele con lo que se vio
# hacer a la barrera. Solo lee: no toca nada. Uso, en el miniPC:
#
#   journalctl -u sgbarrera-receptor-lpr -u sgbarrera-decision -u sgbarrera-mqtt-cliente \
#       --since "today" -o short-iso | venv/bin/python scripts/analizar_pasos.py
#
# Los instantes se muestran en segundos desde el primer evento del paso. Un
# hueco de mas de --hueco segundos (8 por defecto) separa un paso del siguiente.

import argparse
import re
import sys
from datetime import datetime

RE_HORA = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?")
RE_LECTURA = re.compile(r"\[(?:receptor-lpr|lector-rfid)\] leido: (\S+)")


def clasificar(linea):
    # Devuelve (tipo, detalle) o None si la linea no interesa.
    m = RE_LECTURA.search(linea)
    if m:
        return "lectura", m.group(1)
    if "[decision] respuesta del rele" in linea:
        return "pulso", "decision"
    if "[mqtt-cliente] respuesta del rele" in linea:
        return "pulso", "mqtt"
    if "[decision] ERROR al pulsar el rele" in linea:
        return "fallo", "decision"
    if "rele no pulsado" in linea:
        return "omitido", ""
    return None


def leer_eventos(lineas):
    eventos = []
    for linea in lineas:
        m = RE_HORA.match(linea)
        clase = clasificar(linea) if m else None
        if not clase:
            continue
        t = datetime.strptime(f"{m.group(1)}T{m.group(2)}", "%Y-%m-%dT%H:%M:%S")
        segundos = t.timestamp() + (float(m.group(3)) if m.group(3) else 0.0)
        eventos.append((segundos, f"{m.group(1)} {m.group(2)}", *clase))
    return eventos


def agrupar(eventos, hueco):
    pasos, actual = [], []
    for ev in eventos:
        if actual and ev[0] - actual[-1][0] > hueco:
            pasos.append(actual)
            actual = []
        actual.append(ev)
    if actual:
        pasos.append(actual)
    return pasos


def main():
    parser = argparse.ArgumentParser(description="Resumen por pasos del journal de SGbarrera")
    parser.add_argument("--hueco", type=float, default=8, help="segundos de silencio que separan dos pasos")
    hueco = parser.parse_args().hueco

    pasos = agrupar(leer_eventos(sys.stdin), hueco)
    if not pasos:
        print("No hay lecturas ni pulsos en la entrada.")
        return

    for n, paso in enumerate(pasos, 1):
        t0 = paso[0][0]
        lecturas = [e for e in paso if e[2] == "lectura"]
        pulsos = [e for e in paso if e[2] == "pulso"]
        fallos = [e for e in paso if e[2] == "fallo"]
        omitidos = [e for e in paso if e[2] == "omitido"]
        matriculas = sorted({e[3] for e in lecturas})
        print(f"Paso {n}  {paso[0][1]}  {','.join(matriculas) or '-'}")
        print(f"   lecturas={len(lecturas)}  pulsos={len(pulsos)}  fallidos={len(fallos)}  omitidos={len(omitidos)}  duracion={paso[-1][0] - t0:.0f}s")
        if lecturas:
            print("   lecturas en s:", " ".join(f"{e[0] - t0:.0f}" for e in lecturas))
        if pulsos:
            print("   pulsos   en s:", " ".join(f"{e[0] - t0:.0f}" for e in pulsos))


if __name__ == "__main__":
    main()
