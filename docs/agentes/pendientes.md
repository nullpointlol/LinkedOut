# Pendientes de LinkedOut (estado al 19/11/2026, 02:30)

Es la única planificación vigente. La lee cualquier agente que retome el proyecto; las reglas están en `AGENTS.md`.
Al cerrar un ítem se borra de acá. Los planes viejos están en la historia de git.

**Trabajo en curso: el plan «LinkedOut público» en `docs/agentes/plan-publico.md`.** Manda sobre los ítems de abajo cuando se pisan. **Producción va al final de todo** (creador, 19/11: «Cuando esté todo, vamos a producción»), con su prueba en el dev 1112. No se le pregunta antes.

## Cómo está hoy
- **Repo público** `nullpointlol/LinkedOut` (desde el 18/11). El viejo quedó privado como `BT4_old`, y los clones viejos tienen un hook que bloquea el push al nuevo.
  - Clon de trabajo: `~/Downloads/Projects/LinkedOut`.
  - Todo entra por PR con los 4 checks del CI en verde (`suite`, `windows`, `navegador`, `cobertura`) más CodeQL.
  - Lo que va al repo público lo revisa y lo sube el coordinador: diff, autores noreply, `auditar_datos.py --local/--ci`, gitleaks y cada imagen a ojo (AGENTS.md, «Flujo»).
  - `main` sigue en la semilla (e073ad8). Dependabot mira `main`, así que sus 30 alertas de pypdf se cierran solas cuando develop pase a main, en el release; develop ya tiene pypdf 6.21.4.
- **Producción (1111): siempre prendida, con el Radar y los siete portales.**
  - Sigue en el tag `linkedout-estable-20261117-2` (0bc1ecd) y su origin apunta a `BT4_old`. El paso 8 del plan (repunte al repo nuevo y deploy) va al final.
  - Rutas: clon `~\LinkedOut\produccion`, datos `~\LinkedOut\datos`, `produccion.env` y `portal.log`.
  - Tareas: `LinkedOut-portal`; vigilante cada 5 min; guardián cada 10 min (`guardian.log`).
- **Dev (1112): apagado.** Solo para pruebas del creador: `tools\dev.ps1 -ActualizarCodigo -RefrescarDatos -Iniciar` (copia los datos, mail en modo prueba). Al terminar, `-Detener`. El dev viejo quedó en `~\LinkedOut\dev-bt4old-20261118`.
- **Compuerta nocturna: archivada** el 18/11 (tarea `LinkedOut-compuerta` deshabilitada, script en `~\LinkedOut\archivo\`). La compuerta es el CI de GitHub; producción ya no se apaga de noche.
- **Guardado:** se guarda todo. La única excepción son las ofertas ANALIZADA de más de 30 días (ítem 1).
- **Vuelta atrás de producción:** `tools
ollback.ps1 -Produccion -A linkedout-estable-20261117-2`. La base de antes del último deploy está en `datos\backups\`.

## Pendientes, en orden
Después del plan público, salvo que el creador pida otro orden.
1. **Purga de ANALIZADAS viejas** (creador, 17/11).
   - Regla: las ofertas en estado ANALIZADA con más de 30 días desde que se obtuvieron (`created_at`) pasan a descartadas. Ningún otro estado se toca: ni LISTO, ni ENVIADO, ni IGNORADA, ni los demás.
   - UI:
     - botón en el panel «Historial de procesos» (`static/index.html`, `static/radar.js`) que purga;
     - confirmación en el mismo botón, igual que «Descartar» (primer clic «¿Confirmar…?», plazo `MS_CONFIRMAR_DESCARTE`);
     - muestra cuántas va a purgar antes de confirmar.
   - Backend: endpoint con `_solo_operacion_local` y backup de la base antes de escribir.
   - Invariantes:
     - solo ANALIZADA y solo más de 30 días;
     - la regla de 30 días es una constante con nombre;
     - nada se borra de la base: cambia el estado.
   - Decisión pendiente, que el creador toma al ejecutar: si además se vacía el contenido pesado (`texto`, `texto_pagina`, `imagenes`) para liberar espacio.
   - Medida: test con base de prueba y conteos por estado antes y después; solo cambian las ANALIZADA viejas.
   - Dato del 17/11: hoy hay 0 que cumplan (la base arranca el 02/11; las primeras cumplen el 02/12).
2. **Tamaño de las bases en el mismo panel.**
   - Qué muestra: MB de cada base de `<DATA_DIR>` (`postulaciones.db` con su `-wal`, `radar.db`, `buscatrabajo.db`) y de `backups\`. El 17/11: `postulaciones.db` 75 MB, `backups` 177 MB.
   - Va en un desplegable cerrado por defecto, debajo de los toggles y el estado de los procesos, que siguen siendo lo principal.
   - Medida: test de la API con archivos de tamaño conocido; control visual en el 1199 a 1200 px.
3. **Cuatro portales nuevos** (aprobados por el creador el 17/11, del gist de recursos de LeaTex).
   - Los portales:
     - Get on Board (API pública, tecnología LatAm);
     - We Work Remotely (RSS);
     - Working Nomads (API JSON);
     - PyAr Trabajo (página simple).
   - Mismo circuito que RemoteOK, Remotive y Himalayas (`core/reglas_portales.py`, un radar por portal, duplicados).
   - Antes de programar cada uno, verificar que la API o el RSS siga vivo, sin captcha ni Cloudflare (nunca se esquivan) y que las condiciones del sitio lo permitan. Si no, se descarta y se le avisa al creador.
   - Entran apagados, con las reglas en `reglas_portales.json` del perfil. El creador decide prenderlos y con qué búsquedas.
   - Medida: tests con respuestas grabadas (sin red); una consulta real por portal en el 1199.
4. **Varias cuentas de AGY para el portal** (después; el creador las conecta cuando lo pida).
   - Objetivo: ampliar la capacidad del portal repartiendo los llamados entre cuentas, no esquivar límites de Google.
   - Hoy: tope de 2 llamados a la vez (`LINKEDOUT_AGY_MAX_CONCURRENTES`, `core/bridge.py`). El 17/11 hubo 13 análisis caídos por `failed to start conversation: backend returned an empty conversation id`.
     - Un análisis caído no se pierde: el código arma un respaldo por reglas (`StrategistAgent._deterministic_fallback`), pero el Radar lo trata como fallido y lo vuelve a poner en cola para analizarlo de nuevo con AGY.
     - Los 24 posts con alguna falla en `portal.log` terminaron analizados (13), ignorados (10) o listos (1).
   - Hallado: AGY tiene la variable `AGY_ACCOUNT` para elegir entre cuentas conectadas. Con una sola cuenta la ignora, así que no está probada.
   - Hacer:
     - el creador conecta una segunda cuenta;
     - se verifica que `AGY_ACCOUNT` elija la cuenta;
     - `bridge.py` reparte entre cuentas, con 2 a la vez por cuenta y descanso de una cuenta ante errores de saturación;
     - lo mismo para el kit de agentes (`~/.claude/agent-workflow`).
   - Medida: análisis caídos por día (`datos\agy_errores.log`) y tiempo en cola de las ofertas.
5. **Mejoras menores** (de revisiones viejas; verificar si siguen antes de tocarlas):
   - `tests/test_perfil_areas_creador.py::test_datos_reales_del_creador` lee datos reales: pasarlo a una fixture inventada;
   - `tests/test_rendimiento_dos_workers.py::test_pausa_cancelacion_y_stop_con_dos[stop]` y `[descartar]` fallaron a veces en paralelo (09/11): esperas por condición;
   - `tests/test_rescate_capturas.py::test_recapturar_sync_devuelve_el_post_principal` dependía del orden (09/11);
   - el mail puede sumar detalles que no están en la base (09/11, «monitoreo 24x7»): falta una guarda como la del CV;
   - `clean_and_map` en `core/aprendizajes.py` parece código muerto.
   - `tests/test_rendimiento_auditoria.py` (17 tests) falló en la suite en paralelo del 18/11 y pasó sola (26 de 26): depende del orden o de estado compartido bajo xdist. Aislarla.
   - `tests/test_ui_scoring_movil.py::test_detalle_con_brecha_fuerte_condicion_y_me_descarta_se_ve_entero[1200]` falla a veces con «Element is not attached to the DOM»: `renderDetalle` reescribe todo el detalle cuando llega el texto completo.
   - `tests/test_rendimiento_api.py` falló una vez con carga en la suite no-ui completa y pasó sola.
   - Después del deploy final: `portal.cmd` pasa a `LINKEDOUT_*` y se borra el código de compatibilidad con `BUSCATRABAJO_*` (S4).

6. **Archivar sesiones** solo cuando el creador lo pida: «Radar viejo» comparte worktree con la de coordinación y va con ella.

## Carpetas de trabajo
- Clon de trabajo del repo público: `~/Downloads/Projects/LinkedOut`. Las ramas salen de `origin/develop`, en worktrees bajo `.claude/worktrees/` de ese clon.
- `~/Downloads/Projects/buscatrabajo4` y sus worktrees son del repo viejo (`BT4_old`, privado): solo lectura. El hook bloquea el push al repo nuevo.
