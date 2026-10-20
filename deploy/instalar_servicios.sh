#!/usr/bin/env bash
# Genera y registra las unidades systemd de los 7 servicios de SGbarrera.
# Ejecutar en el miniPC (Ubuntu), desde dentro de la carpeta del proyecto:
#   cd SGbarrera
#   chmod +x deploy/instalar_servicios.sh
#   ./deploy/instalar_servicios.sh
#
# Requiere que ya exista el entorno virtual con las dependencias instaladas:
#   python3 -m venv venv
#   source venv/bin/activate
#   pip install -r requirements.txt
#
# El script solo CREA y REGISTRA las unidades -- no arranca ninguna. Al
# final te imprime los comandos exactos para activar cada una, porque
# mqtt-cliente y webhook-sgmas todavia no tienen credenciales reales, y
# lector-rfid necesita revisarse antes de arrancarlo (ver README).

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/venv/bin/python"
SERVICE_USER="$(whoami)"
UNIT_DIR="/etc/systemd/system"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "No encuentro $VENV_PYTHON -- crea antes el entorno virtual:"
  echo "  cd $PROJECT_ROOT && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

declare -A SERVICIOS=(
  [sgbarrera-sync-autorizados]="servicio_sync_autorizados.py"
  [sgbarrera-lector-rfid]="servicio_lector_rfid.py"
  [sgbarrera-receptor-lpr]="servicio_receptor_lpr.py"
  [sgbarrera-decision]="servicio_decision.py"
  [sgbarrera-eventos]="servicio_eventos.py"
  [sgbarrera-mqtt-cliente]="servicio_mqtt_cliente.py"
  [sgbarrera-webhook-sgmas]="servicio_webhook_sgmas.py"
)

for nombre in "${!SERVICIOS[@]}"; do
  script="${SERVICIOS[$nombre]}"
  echo "Generando $nombre.service ($script)..."
  sudo tee "$UNIT_DIR/$nombre.service" > /dev/null <<EOF
[Unit]
Description=SGbarrera - $nombre
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_ROOT/scripts
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_PYTHON $PROJECT_ROOT/scripts/$script
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
done

sudo systemctl daemon-reload

echo
echo "Unidades creadas y registradas (systemctl daemon-reload hecho)."
echo
echo "Para activar arranque automatico + arrancarlas ya, servicio a servicio:"
for nombre in "${!SERVICIOS[@]}"; do
  echo "  sudo systemctl enable --now $nombre.service"
done
echo
echo "AVISOS antes de arrancar:"
echo "  - sgbarrera-lector-rfid: revisa el README ('Desplegar como servicios')"
echo "    antes de arrancarlo -- con la implementacion actual (input() por"
echo "    stdin) no recibira nada al ejecutarse como servicio."
echo "  - sgbarrera-lector-rfid en modo \"serial\" (el lector UHF real): el"
echo "    usuario '$SERVICE_USER' necesita estar en el grupo 'dialout' para"
echo "    poder abrir el puerto (p.ej. /dev/ttyUSB0), si no fallara con"
echo "    'Permission denied'. Compruebalo con 'groups $SERVICE_USER'; si no"
echo "    esta, 'sudo usermod -aG dialout $SERVICE_USER' y cerrar sesion y"
echo "    volver a entrar (o reiniciar el servicio despues)."
echo "  - sgbarrera-mqtt-cliente y sgbarrera-webhook-sgmas: fallaran en bucle"
echo "    (inofensivo, Restart=on-failure) hasta que rellenes las"
echo "    credenciales reales en config/mqtt_config.json y config/webhook_config.json."
