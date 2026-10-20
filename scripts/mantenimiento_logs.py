# Mantenimiento de logs: deja solo los ultimos DIAS_VIVOS dias a mano y
# comprime/borra el resto. Lo lanza el timer systemd sgbarrera-mantenimiento-logs
# cada noche a las 02:00 (ver deploy/instalar_mantenimiento_logs.sh), pero
# tambien se puede lanzar a mano:
#
#   python scripts/mantenimiento_logs.py --dry-run   # solo cuenta, no toca nada
#   python scripts/mantenimiento_logs.py --dry-run --dias 7   # simula otra retencion
#   python scripts/mantenimiento_logs.py
#
# Que hace:
#   - registro_accesos.csv: lo de mas de DIAS_VIVOS dias se comprime a
#     logs/archivo/registro_accesos_AAAA-MM.csv.gz; esos archivos mensuales se
#     borran pasados MESES_ARCHIVO meses (0 = no archivar, borrar directamente).
#   - barrera.db: borra lecturas y comandos ya procesados de mas de DIAS_VIVOS
#     dias y compacta el fichero para devolver el espacio al disco.
# Siempre empieza diciendo cuantos datos hay y desde cuando, para que un "0 que
# podar" se entienda solo: si lo mas antiguo tiene 4 dias, no hay nada que podar
# hasta que cumpla DIAS_VIVOS.
# El journal de systemd (donde van los print() de los servicios) NO lo toca
# este script: lo limita el propio journald con la config que instala el .sh.

import argparse
from datetime import datetime

import almacen
import logger

DIAS_VIVOS = 30
MESES_ARCHIVO = 3


def _tam(n):
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _desde(fecha):
    if not fecha:
        return "sin datos"
    try:
        dias = (datetime.now() - datetime.fromisoformat(fecha)).days
    except ValueError:
        return fecha
    return f"{fecha[:10]} ({dias} dias)"


def main():
    parser = argparse.ArgumentParser(description="Poda de logs de SGbarrera")
    parser.add_argument("--dry-run", action="store_true", help="solo informa, no modifica nada")
    parser.add_argument("--dias", type=int, help="solo con --dry-run: simula otra retencion (p.ej. 7) para ver que haria con los datos reales")
    args = parser.parse_args()
    dry_run = args.dry_run
    if args.dias is not None and not dry_run:
        parser.error("--dias solo se puede usar con --dry-run; para cambiar la retencion de verdad edita DIAS_VIVOS")
    if args.dias is not None and args.dias < 0:
        parser.error("--dias no puede ser negativo")
    dias = DIAS_VIVOS if args.dias is None else args.dias
    prefijo = "[mantenimiento-logs]" + (" (dry-run)" if dry_run else "")

    filas, desde, bytes_csv = logger.resumen()
    print(f"{prefijo} CSV ahora: {filas} filas, {_tam(bytes_csv)}, desde {_desde(desde)}")
    tablas, bytes_db = almacen.resumen()
    print(f"{prefijo} BD ahora: lecturas={tablas['lecturas'][0]} desde {_desde(tablas['lecturas'][1])}, "
          f"comandos={tablas['comandos'][0]} eventos_pendientes={tablas['eventos_pendientes'][0]}, {_tam(bytes_db)}")

    movidas, archivos_borrados = logger.archivar_antiguos(dias, MESES_ARCHIVO, dry_run)
    accion = "archivarian" if dry_run else "archivadas"
    print(f"{prefijo} CSV: {movidas} filas de mas de {dias} dias {accion}; "
          f"{archivos_borrados} archivos mensuales de mas de {MESES_ARCHIVO} meses borrados")

    borradas = almacen.purgar_antiguos(dias, dry_run)
    print(f"{prefijo} BD: lecturas={borradas['lecturas']} comandos={borradas['comandos']} "
          f"procesados de mas de {dias} dias {'se borrarian' if dry_run else 'borrados'}")

    if not dry_run and any(borradas.values()):
        almacen.compactar()
        print(f"{prefijo} BD compactada")


if __name__ == "__main__":
    main()
