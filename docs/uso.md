# LinkedOut: guía de uso en detalle

> El resumen está en el [README](../README.md). Acá, cómo funciona cada parte, la configuración y los errores comunes.

Sistema local de postulación asistida, análisis de ofertas laborales, adaptación personalizada de currículum vitae (formato Google XYZ) y redacción de mensajes de contacto para reclutadores, que usa el CLI de Antigravity (AGY) como motor de IA (sin claves comerciales externas ni servidores pesados).

Cuenta con un panel web responsivo para escritorio y celular, motor único de procesamiento en segundo plano, exportación ejecutiva a Word y PDF, túnel seguro para operar desde el celular y auditoría de calidad con detección de patrones de redacción artificial.

---

## 🧭 Vistas del Sistema (Selector Superior)

El panel cuenta con un selector en la barra superior para alternar entre dos vistas principales, impulsadas por el mismo motor:

1. **Manual** (reemplaza a la antigua vista de postulaciones):
   - **Nueva oferta:** cargás ofertas pegando texto, un enlace o varios enlaces a la vez (pestañas «Texto», «Link», «Varios links»).
   - Corre el mismo motor que el Radar (análisis → CV → mensaje → «Lista para enviar»).
   - En las ofertas manuales **siempre genera el CV** (sin umbral de afinidad).
   - Funciona incluso con el Radar detenido.
   - **Historial anterior:** sección colapsable al pie de la lista de solo lectura con las postulaciones viejas, permitiendo consultar y descargar sus archivos Word y PDF.

2. **LinkedIn**:
   - Lee publicaciones de empleo desde tu feed de inicio y mediante búsquedas globales en LinkedIn en segundo plano, usando Microsoft Edge con tu perfil ya abierto.
   - Clasifica los avisos, descarta lo que no corresponda a búsquedas laborales o no aplique a Argentina y analiza requisitos y afinidad.
   - Genera el CV de forma automática cuando la afinidad supera el umbral configurado (o a demanda).

### Ofertas con texto en imágenes

El Radar y las ofertas manuales por enlace conservan hasta cuatro imágenes de contenido de LinkedIn, incluso si el aviso no tiene texto. Si el texto es corto (menos de 400 caracteres), no tiene señales de puesto o requisitos, o la clasificación no reconoce el puesto, se intenta leer la imagen antes del análisis. La transcripción se guarda por separado y se reutiliza para generar el CV y el mensaje sin volver a analizar. Podés verla plegada en «Leímos el texto de la imagen» en el detalle.

La lectura local usa `rapidocr` y `onnxruntime` (incluidos en `requirements.txt`), con reconocimiento en español y descarga automática de modelos la primera vez. Cada imagen distinta debe alcanzar 40 caracteres alfanuméricos; no se suman entre imágenes ni cuentan espacios o puntuación. Si alguna falla o no alcanza ese umbral, se intenta un único respaldo de Antigravity con `effort='low'` por lectura del post, limitado a 45 segundos. Cada descarga tiene un límite de 10 segundos y 5 MiB por imagen (hasta 20 MiB por post); solo se aceptan imágenes HTTPS de contenido de `media.licdn.com`.

La lectura puede fallar o transcribir con errores: revisá el aviso antes de usarlo. Si falla, el procesamiento continúa con el texto original y no repite la lectura en reintentos automáticos de esa sesión. El botón **Analizar ahora** habilita otro intento; una transcripción ya guardada se reutiliza. No se reanalizan masivamente los avisos anteriores.

### Dos listas: Búsquedas | Mi feed

En la vista **LinkedIn**, la lista de ofertas tiene dos pestañas: **Búsquedas** (las búsquedas globales) y **Mi feed** (tu feed de inicio). Mismo Edge, misma sesión y mismo Iniciar/Detener.

**Canales de postulación.** La clasificación detecta cómo hay que postularse: `email`, `link`, `WhatsApp`, mensaje directo (`dm`) u `otro`, y el destino (URL, número o perfil del autor). Al generar el CV se arma también un mensaje adaptado al canal (sin asunto en los que no son email).

| Canal | Qué pasa |
|---|---|
| `email` | El flujo de siempre: **Aprobar y enviar** → Gmail con el CV adjunto. |
| `link`, `whatsapp`, `dm`, `otro` | **No se envía nada solo ni pasa por Gmail.** La oferta queda en **Lista para enviar** y tenés los botones **Copiar mensaje**, **Descargar CV**, **Abrir** (el link, `wa.me` con el mensaje ya cargado, o el perfil de LinkedIn del autor) y **«Ya me postulé»**. |

Te postulás vos por ese canal y después tocás el botón **«Ya me postulé»** (el primer toque pide confirmación en el mismo botón): la oferta pasa a **Postulada** como postulación manual. **«Ya me postulé»** también aparece en una oferta por email **sin destinatario**, y se puede usar desde **Analizada**, **Lista para enviar** o **Error**, con o sin CV generado.

**Requisitos con un toque.** En el análisis de cada oferta, el estado de cada requisito es un botón: Falta → Transferible → Coincide → Falta, como en la vista Manual. Recalcula la afinidad (y si supera el umbral genera el CV) y, si la oferta ya tiene postulación generada, la actualiza. Lo que marcás como Coincide o Transferible queda **aprendido** (`data/candidato/aprendizajes_candidato.json`): el Estratega lo recibe en cada análisis nuevo y los requisitos con el mismo nombre llegan ya con ese estado, y al regenerar un CV el Redactor parte de lo aprendido. Volver un requisito a Falta lo olvida.

**Firma del email:** el teléfono va como link `https://wa.me/<número>`.

**Chips de la lista:** el filtro por defecto es **Relevantes** (las ofertas, analizadas o por analizar, sin las descartadas). **Por analizar** muestra lo capturado que todavía no se clasificó; **Analizadas**, las que ya tienen análisis. Si un panel queda vacío en Relevantes pero hay ofertas por analizar, te avisa cuántas son.

**Contadores:** **Postuladas** (manuales, por link/WhatsApp/DM/otro) va aparte de **Enviadas** (los envíos de Gmail). Solo los envíos por Gmail cuentan para el tope diario de envíos; marcar una oferta como postulada no.

---

## ⚙️ Motor Único y Ciclo de Estados

Toda oferta —ingresada a mano o capturada por el Radar— pasa por el mismo flujo unificado:

```
Pendiente → Analizada → Generando → Lista para enviar → Enviada / Postulada (o Descartada / Error)
```

- **Pendiente:** aviso ingresado o capturado, listo para analizar.
- **Analizada:** el Estratega evaluó los dolores de la empresa, calculó la afinidad y clasificó los requisitos (coinciden, transferibles y faltan). Podés ajustar cualquier requisito con un clic para recalcular la afinidad y guardar aprendizajes.
- **Generando:** el Redactor y el Auditor elaboran el CV adaptado y el mensaje de contacto.
- **Lista para enviar:** CV y mensaje listos. Podés revisarlos, editarlos y descargar Word o PDF.
- **Enviada / Postulada:**
  - Si el canal es **email**, al tocar **Aprobar y enviar** sale por Gmail con el CV adjunto.
  - Si el canal es **enlace**, **WhatsApp** o **mensaje directo**, te postulás por tu cuenta en el sitio externo y tocás el botón **«Ya me postulé»** (pide confirmación rápida) para registrarlo sin enviar correos.
- **Descartada:** avisos que decidís descartar o búsquedas del exterior no compatibles con Argentina.
- **Error:** si algún paso falla, muestra el motivo y permite reintentar.

---

## 🔍 Filtros y Canales

Para ordenar y encontrar ofertas rápidamente en la lista:

- **Filtro de canal:** selector **«Todos · Con mail · Solo enlace»** para separar las que se postulan por correo de las que requieren enlace externo o mensaje directo.
- **Filtros de estado:** Relevantes, Por analizar, Analizadas, Listas, Generando, Enviadas, Descartadas, Con error y Todas.
- **Filtro de ubicación (en Radar):** Todas · Argentina · Exterior compatible.
- **Orden:** podés ordenar la lista por **Mayor afinidad** o por **Más recientes**.

---

## 📥 Completar Mensajes (Backlog)

Proceso en segundo plano que toma las ofertas analizadas que todavía no tienen mensaje redactado (y aquellas donde la generación dio error):

- Procesa primero las ofertas con **mejor afinidad**, con prioridad baja para no demorar las solicitudes que hagas a mano.
- Funciona también con el Radar detenido.
- Se puede **pausar y reanudar** en cualquier momento desde **Procesos**, donde indica el contador exacto (**«Quedan N»**).
- Pausarlo deja terminar el CV que esté en curso y no toma el siguiente hasta que lo reanudes.

---

## 📊 Historial de Procesos y Telemetría

Haciendo clic en la **franja de la cola** (la barra superior que muestra el estado de trabajo actual) se abre el panel lateral **Procesos**:

- **Estado general:** muestra los trabajos en curso, en cola, el estado del backlog y los trabajos terminados.
- **Progreso por paso:** avance detallado de cada fase (Estratega, Redactor, Auditor, mensaje) con tiempo y porcentaje.
- **Espera de cuota:** si Antigravity reporta que se agotó la cuota temporal, muestra el cartel de espera con la **hora exacta de renovación**. Los trabajos quedan en espera y no se cancelan ni consumen tiempo de vencimiento.
- **Topes del día:** recuento de publicaciones leídas y envíos del día para operar a ritmo seguro.

---

## 🛡️ Robustez y Tolerancia a Fallos

- **Tope compartido de AGY:** `LINKEDOUT_AGY_MAX_CONCURRENTES` limita los intentos simultáneos de análisis, generación, auditoría, mensajes y sondeo de cuota. Acepta un entero positivo; por defecto usa **3**. Si falta o es inválido (incluidos cero y negativos), usa 3 y registra una advertencia. La capacidad se fija al primer uso de cada event loop, compartida entre todas las instancias del bridge; reiniciá el servidor para cambiarla. Cada intento libera su cupo al terminar, fallar o cancelarse. Esperar admisión o cuota no consume el timeout del intento; esperar cuota tampoco ocupa cupo.
- **Dos generaciones:** la aplicación admite hasta dos CV simultáneos dentro del mismo tope compartido de **3** intentos AGY. En el Radar, cuando terminan el pitch y sus canales, se redacta el mensaje mientras se completa el CV. Ambas ramas deben terminar antes de publicar READY; un fallo o cancelación cancela y espera a la otra. El borrador queda en memoria, se consume una vez y sólo se reutiliza si siguen vigentes las versiones y entradas. Los CV heredados o editados conservan el camino normal de composición. La aprobación del contenido actual y la revalidación posterior al login SMTP siguen siendo obligatorias.
- **Plazo de trabajo activo:** cada generación de CV tiene un plazo de **45 minutos de trabajo activo** (la espera de cuota de Antigravity no cuenta). Si se supera, realiza **un reintento automático** antes de marcar error.
- **Cancelación inmediata:** al presionar **Descartar** en una oferta en curso, se cancela su generación de inmediato y se libera la cola para el próximo trabajo.
- **Recuperación tras reinicio:** si reiniciás el servidor, los CV que estaban a medias se reencolan automáticamente al arrancar. Si el CV ya estaba terminado y faltaba el mensaje, se conserva el CV y se genera el mensaje pendiente.
- **Nada sale sin aprobación:** el sistema jamás envía correos por iniciativa propia. Todo envío requiere que revises y apruebes el contenido actual.

---

## ⏱️ Topes y Ritmo de Operación

Pensados para un ritmo de navegación seguro y natural:

Todos los valores de lectura del Radar están en el perfil (`data/candidato/reglas_radar.json`, tarjeta «Reglas del
Radar» en Mi perfil); el código no tiene ninguno y, sin ese archivo completo, el Radar no lee nada. Valores de hoy:

| Acción | Parámetro (en el perfil) |
|---|---|
| Búsquedas de cada cuenta | 6 por hora y 60 por día (67 % si LinkedIn frenó a las dos cuentas en las últimas 24 h) |
| Por lectura de búsqueda | Hasta 300 publicaciones o 15 desplazamientos; corta tras 2 pantallas sin nada nuevo |
| Por lectura del feed | Hasta 80 publicaciones o 20 desplazamientos |
| Entre lecturas | 3 a 6 min aleatorios |
| Entre vueltas del Radar | 25 a 35 min aleatorios (estado `PAUSA`) |
| Pausa por bloqueo de LinkedIn | 6 h, se duplica con cada bloqueo en 24 h hasta 24 h |
| Tope diario de envíos | 40 envíos por Gmail (lo que exceda queda en cola para el día siguiente) |
| Cuentas de LinkedIn | Feed con la principal y búsquedas con la secundaria; si esa cuenta no puede, no se lee (nunca la otra) |
| Entre envíos de correo | 30 a 90 s aleatorios |
| Límite por CV | 1 generación simultánea a la vez |

---

## ✉️ Aprobación y Envío

- Para cada post o propuesta en **Lista para enviar** podés ver el aviso original, editar destinatario (elegís uno de los emails detectados o escribís otro; si la clasificación no pudo decidir cuál corresponde, el campo queda vacío y aprobar se bloquea hasta que elijas uno), asunto y texto del mail, abrir el PDF o el Word adjunto y luego **Aprobar y enviar**, **Descartar** o **Regenerar email** (opcionalmente también el CV).
- **Aprobar listos · N** encola exactamente los N posts que contaste al confirmar, y solo si no cambiaron desde entonces: un post que llegó a **Lista para enviar** después, o que se regeneró, editó o descartó entre el conteo y la confirmación, queda sin aprobar y se informa como "cambió". También omite (y te informa) los que tienen destinatario duplicado, o destinatario, asunto o cuerpo faltante o inválido.
- **Protección de duplicados:** si ya enviaste (o tenés aprobado/en curso) un email a ese destinatario en los últimos **30 días**, la aprobación se rechaza salvo que confirmes explícitamente el duplicado. «Aprobar listos» omite esos casos y te informa cuáles.
- **Aprobar con el Radar detenido:** podés aprobar envíos con el Radar detenido e igual envía lo que quedó en cola.

---

## 🛠️ Configuración Inicial

**1. App Password de Gmail** (los envíos usan SMTP `smtp.gmail.com:465`):
- Tu cuenta de Google necesita **verificación en 2 pasos** activada.
- Creá una contraseña de aplicación en https://myaccount.google.com/apppasswords.
- Copiá `.env.example` a `dev.env` (o a otro archivo indicado en `LINKEDOUT_ENV_FILE`) y completá:
  ```env
  GMAIL_ADDRESS=tu_direccion_de_gmail
  GMAIL_APP_PASSWORD=la_contraseña_de_16_letras
  ```
  (Podés pegarla con los espacios con que la muestra Google: se quitan solos.)
- `dev.env` está en `.gitignore`: no se versiona. Si falta, la vista Radar muestra un aviso con estas instrucciones y los envíos no salen.

**2. Tu perfil de candidato** (todo lo personal vive acá, fuera del repo):
- Copiá el perfil de ejemplo a la carpeta de datos y reemplazalo con tus datos:
  ```powershell
  Copy-Item -Recurse config\ejemplo\candidato dev-datos\candidato
  ```
- `personales.txt`: nombre, puesto, contacto, educación e idiomas. `experiencia.txt`: tu CV completo (un `##` por
  empresa, un `###` por rol con sus años). `restricciones.json`: dónde y cómo podés trabajar. `busquedas.json`: qué
  busca el Radar (tus búsquedas, con bloques de términos o una consulta libre; sin este archivo lee solo el feed). `conocimientos_ausentes.json`: herramientas que no manejás y que nunca
  deben aparecer en tu CV. `anios_por_area_creador.json` (opcional): años por área cuando el cálculo automático no
  alcanza. Todo se edita también desde la vista **Mi perfil**.
- `dev-datos/` (la carpeta de datos por defecto) está en `.gitignore`: tus datos, la base y las sesiones de LinkedIn
  nunca se versionan. La carpeta de datos se puede mover con la variable `LINKEDOUT_DATA_DIR`.

**3. Dependencias:** `python -m pip install -r requirements.txt` (incluye `playwright`, `python-dotenv` y `beautifulsoup4`). **No** hace falta `playwright install`: se usa el **Microsoft Edge ya instalado** en el sistema.

**4. Iniciar la aplicación:**
```powershell
python -m uvicorn server:app --host 0.0.0.0 --port 1111
```
Abrí `http://localhost:1111`. Con `--host 0.0.0.0` el celular entra por la red local (ver «Control de Acceso y Celular»);
con `--host 127.0.0.1` el portal queda solo para la PC.

**5. Primer login en LinkedIn** (una sola vez):
1. Levantá el portal (paso 4), abrí la vista **LinkedIn** y presioná **Iniciar**.
2. El estado pasa a `ESPERANDO_LOGIN`. Presioná **Mostrar navegador** e **iniciá sesión en LinkedIn a mano** en la ventana de Edge.
3. La sesión queda guardada en `data/linkedin_profile/` (perfil aislado, no versionado). El Radar revisa cada 30 s y continúa solo cuando detecta que ya iniciaste sesión.

El login, los CAPTCHAs y las verificaciones de seguridad **nunca se automatizan**: ante cualquier checkpoint el Radar queda en `ESPERANDO_LOGIN` y espera a que lo resuelvas vos.

---

## ⚠️ Errores Comunes

| Situación | Qué pasa / qué hacer |
|---|---|
| **Selectores rotos** (0 publicaciones en todas las consultas durante 2 ciclos seguidos) | El Radar pasa a `ERROR` ("posibles selectores rotos"). La primera vez que se cumple la regla de los 2 ciclos vacíos se guardan captura y HTML en `data/radar_debug/<timestamp>/`, y se sigue intentando en el próximo ciclo. LinkedIn cambia su DOM seguido: ver el punto 4 del smoke test. |
| **Gmail no configurado** | Aviso en la vista Radar: completá `GMAIL_ADDRESS` y `GMAIL_APP_PASSWORD` en `dev.env` (ver arriba). Aprobar queda bloqueado hasta entonces. |
| **Envíos pausados por autenticación** | Si Gmail rechaza las credenciales, se pausan todos los envíos y aparece **Reintentar envíos**. Corregí el App Password en `dev.env` y presioná el botón; lo aprobado sigue en cola. |
| **Error temporal de SMTP** | Solo fallos en los que el mensaje seguro no salió (no se pudo conectar o iniciar sesión, o Gmail respondió un 4xx): 3 reintentos con espera creciente (30 s, 2 min, 5 min); después el post pasa a `ERROR` sin aprobación. |
| **Envío incierto** | Si la conexión se corta *mientras* se transmite el mensaje no se sabe si Gmail lo aceptó: el post pasa a `ERROR` ("Envío incierto: verificá en Gmail (Enviados) antes de reintentar") y **nunca se reintenta solo**. Al presionar **Reintentar** la UI te pide confirmar; después hay que volver a aprobar. Si Gmail ya aceptó el mensaje y falla solo el cierre de la conexión, cuenta como `ENVIADO`. |
| **Caída durante un envío** | Al reiniciar, un post que estaba `ENVIANDO` pasa a `ERROR` ("envío interrumpido, verificar en Gmail antes de reintentar") sin aprobación: revisá "Enviados" en Gmail antes de reintentar para no duplicar (la UI pide confirmación). |
| **Fallo de la clasificación** | El lote se parte en mitades; un post que falla solo se reintenta y tras 3 fallos pasa a `ERROR` (**Analizar ahora** lo vuelve a poner primero). |
| **Fallo del análisis** | Se reintenta hasta 3 veces; después `ERROR` con el motivo (**Analizar ahora** lo reintenta). |
| **Fallo del pipeline o del composer** | El post pasa a `ERROR` con el mensaje y el Radar sigue con el resto. |
| **Plazo de 45 min del CV superado** | Cada generación tiene un límite de 45 minutos de trabajo activo (la espera de cuota de Antigravity no cuenta). Si se supera, pasa a `ERROR` con un reintento automático antes de fallar definitivamente. |

---

## 🔒 Control de Acceso y Celular

Para operar el sistema desde tu celular conectado a la misma red Wi-Fi:
1. En la PC, tocá **Celular** en la barra del Radar.
2. Escaneá el código QR: se guardará una cookie de acceso seguro en el navegador del teléfono.
3. Cualquier conexión externa sin emparejar tiene acceso de **solo lectura** (responde 403 ante intentos de iniciar/detener el radar, aprobar o descartar ofertas).

---

## ⚠️ Aviso de Riesgo

Automatizar la lectura de LinkedIn va contra sus términos y condiciones de uso y puede derivar en restricciones sobre tu cuenta. El Radar mitiga esto operando con ritmo humano, pausas aleatorias y deteniéndose ante cualquier comprobación de seguridad, pero el riesgo no desaparece por completo. Usalo bajo tu propia responsabilidad.

---

## ✅ Smoke Test Manual

1. Creá `dev.env` desde `.env.example` y completá `GMAIL_ADDRESS` y `GMAIL_APP_PASSWORD` vos mismo.
2. Levantá el portal (paso 4 de la instalación) → vista **LinkedIn** → **Iniciar**. El estado pasa a `ESPERANDO_LOGIN` → **Mostrar navegador** → iniciá sesión en LinkedIn a mano.
3. Esperá el primer ciclo: tienen que aparecer publicaciones en la lista. Revisá que autor, texto y emails detectados sean correctos.
4. Si la lista queda vacía o mal parseada, puede que haya que calibrar los selectores del parser (`core/linkedin_parser.py`) contra una captura real: dejá que el Radar genere `data/radar_debug/<timestamp>/page.html` (se recortan 3–4 publicaciones reales, anonimizadas, para reemplazar `tests/fixtures/linkedin_search_posts.html`, se ajustan los selectores y se corre `python -m pytest tests/test_linkedin_parser.py -v` hasta que pase).
5. Elegí una oferta de prueba y **editá el destinatario poniendo tu propia casilla antes de aprobar**. Aprobá y verificá en Gmail ("Enviados" y bandeja de entrada) el asunto, el cuerpo y el archivo adjunto.
6. Detené el Radar. Iniciá de nuevo, reiniciá el portal con el Radar iniciado y verificá que se reanude solo.

---

## 📁 Estructura del Proyecto

```
LinkedOut/
├── core/
│   ├── bridge.py               # Conexión con el CLI de Antigravity (AGY, agy.exe)
│   ├── database.py             # SQLite WAL local (data/postulaciones.db)
│   ├── strategist.py           # Subagente 1: análisis de dolores, requisitos y score
│   ├── writer.py               # Subagente 2: redactor de CV (formato Google XYZ) y mensaje
│   ├── auditor.py              # Subagente 3: auditor ATS y detección de estilo IA
│   ├── scraper.py              # Extractor de ofertas desde enlaces web
│   ├── radar.py                # Bucle del Radar, colas de procesamiento, backlog y robustez
│   ├── radar_store.py          # Almacenamiento de ofertas y estado de procesos
│   ├── radar_api.py            # API REST de control, cola, backlog y aprobación
│   ├── linkedin_harvester.py   # Automatización con Playwright sobre perfil de Edge
│   ├── linkedin_parser.py      # Extracción y filtrado de publicaciones de LinkedIn
│   ├── triage.py               # Clasificación de avisos con modelo rápido
│   ├── email_composer.py       # Redactor de asunto y cuerpo de correo
│   ├── mailer.py               # Envío seguro vía Gmail SMTP con adjunto
│   └── docx_generator.py       # Compilador de CV a formato DOCX ejecutivo
├── data/
│   ├── candidato/              # Experiencia laboral, datos de contacto y aprendizajes
│   └── exports/                # CVs compilados en DOCX y PDF
├── static/
│   ├── index.html              # Portal web responsivo
│   ├── app.js                  # Lógica del cliente, SSE y soporte general
│   └── radar.js                # Vistas Manual y Radar, historial y control de colas
├── tests/                      # Suite de pruebas automatizadas
├── server.py                   # API FastAPI (se levanta con uvicorn)
└── README.md
```

---

## 🧪 Pruebas Automatizadas

Para correr la suite completa de pruebas:

```powershell
python -m pytest -q tests -p no:cacheprovider
```
