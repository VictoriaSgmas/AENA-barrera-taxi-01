#!/usr/bin/env bash
# Instala la retencion de logs de SGbarrera (30 dias) en el miniPC (Ubuntu):
#   1. Limita el journal de systemd (donde van los print() de los 7 servicios).
#   2. Registra un timer que cada noche a las 02:00 poda registro_accesos.csv y
#      barrera.db (scripts/mantenimiento_logs.py).
#
#   cd SGbarrera
#   chmod +x deploy/instalar_mantenimiento_logs.sh
#   ./deploy/instalar_mantenimiento_logs.sh
#
# Es idempotente: se puede volver a ejecutar sin romper nada.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/venv/bin/python"
SERVICE_USER="$(whoami)"
UNIT_DIR="/etc/systemd/system"
NOMBRE="sgbarrera-mantenimiento-logs"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "No encuentro $VENV_PYTHON -- crea antes el entorno virtual (ver instalar_servicios.sh)."
  exit 1
fi

# --- 1. journald: tope de 30 dias Y de tamano ---------------------------------
# MaxRetentionSec borra entradas con mas de 30 dias; MaxFileSec=1day hace que
# el journal rote a diario (journald solo puede borrar ficheros completos, asi
# la retencion tiene precision de un dia). SystemMaxUse es el techo duro: si
# algun servicio entra en bucle de errores (p.ej. mqtt-cliente sin
# credenciales, reiniciando cada 5 s), journald descarta lo mas viejo en vez
# de llenar el disco.
echo "Configurando journald (30 dias / 200 MB max)..."
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/sgbarrera.conf > /dev/null <<EOF
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=50M
MaxRetentionSec=30day
MaxFileSec=1day
EOF
sudo systemctl restart systemd-journald
# Recorta ya lo acumulado hasta ahora (la config nueva solo actua sobre lo que
# rota a partir de ahora).
sudo journalctl --vacuum-time=30d --vacuum-size=200M

# --- 2. timer de poda del CSV y la BD -----------------------------------------
echo "Generando $NOMBRE.service y $NOMBRE.timer..."
sudo tee "$UNIT_DIR/$NOMBRE.service" > /dev/null <<EOF
[Unit]
Description=SGbarrera - poda de logs (CSV y BD, retencion 30 dias)

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$PROJECT_ROOT/scripts
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_PYTHON $PROJECT_ROOT/scripts/mantenimiento_logs.py
# Que no compita con los servicios que abren la barrera
Nice=10
IOSchedulingClass=idle
EOF

# Persistent=true: si el miniPC estaba apagado a las 02:00, la poda corre en
# cuanto arranca en vez de saltarse ese dia.
sudo tee "$UNIT_DIR/$NOMBRE.timer" > /dev/null <<EOF
[Unit]
Description=SGbarrera - poda de logs cada noche a las 02:00

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$NOMBRE.timer"

echo
echo "Hecho. Comprobaciones utiles:"
echo "  systemctl list-timers $NOMBRE.timer      # proxima ejecucion"
echo "  sudo systemctl start $NOMBRE.service      # ejecutarla ya, a mano"
echo "  journalctl -u $NOMBRE.service             # que borro la ultima vez"
echo "  journalctl --disk-usage                   # tamano actual del journal"
echo "  timedatectl                               # la hora de las 02:00 es la del sistema:"
echo "                                            # debe salir Atlantic/Canary"
