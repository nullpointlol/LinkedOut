# AGENTS.md — LinkedOut

Instrucciones para todo agente que trabaje en este repo (Claude, Codex, AGY u otro). Lo que no está acá ni en `docs/`, otro agente no lo sabe. Cada regla va una sola vez; el detalle, en el doc enlazado.

## Proyecto
- LinkedOut: portal local FastAPI que lee ofertas de LinkedIn (Radar y carga manual), las analiza con IA y genera CV y mail de postulación.
- Nada se envía sin que el creador apruebe el contenido. Los datos del candidato viven fuera del repo, en `<DATA_DIR>/candidato/`; leé también `data/candidato/reglas_candidato.md`.
- Python, SQLite (WAL), HTML/JS sin framework. Textos en castellano rioplatense.

## Comandos
- Instalar: `pip install -r requirements-dev.txt`. Levantar un portal: `python -m uvicorn server:app --port <puerto>` (con `--host 0.0.0.0` lo ve el celular de la red local).
- Tests en la PC: `python tools/tests_rapidos.py` (solo los de los archivos tocados, `-n 1`, prioridad baja). Nunca la suite entera en la PC: frena al portal real.
- **Compuerta = CI de GitHub (Fase 2, repo público).** La suite completa corre en GitHub Actions en cada push y pull request (`.github/workflows/ci.yml`: checks `suite`, `windows`, `navegador` y `cobertura`, todos por `tools/verificar.py --etapa ...`). En la PC no se corre la suite completa ni hay compuerta nocturna: la tarea `LinkedOut-compuerta` y `compuerta-produccion.ps1` quedan archivados, y producción ya no se apaga de noche. Marcadores y equivalencias: `docs/codigo-y-tests.md`.
- Procesos (creador, 16/11: nunca procesos inútiles): todo lo que lance una sesión (portal de prueba, suite, espera) va con plazo por `tools/temporal.py` y se apaga al terminar. Las esperas en segundo plano llevan `timeout` y se comprueba que avanzan; nunca se espera en silencio. El guardián apaga todo proceso que no sea producción, el dev activo o un temporal vigente; el vigilante revive producción si se cae (detalle: `docs/despliegue.md`).
- Portal de prueba (1199, datos copiados, sin Radar), en PowerShell:
```powershell
$env:LINKEDOUT_DATA_DIR="<copia de datos>"; $env:LINKEDOUT_SIN_RADAR="1"; python tools/temporal.py lanzar --minutos 60 --motivo "portal de prueba" -- python -m uvicorn server:app --port 1199
```
- Producción (1111) es lo real y está siempre prendida, con el Radar (feed, búsquedas, análisis y generación) y los radares de portales corriendo (`/api/radar/status`, `/api/portales/estado`). Tras cualquier deploy, rollback o reinicio, se verifica que sigan así (creador, 16/11 y 17/11).
- Portal dev 1112, solo para pruebas y apagado por defecto (creador, 17/11): `powershell -File tools\dev.ps1 -Iniciar | -Detener | -ActualizarCodigo | -RefrescarDatos`. Arranca con datos copiados de producción sin sesiones de LinkedIn, Radar detenido y mail de prueba (a destinatarios reales solo con `-MailReal`). Al terminar la prueba, `-Detener`.
- Backup completo del portal local (7z, nunca a git): `powershell -File tools\backup_local.ps1`; no detiene el portal ni el Radar. Un backup «CON ADVERTENCIAS» no está completo: leé su informe. Qué trae y cómo restaurar: `tools/restaurar_local.md`.
- **Estado y pendientes: `docs/agentes/pendientes.md`** (leelo primero al retomar). Despliegue y vuelta atrás: `docs/despliegue.md`.

## Estructura
- `core/`: motor (Radar, análisis, CV, mail, tickets, perfil). `server.py`: API.
- `static/`, `templates/`: interfaz. `tests/`: pytest. `config/ejemplo/`: perfil ficticio.
- `tools/`: compuerta, deploy, rollback, sintaxis de LinkedIn. `tools/agents/`: kit del workflow (no se edita).
- `docs/`: detalle de cada tema. `data/` (real): nunca se versiona ni se toca.

## Reglas duras
- **Producción, solo con OK del creador.** Sin su OK no se despliega, no se hace rollback, no se reinicia ni se toca `~/LinkedOut/{produccion,datos,produccion.env}`, `portal.cmd`, `portal.vbs` ni sus tareas (`LinkedOut-portal`, `LinkedOut-vigilante`, `LinkedOut-guardian`). Con un archivo `CONGELADO` presente no hay deploy. Producción cambia solo con `tools/deploy.ps1` y `tools/rollback.ps1`, nunca a mano. Rutas exactas: `docs/despliegue.md`.
- **Datos reales:** `<DATA_DIR>` (perfil, búsquedas, base) no cambia de formato antes de que esté desplegado el código que lo usa. Los tests nunca usan `data/` real. Detalle: `docs/perfil.md`.
- **Mail:** nada se envía sin aprobación explícita del contenido actual. «Aprobar» es la orden de enviar; no existe un estado «aprobado» que espera. Con `LINKEDOUT_MAIL_PRUEBA` todo va a esa dirección con «[PRUEBA]». Ninguna dirección en el código.
- **LinkedIn:** ningún test arranca el Radar; todo portal de prueba lleva `LINKEDOUT_SIN_RADAR=1`. La sintaxis se prueba solo con la cuenta 2 (`docs/linkedin-sintaxis.md`).
- **Cuentas (creador, regla de producto):** el feed se lee solo con la cuenta principal; las búsquedas, solo con la secundaria. Sin la secundaria disponible no se busca (`cuenta_feed` y `cuenta_busquedas` en `reglas_radar.json`; detalle en `docs/radar.md`).
- **Secretos y datos personales:** nunca en código, tests, docs ni commits. Los tests usan un candidato inventado (perfil ficticio de `config/ejemplo/`).
- **Nombre:** el proyecto es LinkedOut. Nada nuevo (tags, carpetas, scripts, textos) con el nombre viejo ni sus siglas.
- **Guardas de producción:** `deploy.ps1` y `rollback.ps1` no hacen nada sin `-Produccion`; la suite se corta si los datos, el `.env` o un puerto apuntan a producción, y en los tests conectarse al 1111 falla. Sin variables, los datos son `dev-datos` y el `.env` es `dev.env`. Un test que corre un script de despliegue usa config simulada (`LINKEDOUT_CONFIG_DIR`) y un `HOME` de prueba. Detalle: `docs/despliegue.md`.
- Nunca `git stash`. Nunca llamar a mano al portal real.

## Flujo
- `develop` es la rama más actualizada. Todo sale de `develop` en un worktree nuevo y vuelve a `develop`. Un worktree que nació de otra base se lleva a `origin/develop` antes de editar; lo hecho sobre una base vieja es solo referencia y se rehace.
- Un tema, una entrega, una rama (creador). Si otra sesión tiene el mismo tema, se acuerda una sola entrega: una mergea primero y la otra trae `develop` y entrega enseguida, o se juntan en una rama. Ninguna rama del tema queda viva fuera de `develop`.
- TDD: test rojo, código, verde. Antes de mergear, traé `develop` a tu rama y resolvé ahí. Mientras se trabaja, en la PC, solo `tools/tests_rapidos.py`.
- **Nada entra a `develop` ni a `main` (ni siquiera docs) sin el CI de GitHub en verde sobre ese commit** (Fase 2; reemplaza la suite local autorizada y la compuerta nocturna). Se integra por pull request: las reglas de las ramas exigen los 4 checks. Mergea a `develop` el coordinador, que aprueba en nombre del creador. Promoción a producción solo con el OK del creador. Se prueba antes en el 1199.
- Desde el 14/11 el coordinador hace todo él mismo hasta nuevo aviso del creador; otra sesión solo si no gasta más cuota. Las sesiones se archivan solo cuando el creador lo pide. A otras sesiones se les escribe solo si un cambio les exige actuar (cada mensaje gasta su cuota). Lo que el creador le aprobó a una sesión vale para todas: no se le vuelve a confirmar.
- **Toda sesión hija le avisa al coordinador. Es obligatorio, sin excepción (creador, 18/11).**
  - **Al finalizar, siempre.** Cada turno termina con un SendMessage a la sesión que la inició. Va también al terminar la tarea, con rama, commit y resultado de los tests. No hay turno que termine sin ese aviso.
  - **Al instante, durante el trabajo:**
    - si se traba o algo falla, con la causa;
    - si necesita una decisión del creador;
    - si deja un proceso largo corriendo, con qué es y cuánto falta.
  - No se espera a que el coordinador pregunte. Si no responde, se sigue trabajando en lo que no dependa de él. La operación nunca queda en pausa.
- Commits en castellano, terminados en `Co-Authored-By: <modelo>`. Comandos para el creador: PowerShell 5.1, bloque `powershell`.
- Cada regla nueva sobre cómo actuar se escribe acá (o en el kit, `~/.claude/agent-workflow`, si vale para todos los proyectos), se commitea y se sube al remoto. La memoria local de un agente es solo una copia.

## Con el creador
- Modo cavernícola coherente: frases cortas y claras, solo lo necesario, en voseo. Primero el resultado, después lo que él debe hacer.
- Al creador se le escribe solo si hay una decisión que tomar. Nada de reportes de avance.
- Si el coordinador pide un plan primero, se espera su OK antes de programar (13/11).
- Decidí y avanzá sin pedir «ok» en lo que ya pidió. Preguntá solo ante acciones irreversibles o externas, gastos o decisiones de producto.
- Veracidad exacta: cada falla con su número exacto y su causa real; lo no verificado se dice como no verificado.
- No adelantar decisiones: lo que depende de una decisión pendiente del creador no se prepara, no se mide ni se programa.
- Sin límites propios (15/11): no agregar condiciones, topes, pausas ni esperas que el creador no definió. Si un portal
  muestra un captcha, se abre el ticket y nada más; el creador decide después.
- Sin ventanas de consola: nada de sondeos cortos; una espera en segundo plano, de 2 min o más.
- Frenado más de 20 min: avisá con opciones. Desde el 17/11 el diseño es mobile-first (rediseño en curso: `docs/agentes/plan-publico.md`).
- Ante una falla de AGY, primero se busca la causa en la misión, el kit, los permisos o los datos; nunca «es un modelo inferior».
- Jooble tiene una sola clave (no existe una de Argentina): nunca pedirla.
- El CV maestro (ES/EN) y sus cifras ya están aprobados por el creador: no son un pendiente.

## Workflow de agentes
- Es la norma: AGY ejecuta, Codex planifica y revisa, Claude coordina y audita. Fuente: `docs/workflow-agentes.md` (antes de despachar, §0, §0b y el ciclo de tu rol); roles, modelos, ciclo v2 y vigilancia en este repo: `CLAUDE.md`.
- **Hoy está suspendido temporalmente** (orden del creador): Claude solo, sin AGY ni Codex, hasta que él diga otra cosa. Las sesiones programan, testean y verifican ellas mismas con estas normas. Nada de lo que generó el kit se borra ni se edita (`tools/agents/`, `docs/workflow-agentes.md`). En `CLAUDE.md` solo se edita la sección «Directivas de Claude» (creador, 17/11).
- Al pedir trabajo a cualquier agente: invariantes, prioridad ante conflicto y una medida por cada cosa que importa.
- Ruteo (creador): AGY ejecuta código y tests (`EXECUTOR=auto`). Codex `medium` planifica y revisa plan, diffs y features; `low` para diagnósticos y tareas chicas; `high` solo en el carril S4 o si toca envío, seguridad o concurrencia. Codex ejecuta en el carril rápido o si AGY falla.
- Carriles: cada pedido lleva `Carril:`.
  - **Rápido** (≤2 archivos, ~20 líneas, sin concurrencia, envío, migraciones ni seguridad): una misión concreta, el ejecutor con cuota (Codex primero), suite completa una vez, sin revisión de Codex; si a los 10 min no está, lo hace Claude. Meta ≤15 min.
  - **Normal:** tests y código en la misma misión, una revisión de Codex, sin holdout. Meta ≤1 h.
  - **S4:** proceso completo con `--high`. Meta ≤3 h.
- Misiones para AGY (creador: «no es el modelo»): traen en «Comandos permitidos» un comando de tests de su zona; no llevan decisiones abiertas (Codex lista los tests heredados que el cambio rompe y el coordinador decide por escrito, por test, si cambia la expectativa o el código); tienen un solo objetivo, en pasos concretos. En cada ronda AGY recibe todos los fallos, con su mensaje y si son cambio pedido o regresión.
- Revisiones con tope: Codex revisa spec y plan buscando casos límite antes de programar. Máximo 2 revisiones de la feature completa; después solo bloquea un P1 (veracidad, envío o aprobación, seguridad, pérdida de datos). Los P2/P3 van a «Mejoras menores» de `docs/agentes/pendientes.md` y la feature se integra igual. Se despliega por feature, con las pausas intactas.
- Problemas del workflow o fallas del kit: se avisan enseguida a la sesión «Análisis workflow agentes» (worktree, misión y línea del log), que también resuelve los permisos de AGY; nunca se parchean a mano en silencio. Si Codex se queda sin cuota a mitad de una tarea, Claude la termina sin pedir más aprobaciones.
- El coordinador revisa cada ~20 min todos los trabajos en curso y actúa ante lo colgado.

## Código y tests
- Nada de `innerHTML` con datos: `createElement` + `textContent`. Texto de 12 px como mínimo. Detalle: `docs/codigo-y-tests.md`.
- Cobertura mínima 96 % (la exige la compuerta).
- Tests estables: se espera por condición, nunca por tiempos fijos; el reloj se inyecta.
- Una lectura de la base no escribe (WAL, «database is locked»).
- El Radar corre en su propio hilo: el event loop nunca espera a otro hilo en forma síncrona.
- Vigía: si el event loop se traba más de 10 s, las pilas de todos los hilos quedan en `<DATA_DIR>/vigia.log`. Ante un cuelgue, mirá ese log antes de reiniciar.
- Las reglas con números van como constantes con nombre, nunca escondidas.

## Dominio
- Radar (búsquedas solo en `busquedas.json`, reglas solo en `reglas_radar.json` del perfil, sin idioma, orden por rondas, hilo propio, reintentos, generar a mano, resultados): [docs/radar.md](docs/radar.md)
- Perfil del candidato (datos, base del CV, formato, años del mail): [docs/perfil.md](docs/perfil.md)
- Portales (Jooble e Indeed: mismo circuito, duplicados, reglas por portal, vuelta propia sin LinkedIn): [docs/portales.md](docs/portales.md)
- Tickets de errores (`reportar_error`, sin duplicados, no se arreglan solos): [docs/tickets.md](docs/tickets.md)
- Código, tests, mail y compuerta en detalle: [docs/codigo-y-tests.md](docs/codigo-y-tests.md)
- Despliegue, rollback, compuerta nocturna y producción: [docs/despliegue.md](docs/despliegue.md)
- Sintaxis de LinkedIn: [docs/linkedin-sintaxis.md](docs/linkedin-sintaxis.md)
