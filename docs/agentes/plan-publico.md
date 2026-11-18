# Plan vigente: LinkedOut público (aprobado por el creador el 17/11/2026)

## Sesiones y estado (lo actualiza el coordinador en cada cambio)
Coordinador: la sesión «LinkedIn job scraping y envío automático» (rama `pendientes-20261117`).
Si la cuota corta, otra cuenta retoma desde las mismas sesiones: leer esta tabla y el `ESTADO.md` de cada rama.
Las sesiones avisan al coordinador al finalizar cada turno (AGENTS.md, «Flujo»), y el coordinador las vigila cada 25 min y reanuda la que esté quieta.

| Sesión | Qué | Rama | Estado | Próximo paso |
|---|---|---|---|---|
| S1 Seguridad (`local_d9b75d8b`) | guardas de rutas, clipper, viewport | `s1-seguridad` (ddd3e1e) | en develop desde el 17/11 22:30 (suite completa, navegador y rendimiento en verde; cobertura 96,05 %) | — |
| S2 Datos personales (`local_074fbbf8`) | cuasi-identificadores, plantilla, `tools/auditar_datos.py` | `s2-datos-personales` (937a1fe) | en develop desde el 17/11 22:30 (suite completa, navegador y rendimiento en verde; cobertura 96,05 %) | — |
| S3 Purga y docs (`local_bdfe452f`) | legado, docs consolidados, LICENSE MIT | `claude/xenodochial-lamarr-aeb600` (a65d208) | en develop desde el 17/11 22:30 (suite completa, navegador y rendimiento en verde; cobertura 96,05 %) | — |
| Rediseño: plan (`local_4046a541`) | plan y prototipos mobile-first, Mi perfil | `rediseno-plan` (7e1542e) | plan aprobado el 17/11 con las 3 decisiones (abajo); se implementa después de la Fase 2 | entrega `docs/rediseno/plan.md` para aprobar |
| S5 Quitar Herramientas (`local_c31c74c5`) | menú, modal, 4 herramientas y su código | — | en develop desde el 18/11 01:10 (suite completa, navegador y rendimiento en verde; cobertura 96,06 %) | — |
| App de escritorio (`local_2fb588be`) | plan de app para la PC (PWA) y acceso de otros dispositivos | ídem | PWA en develop desde el 18/11 01:35 (suite completa, navegador y rendimiento en verde al primer intento; cobertura 96,07 %) | — |
| Vulnerabilidades (`local_6fcf9062`) | auditoría de CVE, estático y secretos; plan de controles para el CI | ídem | 3 altas y 4 medias arregladas, Pillow 13 instalado en la PC; en develop desde el 18/11 01:35 (suite completa, navegador y rendimiento en verde al primer intento; cobertura 96,07 %) | — |
| S4 Nombre (`local_c33a4ec8`) | `BUSCATRABAJO_*` → `LINKEDOUT_*` con compatibilidad | `claude/s4-nombre-linkedout` (bc2422a) | en develop desde el 18/11 09:50 (a3b67f0; suite completa, navegador y rendimiento en verde; 4532 pasaron) | — |
| Repo limpio (`local_18e5dd2f`) | Fase 2: semilla, GitHub público, CI | `claude/nifty-goodall-55d0c3` | Fase 2 despachada el 18/11: pasos 1–3 y preparar 5–7 | frena antes del paso 4 (público) y del 8 (producción): OK del creador |

**Autorización del creador (17/11, noche), vigente hasta terminar todo este plan:** «La PC es toda para vos hasta finalizar todos los procesos. Podés cerrar el portal cuando necesites, ejecutar la suite completa también a necesidad, sin programar nada. Tenés todo libre para implementar.»
- El coordinador corre la suite completa cuando hace falta, sin esperar la compuerta nocturna, y puede apagar producción mientras corre.
- Al terminar, siempre levanta producción con sus radares.
- Las sesiones implementan sin esperar más OK lo que ya tiene plan aprobado. Toda tarea nueva (app de escritorio, vulnerabilidades) primero entrega su plan y el creador lo aprueba (creador, 17/11: «debés hacer una planificación primero»).
- Lo externo e irreversible (hacer público el repo en GitHub) se le confirma al creador en una línea antes de hacerlo.

Las ramas de cada sesión deben salir de `origin/develop` (0bc1ecd). Si un worktree nació de otra base, antes de editar se lleva a `origin/develop`.
Backup del 17/11 antes de empezar: ver `backups_locales/` (informe `.txt` al lado del `.7z`).


## Contexto
El creador quiere publicar LinkedOut como repo **público**:
- sin ningún dato personal en ningún archivo, rama, commit ni comentario;
- documentación clara y sin lo viejo;
- el nombre LinkedOut en todas partes;
- tests y compuerta en GitHub Actions, que en repos públicos no tiene límite de minutos;
- un **rediseño mobile-first** minimalista, con Mi Perfil reorganizado de cero. Va como planificación aparte; la base es `~\Downloads\Projects\# md\guia_redisenio_uiux_LinkedOut.md`;
- un README breve y no técnico, con capturas (desktop y celular) y un CV de ejemplo sin sus datos.

**Decisiones del creador (17/11):**
- Licencia MIT.
- El historial viejo queda en `BT4_old` como archivo privado.
- Los docs de agentes van al repo público, saneados.

**Estado medido (17/11):**
- **Árbol actual: 485 archivos.** No hay identificadores directos (nombre, mail, teléfono, LinkedIn, empleadores), pero quedan cuasi-identificadores:
  - en `docs/perfil.md`: el empleador real con sus fechas;
  - en `tests/test_estructura_cv.py`: la cronología real 2007–2025;
  - en `tests/test_mobile_and_length.py`: la escuela real;
  - logros parafraseados en ~15 tests y en `fixtures-casos.json`;
  - el título del CV real dentro de `templates/cv_template_executive.docx`, en `docProps/app.xml`;
  - 19 rutas absolutas de la carpeta de usuario de la PC;
  - dos mails de páginas reales (kopiustech, empleosit).
- **El historial está sucio:** el CV real está en el commit raíz. Por eso se publica desde **un solo commit semilla**, como ya planea `docs/agentes/migracion-repo.md`.
- **Nombre viejo:**
  - `buscatrabajo` aparece 201 veces en 65 archivos;
  - hay 8 variables `BUSCATRABAJO_*`; `portal.cmd` de producción usa `DATA_DIR` y `ENV_FILE`;
  - la marca `bt4_sesion_ok` está viva en la sesión de LinkedIn;
  - el h1 «buscatrabajo4» está fijado por tests.
- **Seguridad, lo que se vuelve público con el código:**
  - unas 15 rutas que modifican datos no tienen guarda: `DELETE /api/ofertas/{id}`, `POST /api/model-config`, `regenerate`, `expand/*` y otras;
  - todos los GET con datos del candidato están abiertos en la LAN;
  - el clipper responde con `Access-Control-Allow-Origin: *`;
  - `run.py` siempre abre un túnel público de Cloudflare. Producción no usa `run.py`: arranca con uvicorn directo desde `portal.cmd`.
- **CI:** `ci.yml` ya corre Linux, Windows, navegador y cobertura sin secretos, y los tests andan en los runners de GitHub. Solo le falta el disparador por push o PR y `permissions`. No hay LICENSE.

**Invariantes (no pueden empeorar):**
- Producción 1111 sigue andando con sus radares; solo se toca con deploy y con el OK del creador.
- Los datos reales, `produccion.env` y las sesiones de LinkedIn no se tocan.
- Paridad funcional total: ninguna acción, filtro, estado ni puntaje cambia de efecto.
- El contenido de las ofertas es inmutable.
- Nada se borra de los datos; lo viejo se archiva.

**Prioridad ante conflicto:** producción, después cero datos personales, después la paridad, y al final la limpieza o la estética.

## Paso 0. Backup y continuidad (antes de despachar)
- **Backup 7z completo en local** con `tools/backup_local.ps1`. Control: el informe sin advertencias y `7z t` en OK.
- **Continuidad entre cuentas** (la cuota se está por acabar; el creador puede cambiar de cuenta en las mismas sesiones):
  - este plan va al repo como `docs/agentes/plan-publico.md`, con la tabla de sesiones: nombre, id, rama, estado y próximo paso;
  - `pendientes.md` apunta a ese plan;
  - commit y push a `pendientes-20261117` en cada cambio de estado;
  - cada sesión despachada deja en su rama un `ESTADO.md` corto (qué hizo, qué falta, cómo verificar) y lo actualiza al cerrar cada paso, así otro agente retoma desde la misma sesión;
  - la memoria se actualiza: la regla del celular queda reemplazada, y se agrega la del repo público con el CI como compuerta tras la Fase 2.

**Decisiones del creador (17/11, noche):**
- **App de escritorio:** PWA desde Edge sobre `localhost:1111`, como entrega chica apenas S5 esté en develop. En el celular sigue el QR + «Agregar a pantalla de inicio».
- **Vulnerabilidades:**
  - las 3 altas y las 4 medias se arreglan antes de publicar;
  - las librerías del navegador van copiadas en `static/vendor/`;
  - Dependabot, secret scanning y CodeQL se activan en la Fase 2;
  - el Python global de la PC se actualiza (pip, setuptools, pyasn1) y después se corre la suite.
    - Hecho el 17/11: pip 26.1.2 → 26.3, setuptools 78.1.0 → 84.2.1 y pyasn1 0.6.4 → 0.6.5, en el site de usuario; `pip check` sin problemas. Falta la suite, que corre con la integración de S5.
    - Para volver atrás: `pip install --user pip==26.1.2 setuptools==78.1.0 pyasn1==0.6.4`.
- **No hace falta SonarQube ni OWASP Dependency-Check:**
  - CodeQL (gratis en repos públicos) cubre el análisis estático de seguridad;
  - `pip-audit` y Dependabot cubren las dependencias de Python (Dependency-Check y Maven son del mundo Java);
  - la auditoría ya revisó las categorías del OWASP Top 10.

## Fase 1. Limpieza en el repo actual (develop), con 4 sesiones
Cada sesión trabaja en un worktree nuevo desde `origin/develop`, con TDD, `tools/tests_rapidos.py` y push de su rama. La compuerta nocturna prueba esa rama con producción apagada. Cuando sale en verde, el coordinador audita y mergea. Las sesiones S1, S2 y S3 corren en paralelo, con archivos que no se pisan.

**S1. Seguridad para publicar**
- Archivos: `server.py`, `core/radar_api.py` y sus tests.
- Todas las rutas que no son GET llevan `_solo_operacion_local`: solo la PC o un celular emparejado.
- Los GET con datos del candidato también: `/api/candidato/*`, `/api/ofertas*`, exportes, `/api/stream`.
- El clipper sin `*`: solo los orígenes propios.
- Se quita `user-scalable=no` del viewport.
- Se corrige de paso un bug: `borrar_aprendizaje` respalda todo el `DATA_DIR`.
- Medida: un test recorre `app.routes` y falla si una ruta de escritura o de datos queda sin guarda; los tests del celular emparejado siguen en verde.

**S2. Cero datos personales**
- Archivos: tests y fixtures afectados, `docs/perfil.md`, `tools/derivar_plantilla.py`, la plantilla `.docx` y una herramienta nueva.
- Reemplaza por datos inventados:
  - la cronología, la escuela y los logros parafraseados;
  - el empleador de `docs/perfil.md`;
  - los mails de páginas reales;
  - la localidad real de la lista de partidos, que se mantiene completa y genérica.
- `derivar_plantilla.py` también vacía `docProps/app.xml`; se regenera la plantilla.
- Nueva `tools/auditar_datos.py`:
  - en la PC, lee los términos sensibles de `<DATA_DIR>/candidato`, que nunca se versionan;
  - recorre todo el árbol, el interior de los `.docx` y los mensajes de commit de la semilla;
  - en el CI corre solo con patrones genéricos: mails de dominios no de prueba, teléfonos, rutas `C:\Users\`, claves;
  - reemplaza el test angosto `test_sin_datos_candidato_en_codigo.py`.
- Medida: `auditar_datos.py` da 0 hallazgos sobre el árbol, con los términos reales cargados.

**S3. Purga y documentación clara**
- Archivos: docs, scripts viejos, `AGENTS.md`, `CLAUDE.md` y `tools/`. No toca `docs/perfil.md`.
- Purga, con su test si existe:
  - `run.py`, `iniciar_tunel.py`, `iniciar_portal.bat`, `iniciar_escritorio.bat`, `crear_acceso_directo.bat` y `core/desktop.py`. El acceso del celular por LAN no depende de ellos; se verifica;
  - los one-off `scripts/*` y `tools/restaurar_descartados.py`;
  - `docs/linkedin-limites.md` y `docs/recalcular_afinidad.md`;
  - las referencias rotas.
- Consolida:
  - `docs/normas-workflow.md` pasa a `AGENTS.md`;
  - `docs/tests-por-endpoint.md` se fusiona en `codigo-y-tests.md`.
- Los docs de agentes van saneados:
  - rutas como `~\LinkedOut`, nunca absolutas de la carpeta de usuario;
  - sin datos operativos sensibles;
  - `pendientes.md` y `migracion-repo.md` genéricos.
- Unifica los marcadores de la suite (`aislado` contra las etapas de `verificar.py`).
- `docs/despliegue.md` y `tools/restaurar_local.md` quedan sin rutas personales.
- Agrega el archivo `LICENSE` (MIT).
- Medida:
  - ningún archivo sin referencias;
  - un test de enlaces internos de docs en verde;
  - `AGENTS.md` sin contradicciones con `CLAUDE.md`.

**S5. Quitar el menú «Herramientas»** (pedido del creador, 17/11)
- Va después de mergear S1–S3 y antes de S4, porque S4 ya no tendrá que renombrar el bookmarklet.
- Las 4 herramientas son autónomas: ninguna función viva importa sus módulos ni llama a sus endpoints. Son la captura en 1 clic (bookmarklet y clipper), preparar entrevista, mensajes de networking y pre-flight check.
- **Se quita:**
  - **UI:**
    - en `static/index.html`, el menú (`#contenedorMenuToolkit` … `#menuToolkitDropdown`) y `#modalToolkit` entero, y se sube el `?v=` de `app.js`;
    - en `static/app.js`: el listener de clic afuera, las llamadas a `renderPreflightCheck` en `renderOferta` y `limpiarDetalleOferta`, el bloque del toolkit (`toggleToolkitMenu` … `renderPreflightCheck`, incluido `copiarTexto`) y sus `window.*`.
  - **Backend, en `server.py`:**
    - los imports;
    - el middleware CORS del clipper (y `Response`, si queda sin uso);
    - `ClipperIn` y `POST /api/ofertas/clipper`;
    - `GET /api/toolkit/bookmarklet`;
    - los 4 endpoints `toolkit/*` con sus alias;
    - la normalización `interview_prep` y `networking_pitches` del detalle.
  - **Módulos:** `core/bookmarklet.py`, `core/interview_prep.py` y `core/networking.py`.
  - **Tests completos:** `test_toolkit_ui`, `test_toolkit_e2e_integration`, `test_bookmarklet_clipper`, `test_interview_prep`, `test_networking_pitches`, `test_unit_interview_prep` y `test_unit_networking`.
    - Antes de borrarlos se mueven dos controles útiles: que `init_db` usa WAL va a `test_unit_database.py`, y la guarda genérica «sin CORS `*`» va a `test_guardas_rutas.py` de S1.
  - **Tests que se editan:**
    - `test_endpoints_server.py`, `test_manual_motor.py`, `test_cv_completo_base.py`, `test_ux_sin_jerga_y_ajustes.py`, `test_jerarquia_texto.py` y `test_ui_botones.py`;
    - `inventario_botones.json`, que se regenera;
    - las referencias al clipper y al toolkit en `test_guardas_rutas.py` de S1.
  - **Docs:** las filas del toolkit en el doc de tests por endpoint y el bookmarklet en S4. La sesión del rediseño saca el toolkit de `docs/rediseno/mapa-acciones.json`.
- **Se queda:**
  - el menú Ajustes (`toggleAjustesMenu`) y el Escape que cierra «Nueva oferta»;
  - `descargarDocx`, `descargarPdf` y `remediarCVAuditoria`, que usa el editor;
  - el Auditor anti-IA/ATS (`core/auditor.py`), que no depende del toolkit;
  - `_crear_post_manual` y `/api/ofertas`, `/url` y `/batch` (Nueva oferta);
  - el estado `EN_ENTREVISTA`;
  - las columnas `interview_prep` y `networking_pitches` de la base, con sus datos.
- **Invariantes:**
  - ninguna acción viva cambia de efecto;
  - los datos de la base quedan intactos;
  - la cobertura no baja de 96 %: si baja, se agregan tests al código vivo, nunca se baja el piso.
- **Riesgos:**
  - el margen de cobertura queda justo (~96,0 %);
  - `test_ui_navegador` exige 40 o más botones pulsados por ancho;
  - los bookmarklets que ya están en el navegador van a dar 404, porque el clipper ya no existe.
- **Medidas:**
  - `git grep -E "toolkit|clipper|bookmarklet|interview_prep|networking|preflight"` solo encuentra `core/database.py`, `test_unit_database.py` y el fixture;
  - `node --check static/app.js` pasa;
  - la suite de UI pasa a 375 y 1200 px;
  - `tools/verificar.py` llega al 96 % o más.

**S4. Nombre LinkedOut**
- Va **después** de mergear S1–S3, porque toca casi todo.
- Variables:
  - `BUSCATRABAJO_*` pasa a `LINKEDOUT_*`;
  - el código lee las nuevas y, mientras exista, también la vieja, porque `portal.cmd` y `dev-lanzador.cmd` de producción usan la vieja (regla «datos reales con el deploy»).
- La marca `bt4_sesion_ok`: se leen las dos y se escribe la nueva.
- El candado se renombra, y los dos nombres se respetan durante la transición.
- Se renombran:
  - el título de la app, el h1 y `<title>`;
  - el banner NATIVO;
  - los prompts y el User-Agent;
  - `repo.env`;
  - el prefijo de los temporales de pytest;
  - y se actualizan los tests que fijan el h1.
- «Antigravity» queda solo donde nombra la herramienta AGY, nunca como marca del proyecto.
- Medida: `git grep -i -E "buscatrabajo|bt4|nativo"` vacío, salvo en el código de compatibilidad marcado, que se borra en la Fase 2.

## Fase 2. Publicación y CI
La lleva la sesión existente «Repo limpio LinkedOut: plan de migración», que tiene el contexto, siguiendo `migracion-repo.md` con estos cambios.
1. **Precondiciones:**
   - Fase 1 en develop;
   - backup 7z verificado;
   - `auditar_datos.py` con 0 hallazgos sobre `develop^{tree}`;
   - gitleaks sobre la semilla, si se puede instalar.
2. **Semilla:** un commit con autor y committer del mail noreply de GitHub de la cuenta, nunca el mail personal, y un mensaje sin datos.
3. **Guardas antes de tocar GitHub:** `set-url` y un hook `pre-push` en todos los clones viejos. Se prueban con un bare local.
4. **GitHub** (con el OK del creador en ese momento):
   - renombrar a `BT4_old`, que queda privado;
   - crear `nullpointlol/LinkedOut` **público**;
   - subir solo `develop` y `main` desde la semilla.
5. **CI público:**
   - `ci.yml` con `push` y `pull_request`, más `permissions: contents: read`;
   - la descarga de Selawik fijada por hash;
   - reglas en `develop` y `main` que exigen los checks `suite`, `windows`, `navegador` y `cobertura`.
6. **Nueva norma, en `AGENTS.md`, `CLAUDE.md` y la memoria:** «nada a develop sin el CI en verde» reemplaza la suite local y la compuerta nocturna. Se deshabilitan la tarea `LinkedOut-compuerta` y `compuerta-produccion.ps1` (se archivan, no se borran). Producción ya no se apaga de noche.
7. **Deploy:** `deploy.ps1` exige un run verde del SHA en el repo público y se arregla `CI=1` en la vía local.
8. **Producción, con el OK del creador:**
   - el clon pasa al repo nuevo: `fetch` y `reset` a la semilla, mismo árbol, sin reiniciar;
   - después, el deploy de la versión limpia;
   - recién entonces se cambia `portal.cmd` a `LINKEDOUT_*` y se quita la compatibilidad vieja (S4).
9. **Clon nuevo de trabajo:** `~\Downloads\Projects\LinkedOut`. Ahí se copian `.claude/settings.local.json` y la memoria, y las sesiones nuevas abren ahí. Las ~30 ramas viejas quedan solo en `BT4_old`.

## Fase 3. Rediseño mobile-first (planificación aparte)
- **Sesión «Rediseño LinkedOut: plan»:** arranca ya, en paralelo con la Fase 1. Solo lee el código y prototipa fuera del repo.
- **Entrega su propio plan, para la aprobación del creador.** Debe cubrir:
  - inventario completo de vistas y estados;
  - auditoría UX;
  - tokens: la paleta de la guía, una tipografía y una familia de íconos;
  - decisión de stack, como hipótesis: seguir sin framework y cambiar Tailwind CDN por un CSS propio con tokens, sin migrar de framework;
  - **Mi perfil reorganizado por tareas:**
    - Mis datos, con formularios en lugar de texto crudo;
    - Lo que sé;
    - Qué busco: roles, dónde y cómo trabajo, búsquedas editables;
    - Mi CV y mi mail;
    - Automatización (avanzado, plegado);
    - Lo que aprendió el sistema;
    - guardado único con indicador de cambios;
  - flujo mobile lista → detalle → mail, sin perder funciones;
  - estrategia para los ~45 tests de UI que fijan textos e ids, incluido el inventario de botones;
  - prototipos navegables a 375 y 1200 px para aprobar.
- **Se implementa en el repo público después de la Fase 2**, por etapas y con paridad funcional verificada.
- La regla vieja «nada de diseño para celular» queda reemplazada. Se actualizan la memoria y `AGENTS.md`.

**Decisiones del creador sobre el rediseño (17/11, «Sí, las 3»):**
1. Estilos: CSS propio con tokens, sin paso de compilación (se va Tailwind CDN).
2. Búsquedas editables desde Mi perfil: sí, como etapa aparte (E6), después de la paridad.
3. Nombres visibles nuevos: la tabla completa de `docs/rediseno/plan.md` §8 aprobada; los valores internos no cambian.

## Fase 4. README (después del rediseño aprobado)
- **Herramienta `tools/capturas.py`** (Playwright):
  - levanta un portal temporal en el 1199 con el candidato inventado (`config/ejemplo`, completado con los 4 archivos que le faltan) y ofertas ficticias sembradas;
  - captura cada vista a 1200 y 390 px, en `docs/img/`.
- **CV de ejemplo** (`docx` y `pdf`), con `core/docx_generator.py` y `core/pdf_generator.py` sobre el candidato inventado. Los metadatos se controlan con `auditar_datos.py`.
- **README:**
  - qué es;
  - qué hace, en 3 pasos;
  - capturas desktop y celular;
  - CV de ejemplo;
  - instalación en 5 pasos;
  - aviso sobre las condiciones de uso de LinkedIn;
  - licencia.
  Una página.

**Reglas del creador para las capturas y el CV de ejemplo (17/11):**
- Las ofertas y el candidato de ejemplo son de rubros lo más ajenos posible al creador: desarrollo, administración, gastronomía, salud, logística… **Nunca análisis funcional, soporte ni monitoreo.** Cuanto más distinto, mejor.
- Tamaños: celular (390 px), tablet (768 px) y escritorio (1280 px).
- Cada captura es una pantalla, del alto del viewport. Nada de capturas de página completa con scroll interminable.
- No aparece el aviso de error de Gmail ni ningún otro aviso de error: el portal de capturas se configura para que no los tenga.

## Coordinación de sesiones
- **El coordinador** (esta sesión) despacha con brief, invariantes, prioridad y medida. Audita por diff, tests y navegador, mergea y actualiza `pendientes.md`. No programa lo que delega.
- **En paralelo ya:** S1, S2, S3 y «Rediseño: plan». Los archivos están repartidos arriba: `AGENTS.md` y `CLAUDE.md` solo los toca S3, `docs/perfil.md` solo S2, `server.py` solo S1.
- **Después:** S4 cuando S1–S3 estén en develop; la Fase 2 por mensaje a la sesión «Repo limpio»; la Fase 4 al final.
- Las sesiones archivadas quedan así; ninguna se archiva sin el pedido del creador.
- Una sesión frenada más de 20 min avisa con opciones.

## Verificación
- **Fase 1:**
  - compuerta nocturna en verde por rama;
  - test de rutas guardadas;
  - `auditar_datos.py` en 0;
  - grep de nombres viejos vacío;
  - control visual del 1199 a 375 y 1200 px;
  - producción sin cambios: Radar y portales corriendo.
- **Fase 2:**
  - `rev-list --count` de la semilla igual a 1;
  - árbol idéntico a `develop^{tree}`;
  - repo público con 2 ramas y 1 commit;
  - CI en verde sobre la semilla;
  - push de prueba desde un clon viejo, BLOQUEADO;
  - producción sana después del repunte y del deploy;
  - una búsqueda en GitHub del nombre y el mail del creador en el repo público, sin resultados.
- **Fases 3 y 4:**
  - plan y prototipos aprobados por el creador;
  - suite de UI en verde;
  - paridad de acciones, con el inventario de botones;
  - capturas sin datos reales, revisadas a ojo y con `auditar_datos.py`.
