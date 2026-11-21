# Código y tests: normas de detalle

Detalle que `AGENTS.md` resume en renglones. Valen igual que las reglas de `AGENTS.md`.

## Interfaz
- Textos visibles en castellano rioplatense (voseo), sin jerga.
- Nada de `innerHTML` con datos: usar `createElement` + `textContent`.
- En `#vista-radar` se oculta con `hidden`. Texto de 12 px como mínimo, sin `text-zinc-500/600` (tests de legibilidad y jerarquía).
- **Celular:** el diseño es mobile-first (rediseño en curso, ver `AGENTS.md`). Los tests de interfaz miden a 360, 375 y 1200 px.

## Mail
- Nada se envía sin aprobación explícita del contenido actual (`updated_at`).
- **«Aprobado» no es un estado.** El botón «Aprobar» es la orden de enviar: `aprobado_at` deja el mail en la cola de envío y `_send_worker` lo manda a los 30–90 s. No existen los «LISTO aprobados» que esperan: entre `LISTO` (sin aprobar) y `ENVIADO` (terminal) no hay estado intermedio. No escribas «pierde la aprobación» ni «queda aprobado sin enviar», y no lo uses como argumento de diseño.
- **Modo de prueba:** con `LINKEDOUT_MAIL_PRUEBA` en el `.env` local, todo envío va a esa dirección con «[PRUEBA]» en el asunto. Ninguna dirección se escribe en el código.

## Tests
- Los tests nunca usan `data/` real. Nunca llamar a mano al portal real (puerto 1111) ni arrancar el Radar en pruebas. No usar `git stash`.
- **Estables con la PC cargada:** se sincroniza por condición (Events, esperar hasta que se cumpla), nunca por tiempos fijos. Los tiempos de `asyncio.wait_for` y de las guardas son solo contra bloqueos: 60 s, no 5. Un reloj inyectado (`now`, `reloj`) se usa en todo el cálculo y en los datos que el test registra.
- **Cobertura:** piso de 96 % (`COBERTURA_MINIMA` en `tools/verificar.py`; ver `docs/cobertura.md`).
- **Por endpoint:** cada ruta de `server.py` y `core/radar_api.py` tiene un test de éxito, uno de error y uno de efecto en
  la base (en los GET: la respuesta refleja una fila sembrada y la base no cambia; en las rutas que delegan en el Radar:
  la base queda igual). Una ruta nueva entra con los tres. Están en `tests/test_endpoints_server.py`,
  `tests/test_endpoints_radar_api.py` y los tests de cada tema; el 422 de un id no numérico está parametrizado por ruta
  (`test_id_no_numerico_da_422_sin_tocar_la_base`) y los 403 de las rutas de operación, en
  `test_post_desde_afuera_da_403_sin_tocar_la_base_ni_el_radar`.

## Base de datos
**Una lectura de la base no escribe:** ni `INSERT OR IGNORE`, ni un `UPDATE` sin cambios, ni `BEGIN IMMEDIATE` "por las dudas". En WAL eso toma el lock de escritura y la lectura da «database is locked» detrás de cualquier escritura de más de 5 s (`busy_timeout`). Migraciones y reinicios diarios: primero se lee y solo se escribe si falta algo. Lo vigila `tests/test_concurrencia_lectura_escritura.py`.

## Suite: marcadores y quién corre qué
Marcadores (`pytest.ini` y `tests/conftest.py`): `ui` (navegador real, Playwright), `rendimiento` (tiempos contra topes),
`windows` (solo Windows; se agrega solo a un test con `skipif` que nombra Windows), `tambien_windows` (portables que
también corren en Windows, `TAMBIEN_EN_WINDOWS`) y `aislado` (mide tiempos: se corre en serie, fuera del paralelo).

| Quién | Comando | Qué corre |
|---|---|---|
| Sesión, en la PC | `python tools/tests_rapidos.py` | los tests de los archivos tocados, `-n 1`, sin `ui` ni `rendimiento` |
| CI, por jobs | `tools/verificar.py --etapa linux / windows / navegador / cobertura` | lo mismo repartido: Linux sin `windows`; Windows `windows or tambien_windows`; navegador y rendimiento en Linux; cobertura combinada |
| Suite completa, a mano | `python -m pytest -q -n 6 -m "not aislado" -p no:cacheprovider` y después `-m aislado` | todo en paralelo menos `aislado`; después `aislado` en serie |

La forma a mano no separa `ui` ni `rendimiento`: solo saca del paralelo los `aislado`. Si ahí falla un test de tiempos,
se repite solo ese antes de darlo por rojo.
`verificar.py` sin la variable `CI` sale con código 2.

## Compuerta y carga de la PC
- **La suite completa corre en el CI de GitHub.** En la PC satura el disco y el portal deja de responder. `tools/verificar.py` fuera del CI (sin `CI`) sale con código 2. En la PC: `python tools/tests_rapidos.py`, que corre los tests de los archivos tocados contra `origin/main` (un test tocado, o los tests que nombran el módulo tocado), con `-n 1`, BelowNormal, sin navegador ni rendimiento.
- **Una compuerta a la vez:** `tools/verificar.py` y `tools/tests_rapidos.py` toman un candado del sistema (`tools/candado_compuerta.py`, archivo con PID en `%TEMP%`); el de un PID muerto se libera solo. Corren en prioridad BelowNormal; la suite usa `LINKEDOUT_WORKERS` (8 por defecto, 4 en el CI).
- **Vigía del portal:** `core/vigia.py`. Si el event loop no avanza en 10 s, escribe las pilas de todos los hilos (`faulthandler`) en `<DATA_DIR>/vigia.log`, una vez por cuelgue y cada 60 s si sigue. No reinicia nada.
- **Deploy sin carga:** mientras corre `tools/deploy.ps1` ninguna sesión corre su compuerta ni la suite: cinco suites a la vez tumbaron los tests de tiempos del deploy dos veces.
- **Sin ventanas de consola:** en Windows cada comando que corre un agente (bash, git, python, PowerShell) puede abrir y cerrar una ventana de cmd. Nada de bucles de sondeo cortos: esperá con un solo comando en segundo plano y con intervalos de 2 min o más, y no lances comandos de más.

## Commits y comandos
- Commits y PRs: ver [AGENTS.md](../AGENTS.md#commits-y-prs).
- Comandos para el usuario: Windows PowerShell 5.1 (bloque `powershell`, separador `;`).
