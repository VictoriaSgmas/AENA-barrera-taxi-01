# Sistema de acceso de taxis — Raspberry Pi en 7 servicios independientes

Estado actual: el sistema ya no es un único proceso monolítico. Está
partido en **7 servicios independientes** (uno por cada fila de la tabla
8.2 de `manuales/SGbarrera_1.2v.pdf`), cada uno ejecutable por separado, de
forma que si uno falla los demás siguen funcionando. Se comunican entre
ellos a través de una base de datos local compartida
(`logs/barrera.db`), no directamente unos con otros.

> Para la especificación completa del proyecto real (Aeropuerto de Gran
> Canaria), ver `manuales/SGbarrera_1.2v.pdf` y el plan paso a paso en
> [PLAN_INSTALACION.md](PLAN_INSTALACION.md).

## Por qué SQLite en vez de MQTT local para comunicar los servicios

El documento sugiere MQTT local como una opción para `receptor-lpr`. Se
descartó a favor de una base SQLite compartida (`logs/barrera.db`) porque
consigue lo mismo (servicios totalmente desacoplados, ninguno sabe que
existen los demás) sin añadir una pieza más de infraestructura que
instalar, asegurar y vigilar (un broker Mosquitto local) — algo que pesa
especialmente mientras se prueba en un PC normal y no en la Raspberry Pi
final. El MQTT "de verdad" (broker en la nube, para la apertura remota
desde la app) sigue siendo su propio servicio aparte, tal cual pide la
especificación.

## Los 7 servicios

| Servicio | Fichero | Qué hace | Habla con |
|---|---|---|---|
| sync-autorizados | `scripts/servicio_sync_autorizados.py` | GET `/autorizados` cada 15 min, y al momento si hay un comando `sync_ahora` en la BD | VPS, `config/autorizados_cache.json` |
| lector-rfid | `scripts/servicio_lector_rfid.py` | Lee el hardware RFID, deja la lectura en crudo en la BD | Lector RFID, BD |
| receptor-lpr | `scripts/servicio_receptor_lpr.py` | Recibe el POST de la cámara (puerto 8080, ruta `/lpr`), deja la lectura en crudo en la BD | Cámara LPR, BD |
| decision | `scripts/servicio_decision.py` | Único que compara contra la lista y dispara el relé para lecturas automáticas | BD, `reglas.py`, `rele_dt_r002.py` |
| eventos | `scripts/servicio_eventos.py` | Único que habla POST `/eventos`; reintenta hasta 200/201 | BD, VPS |
| mqtt-cliente | `scripts/servicio_mqtt_cliente.py` | Apertura/cierre remotos desde la app, publica estado | Broker MQTT del VPS, relé |
| webhook-sgmas (opcional) | `scripts/servicio_webhook_sgmas.py` | Recibe aviso de cambio de lista, verifica firma, pide sync inmediata | HTTPS entrante, BD |

Módulos compartidos (no son servicios, los importan los de arriba):

- `scripts/almacen.py` — el buzón común (SQLite: `lecturas`, `eventos_pendientes`, `comandos`, `estado`).
- `scripts/vps_cliente.py` — las llamadas HTTP puras a la API (GET/POST), sin caché ni lógica.
- `scripts/reglas.py` — normalizar, buscar por matrícula/RFID en la caché, evaluar PERMITIDO/DENEGADO.
- `scripts/rele_dt_r002.py` — cliente del relé de red (confirmado contra el dispositivo real).
- `scripts/rfid_reader.py`, `scripts/logger.py` — igual que antes.

## Relé Dingtian DT-R002 (confirmado)

`scripts/rele_dt_r002.py` habla con el relé real en `192.168.10.10` via su
`relay_cgi.cgi`. `abrir_pulso()` está **confirmado contra el hardware
real, con clic audible** (pulso de 1s, contacto R1). `mantener_abierto()`
y `cerrar()` usan la misma construcción de URL ya corregida (ver aviso
abajo) pero el efecto en si no está confirmado con el LED — pruébalas
antes de fiarte de ellas en producción.

> **Aviso de un fallo ya corregido:** el CGI del DT-R002 es sensible al
> **orden** de los parámetros y necesita el `&` final — `pwd` tiene que
> ir en último lugar. La primera versión de `rele_dt_r002.py` montaba la
> URL con un `dict` pasado a `requests.get(params=...)`, que colocaba
> `time` después de `pwd` y no añadía el `&` final; el dispositivo
> respondía HTTP 200 pero el relé **no se activaba de verdad** (se notó
> porque la respuesta cruda cambiaba de `&0&1&0&1&10&` a `&-5&1&0&1&10&`
> — el primer campo señala ese fallo). Ahora la URL se construye a mano,
> en el mismo orden exacto que el `curl` ya confirmado. Moraleja para
> cualquier CGI de estos dispositivos sin documentación oficial: no te
> fíes de que un 200 OK signifique que el efecto físico ocurrió —
> compara la respuesta cruda contra una llamada ya confirmada a mano, y
> si puedes, verifícalo físicamente (LED, sonido del relé).

Credenciales en `config/rele_config.json` (contiene secretos, no lo
compartas):

```json
{
  "host": "192.168.10.10",
  "relay_index": 0,
  "password": "....",
  "session_id": ".......",
  "jogging_time_100ms": 10
}
```

**Importante:** desde que existe `servicio_decision.py`, cada matrícula o
RFID autorizado dispara un pulso **real** a este relé, aunque la barrera
todavía no esté cableada al contacto R1. Es inofensivo sin nada conectado,
pero ya no es una simulación — tenlo en cuenta al hacer pruebas.

## Cómo ejecutarlo (banco de pruebas, sin la barrera aún)

Cada servicio es un proceso aparte. Para probar el flujo automático
(matrícula/RFID → decisión → relé → evento), necesitas 4 terminales:

```bash
python scripts/servicio_sync_autorizados.py
python scripts/servicio_receptor_lpr.py
python scripts/servicio_decision.py
python scripts/servicio_eventos.py
```

Y, si tienes el lector conectado, un quinto:
```bash
python scripts/servicio_lector_rfid.py
```

`servicio_mqtt_cliente.py` ya se puede probar en local con un Mosquitto
propio (ver "Probar MQTT en local" más abajo). `servicio_webhook_sgmas.py`
sigue sin poder probarse (necesita el secreto HMAC real y la URL pública
del VPS).

### Probar sin cámara ni lector a mano

Simula una matrícula:
```bash
curl -X POST http://localhost:8080/lpr -H "Content-Type: application/json" -d "{\"license_plate\": \"TESTOK1\"}"
```

Simula un RFID (sin necesidad del hardware ni de `servicio_lector_rfid.py`):
```bash
python -c "import sys; sys.path.insert(0,'scripts'); import almacen; almacen.insertar_lectura('RFID', 'TESTRFID000001')"
```

Verifica el resultado en tres sitios: la consola de `servicio_decision.py`
(imprime `PERMITIDO`/`DENEGADO`), `logs/registro_accesos.csv` (nueva
línea), y la consola de `servicio_eventos.py` (confirma el envío al VPS).

### Probar MQTT en local

Mosquitto ya está instalado (`winget install EclipseFoundation.Mosquitto`)
y corriendo como **servicio de Windows** en segundo plano (`sc query
mosquitto` → `RUNNING`), escuchando en el puerto 1883 con su
configuración por defecto — no hace falta arrancar nada, ya está ahí.
(Para pararlo: `sc stop mosquitto`; para volver a arrancarlo: `sc start
mosquitto`.)

1. Edita `config/mqtt_config.json` **temporalmente**:
   ```json
   {
     "host": "localhost",
     "port": 1883,
     "tls": false,
     "cafile": "",
     "usuario": "prueba",
     "password": "prueba",
     "id_barrera": "AEROPUERTO_ACCESO_1"
   }
   ```
2. En otra terminal, arranca el servicio real de la Pi:
   ```bash
   python scripts/servicio_mqtt_cliente.py
   ```
3. En una tercera terminal, simula la app publicando la orden de
   apertura (mismo topic y payload que usará la app real):
   ```bash
   python scripts/simulador_app.py
   ```

Deberías ver: `servicio_mqtt_cliente.py` recibe la orden y dispara el
relé real (pulso audible, **confirmado**: respuesta `&0&1&0&1&10&`);
`simulador_app.py` recibe de vuelta el `ack` (`{"resultado": "ok",
...}`) y, poco después, el `estado` retenido. Cuando termines, devuelve
`config/mqtt_config.json` a los valores reales (`tls: true`, host/cafile/
credenciales del VPS) — seguirá fallando al conectar hasta que SGMAS
mande esos datos, y es lo esperado.

*(El `usuario`/`password` de prueba de arriba no se han verificado contra
la configuración por defecto del servicio — si el servicio la rechaza,
prueba a lanzar tu propio Mosquitto con `allow_anonymous true` en otro
puerto, ya que el 1883 lo ocupa el servicio.)*

**Solo `accion: "abrir"` está implementada.** `mantener_abierta` y
`cerrar` (`simulador_app.py mantener_abierta` / `cerrar`) responden
con un `ack` de error (`"resultado": "error"`) en vez de tocar el relé:
al probarlas, el CGI del DT-R002 devolvía `&-5&...` (el mismo patrón que
antes indicaba fallo en el pulso) en cuanto se omitía el parámetro
`time` — todo apunta a que el dispositivo lo exige siempre, así que la
suposición de "sin `time` se queda fijo" no vale, y de momento no hacen
falta esos dos modos. Si en algún momento se necesitan, hay que
investigar el parámetro correcto con Dingtian o su documentación antes
de volver a activarlos en `servicio_mqtt_cliente.py`.

## Desplegar como servicios (systemd, Linux/Ubuntu)

Hasta ahora cada servicio se lanzaba a mano en una terminal para probar.
En el miniPC real (Ubuntu Server minimizado) van como unidades `systemd`:
arrancan solas al encender, y si un proceso se cae, `systemd` lo reinicia
solo (`Restart=on-failure`).

### 1. Entorno virtual

Ubuntu bloquea `pip install` a nivel de sistema (PEP 668). Hay que crear
un entorno virtual dentro del proyecto:
```bash
cd SGbarrera
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Generar e instalar las unidades

```bash
chmod +x deploy/instalar_servicios.sh
./deploy/instalar_servicios.sh
```

Esto crea las 7 unidades (`sgbarrera-sync-autorizados`,
`sgbarrera-lector-rfid`, `sgbarrera-receptor-lpr`, `sgbarrera-decision`,
`sgbarrera-eventos`, `sgbarrera-mqtt-cliente`, `sgbarrera-webhook-sgmas`)
en `/etc/systemd/system/` y hace `systemctl daemon-reload`. **No arranca
ninguna todavía** — al final te imprime el comando exacto de cada una:

```bash
sudo systemctl enable --now sgbarrera-decision.service
```
... y así con cada servicio.

Para ver que está viva y sus logs en vivo:
```bash
sudo systemctl status sgbarrera-decision.service
journalctl -u sgbarrera-decision.service -f
```

Las unidades ya llevan `Environment=PYTHONUNBUFFERED=1` — sin eso, Python
almacena la salida en bloques cuando no hay una terminal real detrás (como
pasa con `journal`), y los `print()` de los scripts tardan en aparecer, o
solo se ven al pararse el proceso. Si alguna vez lanzas un script suelto
a mano (fuera de systemd) y quieres ver sus logs en vivo, arráncalo igual
con `PYTHONUNBUFFERED=1 python scripts/nombre.py` (o `python -u
scripts/nombre.py`).

### 3. `lector-rfid`: revisar antes de arrancarlo

`rfid_reader.py` tiene tres modos, en `config/rfid_config.json`:

- `"modo": "stdin"`: lee por entrada estándar, para un lector que emula
  teclado. **Funciona en una terminal a mano, pero NO como servicio**
  — un servicio no tiene ninguna ventana con foco a la que un lector
  que emula teclado pueda "escribirle".
- `"modo": "evdev"`: como "stdin" pero para producción en Linux como
  servicio — lee el dispositivo de entrada directamente
  (`/dev/input/eventX`), sin depender de ninguna ventana. Para usarlo:
  1. Conecta el lector y averigua su ruta: `ls /dev/input/by-id/`
     (busca uno que acabe en `-event-kbd`).
  2. Ponla en `"dispositivo_evdev"` en `config/rfid_config.json`.
  3. Añade el usuario del servicio al grupo `input`:
     `sudo usermod -aG input <tu_usuario>` (hace falta cerrar sesión y
     volver a entrar para que se aplique).
  4. Cambia `"modo"` a `"evdev"` en `config/rfid_config.json`.
- `"modo": "serial"` (el que usa ya el lector UHF real, R16-9DB): este
  lector NO emula teclado. Hay que configurarlo **una vez**, desde el
  software DEMO del fabricante (`manuales/lector rfid uhf R16-9DB/`),
  en modo **"Active reading"** con la salida por **"USB virtual serial
  port"** (pestaña "Serial port function"). Así configurado, el propio
  lector manda cada lectura como texto por ese puerto COM, una línea
  por lectura — `rfid_reader.py` solo abre el puerto y lee líneas, sin
  hablar ningún protocolo con el lector. Para usarlo:
  1. Con el lector conectado por USB, mira en el Administrador de
     dispositivos de Windows (o `ls /dev/serial/by-id/` en Linux) qué
     puerto le ha tocado (ahora mismo: `COM4`) y ponlo en
     `"puerto_serie"` en `config/rfid_config.json`. El baudrate
     (`"baudios"`, 9600 de fábrica) debe coincidir con el que muestre
     la pestaña "Serial port function" del software DEMO.

     **En Linux, usa la ruta de `/dev/serial/by-id/` (no `/dev/ttyUSB0`
     directamente):** `/dev/ttyUSB0` puede cambiar de número entre
     reinicios o si hay otro adaptador USB-serie conectado (el router
     4G, por ejemplo); la ruta de `by-id` (algo como
     `/dev/serial/by-id/usb-<fabricante>_<modelo>_<numero_serie>-if00-port0`)
     es estable y siempre apunta al mismo lector.
  2. **Linux: añade el usuario del servicio al grupo `dialout`** —
     `/dev/ttyUSB0` (o el que sea) pertenece a `root:dialout`, así que
     sin esto `pyserial` falla con `PermissionError: [Errno 13]
     Permission denied`, igual que pasa con el grupo `input` en modo
     `"evdev"` (arriba). `sudo usermod -aG dialout <tu_usuario>` (hace
     falta cerrar sesión y volver a entrar, o `newgrp dialout` en la
     misma sesión, para que se aplique). Si vas a arrancarlo como
     servicio systemd (`deploy/instalar_servicios.sh`), es el usuario
     que ejecuta el servicio (`whoami` en el momento de instalar) quien
     necesita estar en ese grupo, no solo tu usuario de sesión.
  3. **Windows: cierra el software DEMO del fabricante** antes de
     probar `rfid_reader.py` — solo un proceso puede tener el puerto
     COM abierto a la vez; si el DEMO lo tiene abierto, `pyserial`
     falla con `PermissionError: Acceso denegado`. (En Linux no aplica,
     salvo que tengas otro proceso tuyo con el puerto ya abierto.)
  4. Prueba solo el hardware, sin tocar el resto del sistema:
     ```bash
     python scripts/rfid_reader.py
     ```
     Pasa un par de tags conocidos — cada uno debe imprimir una línea.

     Si alguna vez ves una lectura corta y rara (p.ej. `977` en vez de un
     decimal de 10 dígitos) justo al arrancar el script o justo después
     de otra lectura, no es un tag nuevo: es la trama de la antena
     desincronizada (se empezó a leer a mitad de una trama en curso, por
     ejemplo por la cola de bytes que ya hubiera en el puerto al abrirlo).
     `_leer_serial()` ya lo descarta solo (comprueba que la trama empiece
     en STX y acabe en ETX; si no, no devuelve nada) — si ves que se cuela
     igual, dilo.
  4. **Formato — conseguido: el lector manda el EPC completo (24
     caracteres hex).** Al principio, intentar cambiar el lector a salida
     hexadecimal completa desde el software DEMO del fabricante no
     funcionaba: confirmado con capturas de tráfico USB (Wireshark +
     USBPcap) que en la mayoría de los casos ni siquiera llegaba a mandar
     el comando de "Set" al lector (ver historial de `manuales/lector
     rfid uhf R16-9DB/USBpcap capturas/` si hace falta revisar el
     detalle; el "SDK" que mandó soporte técnico tampoco sirvió, era otra
     copia del mismo software). Se resolvió más adelante cambiando el
     ajuste de lectura de la antena para que devuelva "todos los bits
     posibles" — confirmado funcionando en el entorno real (2026-09-30).
     **Importante: comprueba que ese ajuste se haya guardado de forma
     permanente en el lector** (algunos de estos módulos tienen un paso
     de "Save"/"Write to flash" aparte de simplemente aplicarlo) — si se
     pierde con un corte de corriente o un reinicio del propio lector, no
     pasa nada grave (sigue el punto siguiente), pero mejor saberlo ya.

     `reglas.py` sabe interpretar **dos formatos**: si la lectura ya es
     un EPC completo (24 hex, `es_epc_completo()`), se usa tal cual --
     identificador exacto, sin huecos ni huella, autorizado o no. Si no,
     asume el decimal de fábrica ("10 no. in D (four byte)") y resuelve
     por huella de 4 bytes: con 5 pares reales (lectura del lector ↔ EPC
     real en el servidor) se confirmó que ese decimal son exactamente los
     **últimos 4 bytes** del EPC (96 bits), en big-endian, y
     `buscar_por_rfid()` compara ese "trozo" contra la cola de cada
     `rfid` de la lista de autorizados. Ver el comentario en
     `scripts/reglas.py` para el detalle de la fórmula.

     **Filtro de tags ajenos (regla estricta, a propósito):** la antena
     de largo alcance (4-6m) también capta tags reales de otras cosas que
     pasen cerca (otros vehículos, objetos con su propio RFID) — no son
     basura, son identificadores válidos, solo que no son nuestros.
     `servicio_lector_rfid.py` descarta en el origen, antes de tocar el
     almacén compartido, todo lo que no cumpla **las dos condiciones de
     `reglas.es_tag_propio()`: exactamente 24 caracteres Y empezar por
     `reglas.PREFIJO_EPC_PROPIO` (`"E2806894"`)**. Nada más pasa — ni
     EPC de otro fabricante/lote, ni lecturas de otra longitud, **ni
     siquiera el decimal truncado de fábrica** (se decidió a propósito no
     dejarlo pasar como respaldo, para mantener la regla simple; el
     soporte para ese formato sigue en `fingerprint_rfid()`/
     `buscar_por_rfid()` por si algún día hace falta reactivarlo, pero
     `servicio_lector_rfid.py` ya no deja que llegue tan lejos). Si el
     lector alguna vez vuelve al decimal de fábrica (p.ej. el ajuste de
     "todos los bits" no se guardó de forma permanente), sus lecturas se
     descartarán en el origen igual que un tag ajeno, en vez de
     resolverse por huella.

     Esta traducción también se ve en pantalla, no solo en la decisión
     final: tanto `python scripts/rfid_reader.py` (prueba de hardware,
     que enseña TODO lo que llega, sin el filtro de tags ajenos, para
     poder depurar) como el log de `sgbarrera-lector-rfid`
     (`servicio_lector_rfid.py`) imprimen, además del valor crudo, el
     resultado de `reglas.describir_rfid()` — el EPC completo (`E280...`)
     si el tag está en `config/autorizados_cache.json`, o si no, `sin
     registrar (huella XXXXXXXX)`. Si un tag que sabes que está dado de
     alta sale como "sin registrar", no es un fallo de esta traducción:
     significa que ese EPC concreto no está (todavía) en la caché local
     — revisa si `sgbarrera-sync-autorizados` ha corrido y si el backend
     ya lo tiene en `/autorizados` (`servicio_sync_autorizados.py`).

En los tres modos, `leer_rfid_bloqueante()` mantiene siempre la misma
firma (sin argumentos, devuelve texto o `None`), así que
`servicio_lector_rfid.py` no cambia al cambiar de modo.

### 4. `mqtt-cliente` y `webhook-sgmas`: esperando credenciales

Si los arrancas ahora sin los datos reales de SGMAS, entrarán en un
bucle de reintento — inofensivo (`systemd` los reinicia con
`RestartSec=5`), pero llenará el log de errores de conexión hasta que
rellenes `config/mqtt_config.json` y `config/webhook_config.json` con
los valores reales.

### 5. Retención de logs (30 días)

Sin esto, los logs crecen sin límite. Se instala una vez en el miniPC:

```bash
chmod +x deploy/instalar_mantenimiento_logs.sh
./deploy/instalar_mantenimiento_logs.sh
```

Hace dos cosas:

- **Journal de systemd** (los `print()` de los 7 servicios): config nativa de
  `journald` en `/etc/systemd/journald.conf.d/sgbarrera.conf` — máximo 30
  días y máximo 200 MB (el tope de tamaño evita que un servicio en bucle de
  errores, como `mqtt-cliente` sin credenciales, llene el disco). No usa cron:
  journald se limpia solo.
- **Timer `sgbarrera-mantenimiento-logs`**, cada noche a las 02:00
  (`Persistent=true`: si el miniPC estaba apagado, corre al arrancar), que
  ejecuta `scripts/mantenimiento_logs.py`:
  - `registro_accesos.csv`: lo de más de 30 días se comprime a
    `logs/archivo/registro_accesos_AAAA-MM.csv.gz` (un fichero por mes) y esos
    archivos se borran a los 3 meses.
  - `barrera.db`: borra lecturas y comandos ya procesados de más de 30 días y
    compacta el fichero. No toca `eventos_pendientes` (cola de envío al VPS).

Los valores (`DIAS_VIVOS`, `MESES_ARCHIVO`) están arriba de
`mantenimiento_logs.py`. Para ver qué borraría sin tocar nada:
`python scripts/mantenimiento_logs.py --dry-run` (empieza diciendo cuántos datos
hay y desde cuándo; con `--dry-run --dias 7` simula otra retención sin borrar
nada, útil para comprobar la poda con datos recientes).

## Lo que falta por confirmar/probar (webhook-sgmas)

Este servicio está programado por completo, pero no se ha podido
verificar de punta a punta porque depende de datos que todavía no ha
mandado SGMAS (ver `PLAN_INSTALACION.md`, "Bloqueantes externos"):

- **mqtt-cliente**: ya probado en local (ver arriba). Para producción
  falta confirmar el host del broker real (`taxi.sgmas.es` vs
  `mqtt.taxi.sgmas.es`), conseguir `mosquitto-ca.crt` y la password real
  de `pi_aeropuerto`.
- **webhook-sgmas** (`config/webhook_config.json`): falta el
  `WebhookSecretoHmac` real y la URL pública en el VPS que reenvía el
  aviso hasta aquí por el túnel VPN.
- Pendiente de confirmar con SGMAS si las aperturas manuales por MQTT
  deben generar también un evento en `/eventos` — por ahora
  `servicio_mqtt_cliente.py` **no** lo hace (solo publica el `ack`),
  siguiendo la nota del propio documento.

## Formato de la cámara (confirmado)

La cámara Milesight MS-C2972-RFLPE manda el reconocimiento como JSON
directo, con la matrícula en el campo **`license_plate`**:

```json
{"event_type": "Regular", "license_plate": "9421GMT", "plate_confidence": "0.84", "...": "..."}
```

## Configurar el acceso al VPS

`config/api_config.json` (contiene un secreto, no lo compartas):

```json
{
  "base_url": "https://taxi.sgmas.es/api/barreras/v1",
  "id_barrera": "AEROPUERTO_ACCESO_1",
  "token": "..."
}
```

### Motivos de denegación

`reglas.evaluar()` decide así:

| Situación | Motivo enviado |
|---|---|
| Matrícula/RFID no aparece en `/autorizados` | `NO_AUTORIZADO` |
| Aparece pero `activo: false` | `CADUCADO` |
| Aparece activo pero fuera de `[valido_desde, valido_hasta]` | `FUERA_HORARIO` |
| — | `LICENCIA_DESCONOCIDA` no se genera: no hay forma de distinguirlo localmente con los campos actuales; pedir a SGMAS un campo adicional si hace falta. |

## Estructura del proyecto

```
SGbarrera/
├── README.md
├── PLAN_INSTALACION.md
├── requirements.txt
├── config/
│   ├── api_config.json           <- credenciales del VPS (secreto)
│   ├── rele_config.json          <- credenciales del rele DT-R002 (secreto)
│   ├── mqtt_config.json          <- credenciales del broker MQTT (pendiente, secreto)
│   ├── webhook_config.json       <- secreto HMAC del webhook (pendiente, secreto)
│   ├── rfid_config.json          <- modo del lector RFID ("stdin", "evdev" o "serial") y puerto/baudrate
│   └── autorizados_cache.json    <- cache de /autorizados (se genera solo)
├── manuales/
├── deploy/
│   ├── instalar_servicios.sh     <- genera y registra las unidades systemd
│   └── instalar_mantenimiento_logs.sh  <- limita journald + timer de poda a las 02:00
├── scripts/
│   ├── almacen.py                 <- BD compartida entre servicios
│   ├── vps_cliente.py             <- llamadas HTTP puras a la API
│   ├── reglas.py                  <- normalizar/buscar/evaluar
│   ├── rele_dt_r002.py            <- cliente del rele (confirmado)
│   ├── rfid_reader.py
│   ├── logger.py
│   ├── mantenimiento_logs.py      <- poda nocturna de CSV y BD (retencion 30 dias)
│   ├── servicio_sync_autorizados.py
│   ├── servicio_lector_rfid.py
│   ├── servicio_receptor_lpr.py
│   ├── servicio_decision.py
│   ├── servicio_eventos.py
│   ├── servicio_mqtt_cliente.py   <- probado en local (accion "abrir"); host real pendiente de SGMAS
│   ├── servicio_webhook_sgmas.py  <- completo, sin probar (pendiente de credenciales)
│   └── simulador_app.py           <- simula la app publicando una orden por MQTT
└── logs/
    ├── registro_accesos.csv       <- log local, ultimos 30 dias (se genera solo)
    ├── archivo/                   <- meses anteriores comprimidos (.csv.gz)
    └── barrera.db                  <- buzon compartido entre servicios (se genera solo)
```

## Qué falta (ver PLAN_INSTALACION.md para el detalle)

- Cablear la barrera real al contacto R1/COM del DT-R002.
- Confirmar `mantener_abierto()`/`cerrar()` con un LED antes de usarlas en producción.
- Lector UHF: el emparejamiento decimal↔EPC (ver "3. `lector-rfid`" arriba) está confirmado con 5 tags reales, pero convendría validarlo con más tags de la flota si es posible (por ahora asume que los últimos 4 bytes del EPC no se repiten entre tags, lo cual es muy probable pero no está 100% verificado a gran escala). Pendiente también pasar de alimentación USB de banco a 12V/2A + montaje definitivo.
- Los datos pendientes de SGMAS para `mqtt-cliente` y `webhook-sgmas`.
- Convertir cada servicio en su propia unidad systemd en la Raspberry Pi real.
