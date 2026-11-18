# Despliegue y vuelta atrás

## Producción (desde el corte del 14/11)
| | Dónde |
|---|---|
| Código | `~\LinkedOut\produccion`: clon propio del repo (no un worktree), rama local `produccion` en una versión `linkedout-estable-*` |
| Datos | `~\LinkedOut\datos` (`LINKEDOUT_DATA_DIR`) |
| `.env` | `~\LinkedOut\produccion.env` (`LINKEDOUT_ENV_FILE`) |
| Proceso | tarea `LinkedOut-portal` al iniciar sesión → `~\LinkedOut\portal.vbs` → `portal.cmd` (puerto 1111, log `portal.log`) |
| Vigilante | tarea `LinkedOut-vigilante` cada 5 min → `~\LinkedOut\vigilante.ps1`: 2 intentos sin respuesta → reinicia `LinkedOut-portal` |
| Config de despliegue | `~\LinkedOut\despliegue.json` (+ `despliegues.log` al lado) |

Variables `LINKEDOUT_*` (S4, 18/11): el `portal.cmd` de producción todavía fija las del nombre viejo del proyecto y el
código las sigue leyendo si falta la nueva (`core.paths.variable_entorno`). Esa compatibilidad se borra en la Fase 2,
después de actualizar el lanzador.

Lo de antes del corte queda sin uso pero no se borra: la ubicación vieja (`~/<UBICACION_VIEJA>`, la constante de `tests/guardas_produccion.py`) (worktree viejo,
`portal-real.*`, logs) y la tarea vieja del portal, deshabilitada. El servicio de Windows `LinkedOut` quedó instalado pero sin uso (detalle
del corte en la historia de git, hasta el 17/11).

Un merge a `develop` no toca producción. La única forma de cambiarla es:

| Qué | Comando |
|---|---|
| Crear una versión (sobre `develop` con el CI en verde) | `git tag -a linkedout-estable-AAAAMMDD <commit> -m "..."` y `git push origin refs/tags/linkedout-estable-AAAAMMDD` |
| Desplegar una versión | `powershell -File tools\deploy.ps1 -Produccion -Version linkedout-estable-AAAAMMDD` |
| Volver al despliegue anterior (< 1 min) | `powershell -File tools\rollback.ps1 -Produccion` |
| Volver a una versión | `powershell -File tools\rollback.ps1 -Produccion -A linkedout-estable-AAAAMMDD` |
| Volver a un punto con la base de ese momento | `powershell -File tools\rollback.ps1 -Produccion -A deploy-AAAAMMDD-HHMMSS -ConBase` |
| Ver la compuerta de una rama (nocturna) | `~\LinkedOut\compuerta\ultimo.json` (por rama: `sha`, `resultado`, fallados) |
| Backup completo del portal local (7z) | `powershell -File tools\backup_local.ps1` (restaurar: `tools/restaurar_local.md`) |

Sin `-Produccion` los scripts no hacen nada (13/11). Con `CONGELADO` en `~/LinkedOut` o en la ubicación vieja
(el de antes del corte), tampoco.

## Qué garantiza `deploy.ps1`
1. Solo despliega versiones: un tag `linkedout-estable-*` (lo trae de origin) cuyo commit está en `origin/develop`.
2. Pasó la compuerta: el CI de GitHub en verde o, sin CI disponible, `tools/verificar.py` en una copia aparte.
   Si el CI falló o todavía corre, no despliega.
3. Si `requirements.txt` cambió respecto de lo que corre, no despliega: primero se instalan los requisitos.
4. No corta un CV que se está generando.
5. Hace backup de la base antes de tocar nada.
6. Detiene la tarea `LinkedOut-portal`, pasa el clon a la versión (`reset --hard`) y la vuelve a arrancar. Chequeo de
   salud (página 200, estado del Radar, proceso nuevo). Si falla, vuelve solo al commit anterior.
7. Deja un tag local `deploy-*` en el clon (versión, commit anterior y backup: lo usa `rollback.ps1`) y una línea en
   `~/LinkedOut/despliegues.log`.
8. Con el portal detenido (Edge cerrado), copia las cookies de cada perfil `linkedin_profile*` (las sesiones de
   LinkedIn) a `<datos>/backups/sesiones/` (`Save-SesionesEdge`; quedan las 3 últimas por perfil). Con el Radar
   andando, Edge las tiene en exclusiva: `tools/backup_local.ps1` usa esta copia. Si falla, lo anota y sigue.

## Configuración local
`~/LinkedOut/despliegue.json` (no se versiona; otra carpeta con `LINKEDOUT_CONFIG_DIR`): `produccion`, `datos`, `tarea`
y `puerto`, todas obligatorias: no hay valores por defecto que apunten a producción. `env` es opcional (por defecto,
`produccion.env` junto a la carpeta de datos). La usan `deploy.ps1`, `rollback.ps1`, `dev.ps1` (de dónde copia los
datos y las claves) y `backup_local.ps1`. El `despliegue.json` de la ubicación vieja ya no se lee.

## Guardas de producción (13/11)
- `tests/conftest.py` corta la suite si la carpeta de datos, el `.env` o un puerto apuntan a producción:
  `~/LinkedOut/{produccion,datos,produccion.env}`, los archivos sueltos de producción en `~/LinkedOut` (`portal.cmd`,
  `portal.vbs`, `portal.log`, `vigilante.ps1`, `vigilante.log`, `despliegue.json`, `despliegues.log`, `CONGELADO`), y lo
  de antes del corte: la ubicación vieja (`~/<UBICACION_VIEJA>`, la constante de `tests/guardas_produccion.py`), `data/` y `.env` del checkout principal (`tests/guardas_produccion.py`). Los tests usan un `.env` propio vacío y conectarse a 127.0.0.1:1111 falla.
- Sin `LINKEDOUT_DATA_DIR`, `core/paths.py` usa `<repo>/dev-datos`; sin `LINKEDOUT_ENV_FILE`, el mailer usa
  `<repo>/dev.env`. Una herramienta que use los datos reales (`tools/login_cuenta.py`) se corre con la variable explícita.
- Nunca correr un script de despliegue real en un test: el 13/11 un test corrió `deploy.ps1` con la config real
  (la variable de prueba todavía no existía) y movió el código de producción. Todo test de `deploy.ps1`, `rollback.ps1`
  o `dev.ps1` usa `LINKEDOUT_CONFIG_DIR` con una config simulada, pone `USERPROFILE`/`HOME` del subproceso en la
  carpeta de prueba y comprueba antes que el script lee esa variable.

## Portal dev 1112
`tools/dev.ps1 -Iniciar | -Detener | -ActualizarCodigo | -RefrescarDatos` (`tools/dev_datos.py` hace la copia y el `.env`):
- código en `~/LinkedOut/dev`, un worktree en la punta de `develop`;
- datos en `~/LinkedOut/dev-datos`, copia de la carpeta de datos real (bases con la API de backup de SQLite) sin perfiles de
  Edge (`linkedin_profile*`, `desktop_profile`), backups ni `.env`;
- `~/LinkedOut/dev.env`: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` y `JOOBLE_API_KEY` copiadas del `.env` real (nunca se
  imprimen) y `LINKEDOUT_MAIL_PRUEBA`: todo mail va a la dirección de prueba;
- solo para pruebas y apagado por defecto (creador, 17/11): `-Iniciar` deja la marca `~/LinkedOut/dev-activo` (sin ella
  el guardián apaga el 1112) y `-Detener` la quita. En la base recién copiada, el Radar detenido, sin búsquedas y con el
  análisis y la generación en pausa. Sin tarea programada ni ventana (un `.cmd` oculto);
- el mail va en modo prueba a la casilla del remitente (o a `-MailPrueba <dirección>`); a destinatarios reales solo con
  `-MailReal`.
Se niega antes de hacer nada: otro puerto que el 1112, y como destino los datos reales, el `.env` real, producción
(`~/LinkedOut/{produccion,datos,produccion.env}` y sus archivos sueltos: `portal.cmd`, `portal.vbs`, `vigilante.ps1`,
`despliegue.json`, `CONGELADO` y logs) o lo de antes del corte (la ubicación vieja).

## Compuerta nocturna (14/11; habilitada de nuevo el 17/11)
El CI de GitHub está suspendido hasta que haya minutos (`.github/workflows/ci.yml` quedó solo con `workflow_dispatch`).
La suite completa corre de noche en la PC:
- tarea programada `LinkedOut-compuerta`, diaria a las 03:00, oculta (`conhost --headless`), con tope de 3 h y solo con
  la sesión iniciada;
- **con producción apagada** (creador, 17/11): la tarea lanza `~\LinkedOut\compuerta-produccion.ps1` (fuera del repo,
  como el vigilante). Deja la marca `~\LinkedOut\compuerta-en-curso`, apaga el 1111 con su árbol y sus Edge, corre
  `compuerta\herramienta\tools\compuerta_nocturna.ps1` y, pase lo que pase, quita la marca, levanta `LinkedOut-portal` y
  espera al Radar. Con la marca de menos de 3,5 h el vigilante no revive el 1111; una marca más vieja (la tarea murió por
  su tope) se ignora. `-SinSuite` prueba el apagado y la vuelta sin correr la suite (probado el 17/11: 14 s). Lo anota
  en `~\LinkedOut\compuerta\noche.log`. Pasarlo al repo, con tests, está en `docs/agentes/pendientes.md`;
- usa su propio worktree, `~\LinkedOut\compuerta\herramienta`, que cada noche pasa a la punta de develop;
- corre `tools/verificar.py` completo con 4 workers en BelowNormal sobre develop y sobre cada rama remota con cambios que
  develop no tiene (`git cherry`) y commits de los últimos 7 días, de la más reciente a la más vieja, menos las de
  `~\LinkedOut\compuerta\excluir.txt` (ramas reemplazadas que no se borran), de a una y en un worktree temporal. Un commit ya en
  verde no se repite;
- vigila el portal real (GET a su status cada 15 s): ante 2 respuestas seguidas de más de 3 s corta la suite y lo anota
  como «cortada» (con producción apagada no corta nunca);
- deja `~\LinkedOut\compuerta\AAAAMMDD.md` (por rama: verde, rojo o cortada, fallados, duración) y `ultimo.json`, más
  un log por rama en `logs\`.
Las reglas son constantes con nombre en `tools/compuerta_nocturna.py`.

## Compuerta de calidad (CI, suspendido)
`.github/workflows/ci.yml` corre `tools/verificar.py --etapa ...` en cada push a cualquier rama. Es el único lugar donde
corre la suite completa; una rama se mergea solo con el CI en verde sobre su último commit. Cuatro jobs en paralelo
(13/11: Linux se factura x1 y Windows x2; unos 6 min de reloj y unos 17 facturados por corrida):
- **Suite (Linux, `--etapa linux`)**: todo menos navegador, rendimiento y lo marcado `windows`, con `-n auto`.
- **Tests de Windows (`--etapa windows`)**: los marcados `windows` (un `skipif` que nombra Windows: PowerShell,
  prioridades, Job Objects) y los de `TAMBIEN_EN_WINDOWS` en `tests/conftest.py` (PDF con Edge real, escritorio,
  bridge), que también corren en Linux.
- **Navegador y rendimiento (Linux, `--etapa navegador`)**: Playwright con Chromium a 360, 375 y 1200 px, y los topes de
  rendimiento. En Linux la fuente del sistema es Selawik (`.github/fuentes-linux.sh`, métricas de Segoe UI), como en Windows.
- **Cobertura combinada (`--etapa cobertura`)**: combina los datos de Linux y Windows (`.coveragerc`, rutas relativas) y
  exige `COBERTURA_MINIMA` (96 %).

## Producción (creador)
El portal real (1111) es lo único que usa el creador y está siempre prendido. No se toca sin su OK: nadie
despliega, hace rollback ni reinicia, ni toca
`~/LinkedOut/{produccion,datos,produccion.env}`, `portal.cmd`, `portal.vbs` ni las tareas `LinkedOut-portal` y
`LinkedOut-vigilante` y `LinkedOut-guardian`. Mientras exista `CONGELADO` en la ubicación vieja (o `~\LinkedOut\CONGELADO`), no
hay deploy. Lo nuevo se prueba en el 1199 (tests) o en el dev 1112 (datos copiados).
Se promueve solo con el OK del creador: backup 7z, versión `linkedout-estable-*`, deploy y rollback probado.

Desde el 17/11 producción tiene todo lo que era del dev (código, datos, sesiones y `.env`) y el dev quedó apagado. La marca `~\LinkedOut\produccion-activa` mantiene prendido el 1111: con ella, el vigilante (`~\LinkedOut\vigilante.ps1`, fuera del repo, cada 5 min) lo revive si se cae y el guardián lo respeta; sin ella, el vigilante no lo revive y el guardián lo apaga. El vigilante cierra solo el Edge de `LinkedOut\datos\linkedin_profile*` colgado y chequea el portal con `curl.exe --noproxy`. El guardián (`tools/guardian_procesos.py` del clon de producción, tarea `LinkedOut-guardian`, cada 10 min) apaga todo portal o Edge que no sea producción con su marca, el dev con la marca `~\LinkedOut\dev-activo` o un temporal vigente de `tools/temporal.py`, y lo anota en `~\LinkedOut\guardian.log`. Mientras corre la compuerta nocturna (marca `~\LinkedOut\compuerta-en-curso`) el vigilante no revive el 1111.

