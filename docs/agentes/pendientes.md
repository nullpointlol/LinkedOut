# Pendientes de LinkedOut (estado al 17/11/2026, 17:30)

Es la única planificación vigente. La lee cualquier agente que retome el proyecto; las reglas están en `AGENTS.md`.
Al cerrar un ítem se borra de acá. Los planes viejos están en la historia de git (y en `~\LinkedOut\archivo\docs-agentes-hasta-20261117`).
Además, solo `docs/agentes/migracion-repo.md` (el ítem 8).

**Trabajo en curso (desde el 17/11): el plan «LinkedOut público» en `docs/agentes/plan-publico.md`** (limpieza, nombre, repo público con CI, rediseño y README). Manda sobre los ítems de abajo cuando se pisan; la migración (ítem 8) pasa a ser su Fase 2.

## Cómo está hoy
- **Producción (1111) es lo real y está siempre prendida** desde el 17/11 03:24.
  - Tag `linkedout-estable-20261117-2` (0bc1ecd), con todo lo que era del dev: datos, sesiones de LinkedIn y `.env`.
  - Radar CORRIENDO: feed, búsquedas, análisis, generación y envíos, con mail real.
  - Los siete portales corriendo. RemoteOK, Remotive y Himalayas, desde el 17/11 11:01, con las dos búsquedas en inglés.
  - Rutas: clon `~\LinkedOut\produccion`, datos `~\LinkedOut\datos`, `produccion.env` y `portal.log`.
  - Tareas: `LinkedOut-portal`; vigilante cada 5 min; guardián cada 10 min (`guardian.log`).
- **Dev (1112): apagado.** Solo para pruebas: `tools\dev.ps1 -Iniciar`; el guardián lo apaga sin la marca `dev-activo`.
- **Compuerta nocturna habilitada** (03:00):
  - apaga producción, corre la suite completa sobre develop y las ramas pendientes, y levanta producción con sus radares (`~\LinkedOut\compuerta-produccion.ps1`; detalle en `docs/despliegue.md`);
  - registro en `~\LinkedOut\compuerta\noche.log`, informe en `AAAAMMDD.md` y `ultimo.json`;
  - el CI de GitHub no corre (sin minutos).
- **Ramas en origin:** `develop`, `main` y las de trabajo abiertas.
  - El 17/11 se borraron 8 ramas cuyo contenido ya estaba en develop. Sus SHA están en el mensaje del commit «docs: planificación completa…» del 17/11 (`git log --grep "planificación completa"`); se recrean con `git push origin <sha>:refs/heads/<rama>`.
- **Guardado:** se guarda todo. La única excepción son las ofertas ANALIZADA de más de 30 días (ítem 4).
  - Copias del corte: `~\LinkedOut\datos-hasta-20261114` y `~\LinkedOut\archivo\` (unos 3,4 GB).
  - Backup 7z: `backups_locales\linkedout-local-20261117-015536.7z`.
- **Vuelta atrás de producción:**
  - `tools\rollback.ps1 -Produccion -A linkedout-estable-20261114`;
  - cambiar `datos` por `datos-hasta-20261114` y devolver `produccion.env.hasta-20261114`;
  - se pierde lo hecho desde el corte.
  - La base de antes del deploy quedó en `datos\backups\postulaciones-20261117-032406-antes-de-deploy.db`.

## Pendientes, en orden
Nada de esto se ejecuta hasta que el creador lo pida (17/11: «No la vamos a ejecutar ahora»), salvo los controles 1 y 2.

1. **Control de 24 h de producción** (hasta el 18/11 ~03:30). Revisar:
   - `portal.log` sin errores nuevos;
   - el Radar con lecturas, análisis y envíos del día;
   - `vigilante.log` sin reinicios, salvo la compuerta;
   - `guardian.log` sin apagados inesperados;
   - sin CPU sostenida en el 1111.
   Si algo falla: rollback (arriba).
2. **Primera compuerta con producción apagada** (18/11 03:00). Revisar:
   - `noche.log`: «producción apagada», «compuerta terminada» y «producción levantada: Radar CORRIENDO»;
   - `/api/portales/estado` con los siete corriendo.
   Si `pendientes-20261117` sale en verde, se mergea a develop (docs y directivas).
3. **La compuerta con producción apagada, dentro del repo.**
   - Problema: hoy vive en `~\LinkedOut\compuerta-produccion.ps1`, fuera del repo y sin tests, y el vigilante respeta la marca `compuerta-en-curso` también fuera del repo.
   - Hacer: pasar el apagado y el levantado a `tools/compuerta_nocturna.ps1`, con tests de config simulada y `HOME` de prueba (nunca la producción real); `-Registrar` apunta la tarea ahí.
   - Mismo arreglo de paso: `deploy.ps1` pone `CI=1` solo para su compuerta local (hoy falla sin la variable; el 17/11 se pasó a mano), con test en `tests/test_despliegue_produccion_nueva.py`.
   - Invariantes:
     - producción nunca queda apagada al terminar, ni si la suite falla;
     - el vigilante revive el 1111 si la marca tiene más de 3,5 h.
   - Medida: un test simula la falla de la suite y el 1111 vuelve; un deploy sin variables pasa la compuerta.
4. **Purga de ANALIZADAS viejas** (creador, 17/11).
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
5. **Tamaño de las bases en el mismo panel.**
   - Qué muestra: MB de cada base de `<DATA_DIR>` (`postulaciones.db` con su `-wal`, `radar.db`, `buscatrabajo.db`) y de `backups\`. El 17/11: `postulaciones.db` 75 MB, `backups` 177 MB.
   - Va en un desplegable cerrado por defecto, debajo de los toggles y el estado de los procesos, que siguen siendo lo principal.
   - Medida: test de la API con archivos de tamaño conocido; control visual en el 1199 a 1200 px.
6. **Cuatro portales nuevos** (aprobados por el creador el 17/11, del gist de recursos de LeaTex).
   - Los portales:
     - Get on Board (API pública, tecnología LatAm);
     - We Work Remotely (RSS);
     - Working Nomads (API JSON);
     - PyAr Trabajo (página simple).
   - Mismo circuito que RemoteOK, Remotive y Himalayas (`core/reglas_portales.py`, un radar por portal, duplicados).
   - Antes de programar cada uno, verificar que la API o el RSS siga vivo, sin captcha ni Cloudflare (nunca se esquivan) y que las condiciones del sitio lo permitan. Si no, se descarta y se le avisa al creador.
   - Entran apagados, con las reglas en `reglas_portales.json` del perfil. El creador decide prenderlos y con qué búsquedas.
   - Medida: tests con respuestas grabadas (sin red); una consulta real por portal en el 1199.
7. **Varias cuentas de AGY para el portal** (después; el creador las conecta cuando lo pida).
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
8. **Migración al repo limpio** (después del ítem 1). Plan completo en `docs/agentes/migracion-repo.md`, con sus ajustes del 17/11. Los pasos en GitHub, solo con el OK del creador. Sesión con el contexto: «Repo limpio LinkedOut: plan de migración».
9. **Mejoras menores** (de revisiones viejas; verificar si siguen antes de tocarlas):
   - `tests/test_perfil_areas_creador.py::test_datos_reales_del_creador` lee datos reales: pasarlo a una fixture inventada;
   - `tests/test_rendimiento_dos_workers.py::test_pausa_cancelacion_y_stop_con_dos[stop]` y `[descartar]` fallaron a veces en paralelo (09/11): esperas por condición;
   - `tests/test_rescate_capturas.py::test_recapturar_sync_devuelve_el_post_principal` dependía del orden (09/11);
   - el mail puede sumar detalles que no están en la base (09/11, «monitoreo 24x7»): falta una guarda como la del CV;
   - `clean_and_map` en `core/aprendizajes.py` parece código muerto.
   - `tests/test_rendimiento_auditoria.py` (17 tests) falló en la suite en paralelo del 18/11 y pasó sola (26 de 26): depende del orden o de estado compartido bajo xdist. Aislarla.
   - `test_la_vista_radar_con_la_base_grande_se_dibuja_a_tiempo` tardó 4245 ms (tope 4000) con producción prendida; sola dio 3048 ms.
10. **Archivar sesiones** solo cuando el creador lo pida: «Radar viejo» comparte worktree con la de coordinación y va con ella.

## Carpetas de trabajo
Las sesiones trabajan en worktrees de `~\Downloads\Projects\buscatrabajo4\.claude\worktrees\`, siempre con una rama nueva desde `origin/develop`. Los worktrees `bt4-*` y la carpeta principal (rama `master`) son de antes de la reescritura del historial del 11/11: tienen cambios locales viejos, **nunca se suben** (traen historia con datos personales) y no se usan.
