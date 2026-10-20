# Plan de instalación — Barrera de acceso Taxis, Aeropuerto de Gran Canaria

Basado en `manuales/SGbarrera_1.2v.pdf` (Especificación técnica v1.2, SGMAS
SOFTWARE SLU). Este documento convierte esa especificación (organizada por
tema) en un **orden cronológico de trabajo**: qué hacer primero, cómo probar
cada pieza por separado antes de unirla a las demás, y qué depende de qué.

Idea general: **de lo más seguro/barato de probar a lo más arriesgado**.
Todo lo que se pueda verificar en una mesa, sin ir al aeropuerto y sin tocar
la barrera real, se hace primero. Lo último que se toca es la central de la
barrera real y la alimentación de 230V.

## Antes de nada: qué cambia respecto al codigo que ya teniamos en `scripts/`

El proyecto que veniamos montando (camara Milesight + RFID + rele por GPIO +
radar PB-RADAR-BT) era un prototipo de aprendizaje. Este documento de SGMAS
es la **especificacion oficial** del despliegue real del aeropuerto, y en
varias cosas es mas concreta o distinta:

| Pieza | Lo que teniamos | Lo que pide el documento oficial |
|---|---|---|
| Rele de apertura | Modulo de rele por GPIO de la Pi (`barrier_control.py`) | **Dingtian DT-R002**, rele de red por HTTP. La Pi no se conecta electricamente a la barrera en ningun punto — mucho mas seguro para la Pi. Hay que reescribir `barrier_control.py` para que hable HTTP con el DT-R002 en vez de GPIO. |
| Terminales de la barrera | `UP`/`COM` confirmados en el manual de la PB-PARKBARRIER-ULTRA707 | Sigue siendo valido: el documento pide identificar "entrada de apertura por pulso" + "COM" en la central real (seccion 5.1) — es el mismo concepto, el `UP`/`COM` que ya documentamos. Lo unico que cambia es que ahora quien cierra ese contacto es el rele DT-R002 (mandado por red), no un rele soldado a la Pi. |
| RFID | Lector generico que emula teclado USB (`rfid_reader.py`) | **Lector UHF de largo alcance (R16-9DB o equivalente)**, lee el tag a 4-6m sin que el coche pare. Interfaz real confirmada: puerto serie virtual por USB (no RS485/Wiegand/TCP), leido con `rfid_reader.py` modo `"serial"` (ver README.md). |
| Camara LPR | Webhook HTTP a `puerto 8000, ruta /lpr-event` | Webhook HTTP a `puerto 8080, ruta /lpr` — mismo mecanismo, solo cambia puerto/ruta. Facil de ajustar en `lpr_webhook_server.py`. |
| Listas de autorizados | `sync_listas.py` generico contra una URL de ejemplo | **API REST cerrada** (`GET .../autorizados`, `POST .../eventos`) con token Bearer, formato de campos fijado, reintentos, UUID por evento. Hay que reescribir `sync_listas.py` y `logger.py` (el log pasa a ser tambien una cola de envio a la API, no solo un CSV). |
| Apertura remota | No existia | **Nuevo**: cliente MQTT (TLS 8883) que escucha ordenes de abrir/mantener/cerrar desde la app de SGMAS y publica estado. Hay que crear `mqtt_cliente.py` desde cero. |
| Radar PB-RADAR-BT | Sensor de seguridad anti-caida + trigger opcional a GPIO | **No aparece en este documento.** Puede que esta instalacion del aeropuerto no lo lleve (la central de la barrera real puede tener ya su propia fotocelula/bucle), o que se te haya olvidado incluirlo al escribir la especificacion. **Confirmalo** antes de descartar `presence_sensor.py` — si el radar SI va en esta instalacion, se cablea igual que ya documentamos (a la entrada de fotocelulas de la central), en paralelo al DT-R002. |
| Config dispersa | Constantes sueltas en cada script | El documento pide un unico `/etc/barrera/config.yaml` (seccion 8.3) con todos los parametros. Merece la pena adoptar ese formato ya. |

**Nada de lo anterior es una perdida de trabajo**: el diseño de fondo (Pi
decide en local, cachea listas, compara matricula O RFID, todo con log/cola
de eventos, cierre automatico de la barrera) es el mismo que ya teniamos.
Cambian las piezas concretas, no la arquitectura.

## Bloqueantes externos — pedirlos ya, en paralelo a todo lo demas

Estos datos los tiene que dar "SGMAS" (backend/dirección técnica) y no
dependen de tu trabajo de instalación, así que conviene pedirlos **desde
hoy** para que no frenen las fases finales (sección 14 del documento):

- [ ] Confirmar host MQTT exacto: `taxi.sgmas.es:8883` o `mqtt.taxi.sgmas.es:8883`.
- [ ] `MQTT_SETUP.md` (mencionado como "aún no recibido").
- [ ] Confirmar si la Pi debe enviar evento `MANUAL_APP` por la API para
      órdenes recibidas por MQTT, o si basta con el `ack`.
- [ ] Token Bearer de esta Raspberry, contraseña MQTT de `pi_aeropuerto` y
      `WebhookSecretoHmac` (por Bitwarden Send, nunca en texto plano).
- [ ] Alta de la IP pública del VPS en `Ar_Barreras.ip_permitidas`.
- [ ] Fichero `.ovpn` (o claves WireGuard) para el router 4G.
- [x] Interfaz exacta del lector RFID: confirmada — puerto serie
      virtual por USB, no RS485/Wiegand/TCP (ver README.md).
- [x] Formato del RFID: no hizo falta que SGMAS confirme nada. Resuelto
      en dos capas: primero por ingeniería inversa en el lado de la Pi (5
      pares reales lectura↔EPC, sin tocar el lector ni el backend — sigue
      de red de seguridad), y desde el 2026-09-30 el lector ya manda el
      EPC completo de verdad (ajuste de la antena a "todos los bits"),
      confirmado en el entorno real. `reglas.py` reconoce los dos
      formatos a la vez. Ver `scripts/reglas.py` (`buscar_por_rfid`,
      `es_epc_completo`) y README.md ("Formato del RFID").
- [x] Filtro de tags ajenos: la antena de largo alcance también capta
      tags reales de otras cosas cercanas — se descartan en el origen por
      prefijo de empresa (`reglas.es_tag_propio`/`PREFIJO_EPC_PROPIO`),
      antes de llegar al almacén compartido. Ver README.md.
- [ ] Si se quiere webhook de cambios: URL pública en el VPS
      (`Ar_BarreraConfig.WebhookUrlPi`).

## Fase 0 — Preparación (mesa, sin hardware conectado)

- [ ] Repasar la lista de material (secciones 3.1-3.5 del PDF) y confirmar
      qué falta pedir.
- [ ] Decidir y anotar: ¿quién construye el software de la Pi? El documento
      da por hecho que lo hace "SGMAS" y que tú solo instalas — pero por
      cómo hemos ido trabajando, pareces estar construyéndolo tú misma.
      Conviene dejarlo claro para no duplicar trabajo con otra persona del
      equipo.
- [ ] Reservar un rango de red de pruebas en casa/oficina distinto al
      `192.168.10.0/24` del aeropuerto, para no mezclar configuraciones.

## Fase 1 — Banco de pruebas, componente a componente (sin ir al aeropuerto)

Cada punto se puede verificar solo, antes de pasar al siguiente.

**Raspberry Pi (base del sistema)**
- [ ] Raspberry Pi OS Lite 64 bits, usuario propio (no `pi`), SSH solo con
      clave pública.
- [ ] NTP activo, hora en UTC.
- [ ] `dtparam=watchdog=on` (watchdog hardware).
- [ ] Verificación: `ssh` funciona con clave, `timedatectl` muestra hora
      sincronizada.

**Relé DT-R002 (sin la barrera real todavía)**
- [ ] Acceso inicial en `192.168.1.100` (admin/admin), cambiar contraseña.
- [ ] Aplicar los ajustes de la sección 7 del PDF (IP fija, Wi-Fi
      desactivado, `Power Failure Recovery Relay = No`, protocolos
      desactivados salvo HTTP, `Relay Password` y `Session ID` propios —
      anótalos).
- [ ] Verificación sin barrera: conecta un LED + resistencia (o un
      multímetro en continuidad) a los bornes `R1 NO`/`R1 COM` y pulsa
      "Relay Test" desde la web del DT-R002 — el LED debe encender el
      tiempo del `Jogging Time` configurado.
- [ ] Repetir la misma prueba con `curl` desde la Raspberry Pi (comando de
      la sección 7). Si funciona, ya tienes la ruta Pi → DT-R002 probada
      sin haber tocado ninguna barrera real.
- [ ] Código: adaptar `barrier_control.py` para que `abrir_barrera()` haga
      esa llamada HTTP al DT-R002 en vez de activar un GPIO.

**Lector RFID UHF**
- [ ] Montarlo en un soporte provisional, alimentación 12V/2A (de
      momento probado solo por USB en el banco, sin montar).
- [x] Conectarlo al PC/Pi: interfaz USB (puerto serie virtual), no
      RS485/Wiegand/TCP — ahora mismo en `COM4` (ver README.md).
- [ ] Modo lectura automática, filtro anti-repetición 2-3s (pestaña
      "Prefix suffix Data Input" del software DEMO: "search card
      interval" / "card filter number").
- [x] Verificación (sección 9.3 del PDF): pasado con 5 tags conocidos —
      el decimal que imprime `python scripts/rfid_reader.py` coincide,
      en los 5 casos, con los últimos 4 bytes del `rfid` real de
      `config/autorizados_cache.json` (ver README.md). No coincide con
      el EPC completo -- no hacía falta, ver punto anterior. Desde ahora
      `rfid_reader.py` ya no hace falta comprobarlo a mano: imprime el
      EPC completo (`E280...`) resuelto junto al decimal, vía
      `reglas.describir_rfid()` (ver README.md).
- [x] Código: `rfid_reader.py` tiene ya el modo `"serial"` para esta
      interfaz real (misma función `leer_rfid_bloqueante()` hacia
      afuera, el resto del sistema no cambia).

**Cámara LPR**
- [ ] Configurar el HTTP POST de la cámara hacia
      `http://<IP_PI>:8080/lpr` (nota: puerto y ruta distintos a los que
      usamos antes, 8000/lpr-event — hay que actualizar
      `lpr_webhook_server.py`).
- [ ] Verificación: reutilizar el mismo método que ya usamos — dejar el
      servidor escuchando, pasar una matrícula de prueba, y revisar en
      consola si hace falta ajustar `CAMPOS_MATRICULA_POSIBLES` al formato
      real del payload.

**Verificación de cierre de Fase 1:** en tu mesa, sin ir al aeropuerto,
puedes: (a) leer un tag y ver su decimal, (b) presentar una matrícula a la
cámara y verla reconocida, (c) hacer que la Pi cierre el relé del DT-R002 y
verlo con un LED. Las tres cosas por separado, todavía sin la barrera real.

## Fase 2 — Backend: API y MQTT (en paralelo a la Fase 1)

- [ ] Con Postman/curl y un token de pruebas: `GET
      .../AEROPUERTO_ACCESO_1/autorizados` y confirmar que la respuesta
      trae los campos de la tabla de la sección 8.5 (`matricula`, `rfid`,
      `activo`, `valido_desde/hasta`, etc.).
- [ ] Probar `POST .../eventos` con un evento de prueba y confirmar
      `201`/`200` y el formato de error RFC 7807 ante un dato incorrecto.
- [ ] En cuanto se confirme el host MQTT (bloqueante externo): probar la
      conexión TLS 8883 con un cliente de escritorio (p. ej. MQTT
      Explorer) antes de programarla en la Pi — suscribirse a
      `barreras/AEROPUERTO_ACCESO_1/estado` y publicar en `.../cmd`
      manualmente para ver la forma de los mensajes.
- [ ] Código: escribir `api_cliente.py` (GET autorizados con caché en
      disco + cola de eventos con reintento e idempotencia por
      `id_evento`) y `mqtt_cliente.py` (suscripción a `cmd`, publicación
      de `ack` y `estado` con retain y Last Will), y un `config.yaml`
      único que sustituya a las constantes sueltas de los scripts
      actuales.

## Fase 3 — Integración completa en el banco de pruebas

- [ ] Todo junto en la Pi, con `config.yaml` relleno con valores de
      prueba, como servicios `systemd` con reinicio automático.
- [ ] Un tag y una matrícula de prueba dados de alta en el backend real:
      verificar el flujo completo tag/matrícula → decisión → relé (LED) →
      evento visible en la aplicación.
- [ ] Simular corte de red (desenchufar el cable de pruebas): confirmar
      que sigue autorizando con la última lista cacheada y que los
      eventos pendientes se encolan y se envían al reconectar.
- [ ] Probar apertura remota por MQTT desde un cliente de prueba y
      confirmar que llega el `ack`.
- [ ] Probar reinicio de la Pi (corte de alimentación simulado): debe
      levantar sola, con el relé en reposo.

**Verificación de cierre de Fase 3:** el sistema completo funciona de
principio a fin en la mesa, incluyendo fallos de red simulados. A partir de
aquí, lo que queda es específico del sitio físico.

## Fase 4 — En el cuadro real de la barrera (aeropuerto)

Aquí ya se trabaja con 230V y con una barrera en producción — ir con
calma y siempre cortando alimentación antes de manipular el cuadro.

- [ ] Seguridad primero: cortar la alimentación de 230V antes de abrir el
      cuadro. Verificar ausencia de tensión con un multímetro.
- [ ] Identificar en la central real de la barrera (sección 5.1): entrada
      de apertura por pulso, entrada de mantener abierta (si existe),
      borne común, salida de estado (si existe). Fotografiar todo (se
      necesita para la sección 13, la entrega a SGMAS).
- [ ] Confirmar si el radar PB-RADAR-BT forma parte de esta instalación
      concreta (no aparece en el documento oficial) — si sí, cablearlo a
      la entrada de fotocélulas/bucle de la central, en paralelo al
      DT-R002, tal y como ya documentamos en el `README.md`.
- [ ] Cablear alimentación 12V DC (carril DIN, fusible/magnetotérmico de
      entrada, regleta con fusible individual 1-3A por equipo — sección
      11), DT-R002, Pi (por convertidor 12→5V, nunca por el USB del
      router), lector y cámara.
- [ ] Puesta a tierra de la caja metálica y de la pantalla del cable
      RS485.
- [ ] Prueba mínima (sección 5.3): pulso desde la web del DT-R002 sobre
      la barrera real — debe abrir. Anotar el tiempo mínimo de pulso que
      acepta (probar 0,5s y 1s).
- [ ] Configurar el router 4G in situ (sección 6): SIM, cobertura (RSSI),
      direccionamiento LAN, cliente VPN, watchdog de ping, reinicio
      programado, gestión por SMS, cambiar contraseñas por defecto.
- [ ] Levantar el túnel VPN y comprobar `ping` desde la Pi a `10.8.0.1`.

## Fase 5 — Pruebas de aceptación (sistema completo, en el sitio real)

El propio documento ya trae un protocolo de 14 pruebas completo (sección
12) — no hace falta inventar uno nuevo, solo seguirlo en orden:

- [ ] 1. Pulso desde la web del DT-R002 → la barrera abre.
- [ ] 2. `curl` desde la Pi al DT-R002 → la barrera abre.
- [ ] 3. `ping` desde la Pi a `10.8.0.1` (VPS) → responde por el túnel.
- [ ] 4. `sync-autorizados` manual → descarga la lista y la guarda en SQLite.
- [ ] 5. Tag autorizado → abre; evento permitido visible en la app.
- [ ] 6. Tag no autorizado/caducado → no abre; evento denegado con motivo.
- [ ] 7. Matrícula autorizada ante la cámara → abre; evento permitido/lpr.
- [ ] 8. Apertura desde la app → abre; `ack` ok; evento `manual_app`.
- [ ] 9. Mantener abierta / cerrar desde la app → la barrera responde;
       estado refleja `rele_activo`.
- [ ] 10. Desconectar antena 4G y pasar tag autorizado → abre igual (modo
        local); eventos se envían al volver la conexión.
- [ ] 11. Corte de 230V 5 min (si hay SAI) → sistema sigue en línea.
- [ ] 12. Corte de 230V sin SAI y recuperación → todo arranca solo, relé
        en reposo, Pi reconecta a MQTT.
- [ ] 13. Reinicio programado del router → recupera el túnel en <3 min.
- [ ] 14. Mando de radio de respaldo → abre con la app y el 4G apagados.

## Fase 6 — Entrega y cierre

- [ ] Recopilar todo lo pedido en la sección 13 del PDF: fotos del
      cuadro/cableado/ubicación, marca y modelo real de la central,
      tiempo mínimo de pulso, credenciales e identificadores de router y
      DT-R002, datos del lector y de la cámara, y el resultado firmado
      de las 14 pruebas.
- [ ] Revisar que no quede ningún punto abierto de la sección 14
      ("Datos pendientes por parte de SGMAS").
- [ ] Actualizar `README.md` de este proyecto para que refleje la
      arquitectura final realmente instalada (quedará como referencia
      para el mantenimiento futuro).

## Próximo paso sugerido

Puedo empezar ya por la parte de código que no depende de ningún dato
pendiente: adaptar `barrier_control.py` al DT-R002 (HTTP), crear el
`config.yaml` único, y dejar `api_cliente.py`/`mqtt_cliente.py` con la
forma de los mensajes ya lista y solo el host/token como variables a
rellenar cuando lleguen. Dime si quieres que lo haga ahora o prefieres
primero cerrar los bloqueantes externos y el banco de pruebas de la Fase 1.
