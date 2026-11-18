# Workflow de agentes v2 (común a todos los proyectos)

Objetivo: no perder calidad y gastar la menor cuota posible.
- Claude: ahorro **máximo**.
- Codex: ahorro **medio**.
- AGY: **sin** ahorro.

Este texto es idéntico en todos los repos. La fuente es `~/.claude/agent-workflow/WORKFLOW.md`; cada repo tiene una copia en `docs/workflow-agentes.md` y los scripts en `tools/agents/`, instalados con `bash ~/.claude/agent-workflow/install.sh <repo>`. Lo único que cambia entre proyectos es `tools/agents/repo.env`.

La v2 (2026-11-05) agrega cuatro cosas:
- especificación y plan por feature;
- tests blindados y tests ocultos;
- revisión de la feature completa;
- métricas.

Además, Claude pasa a auditar por hito en vez de por microtarea. Se basa en patrones con evidencia publicada:
- planificador fuerte y ejecutor barato (architect/editor de Aider);
- revisor de otra familia de modelos;
- especificación, plan y tareas (Spec Kit);
- defensas contra el *reward hacking* (tests bloqueados y ocultos);
- costo por tarea aceptada (Arize).

## 0. Cómo pedirle trabajo a un agente (vale para cualquier Claude, Codex o AGY)
El agente optimiza lo que se mide: lo que no está en los tests no lo protege. Todo pedido (spec, misión, diagnóstico) lleva tres cosas:
1. **Invariantes:** qué no puede empeorar (jerarquía, tamaños, elementos protagonistas, comportamiento existente), con su valor base o una captura.
2. **Prioridad ante conflicto:** qué cede primero cuando dos reglas chocan. Antes de pedir, confirmá que las reglas numéricas se pueden cumplir juntas.
3. **Una medida por cada cosa que importa:** una aserción por invariante y por defecto, no solo por lo que no tiene que pasar.

Ejemplo (AdVenture, pantalla-v15, misión 09):
- El pedido decía qué no podía pasar (desbordes, menos de 11 px), pero no qué había que conservar.
- Codex cumplió los tests achicando el precio, que era el protagonista. Además, «tamaño actual = máximo» y «≥ 11 px» no entraban juntos a 360 px.
- Recién funcionó con invariantes, prioridad, capturas y una aserción por defecto (3 intentos).

## 0b. Cómo trabaja todo agente con el creador (memoria compartida)
Estas reglas valen para cualquier agente: Claude, Codex, AGY u otro.
- **La memoria vive en el repo.** Toda regla sobre cómo actuar en un proyecto (preferencias del creador, convenciones, decisiones operativas) se escribe en el repo: `AGENTS.md` del proyecto o este documento. Después se commitea y se sube al remoto. La memoria local de un agente es solo una copia: si no está en el repo, otro agente no la conoce.
- **Autonomía:** decidir y avanzar sin pedir confirmación en lo que el creador ya pidió. Se pregunta solo ante acciones irreversibles o externas, gastos, decisiones creativas o de producto reservadas al creador, o cuando falta un dato que no está en el repo.
- **Brevedad:** respuestas cortas, en español rioplatense (voseo). Primero el resultado, después lo que el creador tiene que hacer.
- **Bloqueos:** si un trabajo queda frenado más de unos 20 min (saturación, cuota, permisos, el kit), se le avisa al creador con opciones; nunca se espera en silencio.
- **Problemas del workflow:** cualquier conflicto con estas reglas o falla del kit (un permiso que falta, una compuerta que rechaza trabajo correcto, un script que se rompe, una regla que choca con el pedido) se le comunica enseguida a la sesión dueña del workflow, «Análisis workflow agentes», con el worktree, la misión y la línea del log. No se parchea a mano en silencio. Esa sesión además vigila sola todos los worktrees con `kit_monitor.sh` y actúa sin esperar el aviso. Su rol es el workflow (kit, reglas, permisos de AGY): qué tareas correr, con qué ejecutor y si esperar una cuota lo deciden los coordinadores con el creador.
- **Permisos de AGY:** los gestiona el workflow, no el creador. Si una misión necesita un comando seguro que no está en la allowlist, la sesión del workflow lo agrega (con respaldo de `settings.json`). Nunca se agregan comandos destructivos o externos (push, reset, rm, instalaciones globales, red) sin el creador. El loop chequea la allowlist antes de despachar y frena con la lista exacta si falta algo.
- **Mensajes entre sesiones:** cada mensaje gasta cuota de la otra sesión. Se escribe solo si el cambio afecta algo que la otra sesión tiene corriendo o le exige una acción concreta (relanzar, instalar, cambiar `repo.env`, editar misiones).

## 0c. Carriles: el tiempo de cada cambio depende de su tamaño y su riesgo (plan del creador, 07/11/2026)
El coordinador decide el carril al recibir el pedido y lo escribe en la misión (`Carril:`). También anota `Pedido:` (fecha y hora), así se mide el tiempo hasta el despliegue.

| Carril | Cuándo | Cómo | Meta |
|---|---|---|---|
| **rápido** | Hasta 2 archivos y unas 20 líneas; sin concurrencia, envío, migraciones ni seguridad. | Una misión con el cambio concreto. Ejecuta el que tenga cuota (Codex primero). Sin revisión de Codex: audita el coordinador sobre la compuerta. | ≤ 15 min |
| **normal** | Feature mediana sin S4. | Una misión por zona, con tests y código juntos. Una sola revisión de Codex; si pide cambios, se corrigen y alcanza con la compuerta. Sin holdout ni revisión de la feature. | ≤ 1 h |
| **s4** | Concurrencia, envío o aprobación, migraciones, seguridad. | Proceso completo: plan de Codex, tests aparte, hasta 2 revisiones, holdout y revisión de la feature con `--high`. | ≤ 3 h |

Una misión sin `Carril:` (anterior al 07/11/2026) sigue el proceso completo.

**Antes de despachar:** `mision_lint.sh <worktree> <misión>` (el loop también lo corre y sale con **8** si la misión está mal armada). Frena por:
- faltan campos;
- un carril que no corresponde al tamaño o a la clase;
- comandos fuera de la allowlist de AGY;
- textos que la misión cambia declarados con `Cambia el texto: «viejo» → «nuevo»` y que algún test existente fija sin estar en «Archivos:»;
- decisiones abiertas en la misión («requiere pregunta», «pendiente de autorización», «preguntá al», «a definir», «TBD» o «PREGUNTAS PARA EL CREADOR» sin responder);
- misiones de implementación para AGY sin «Tests heredados afectados:» (con la decisión para cada test que se rompe o «ninguno»).
Al encadenar con `feature.sh run`, todas las misiones pendientes se validan con `mision_lint.sh` antes de empezar: si alguna falla, genera `lint-plan.md` y sale con 8 sin ejecutar ninguna.

**Rama al día:** toda misión arranca con un merge de `BASE_BRANCH`. Si choca, sale con 8 y no cambia nada. El kit corre siempre desde la integración, y `tools/agents/**` y `docs/workflow-agentes.md` llevan `merge=ours`, así no generan conflictos.

**Una sola corrida de suite por cambio:** AGY y Codex corren solo los tests de su misión. Para ello, AGY dispone del comando fijo `bash tools/agents/mission_tests.sh` en sus comandos permitidos, que corre solo los tests de su misión vía `GATE_FILE_CMD`. La compuerta corre la suite una vez y deja el resultado con el commit (`_ref/compuerta-<commit>.md`). La auditoría lo reutiliza con `gate_status.sh <worktree>` mientras el árbol no cambie.

**`EXECUTOR=auto`:**
- Si AGY está sin cuota o saturado más de 10 min, ejecuta Codex.
- Si Codex está sin cuota, ejecuta AGY.
- Si ninguno tiene cuota, sale con 3 y el carril rápido lo puede hacer el coordinador.
- Solo se avisa, no se pregunta.

**Tablero:** `estado.sh` regenera solo, en cada arranque y salida del loop, el estado de todos los worktrees del repo: qué corre, desde cuándo, carril, ejecutor y último resultado, más los últimos cambios con sus minutos. Se escribe en `ESTADO_FILE` (`repo.env`; por defecto `~/.claude/agent-workflow/estado/<repo>.md`).

**Métricas:** `metricas/<repo>.csv` suma `carril` y `minutos_desde_pedido`.

## 1. Roles y modelos
| Agente | Modelo | Hace | No hace |
|---|---|---|---|
| **AGY** | `gemini-3.8-flash-high`. **Solo si está saturado** (503, «No capacity available») unos 10 min, esa ronda usa un respaldo de máximo razonamiento (`AGY_FALLBACK_MODELS`: Opus 4.6 Thinking, después Sonnet 4.6 Thinking) y la siguiente vuelve a Flash High. Nunca por cuota agotada. (Autorizado por el creador el 06/11/2026.) | Misiones de tests, de implementación y de lectura (exploración). | Tareas grandes; diseñar; elegir otro modelo. |
| **Codex** | `gpt-6.1-sol`, `medium` por defecto; `high` solo para el plan o la revisión de algo de concurrencia, seguridad o el camino de envío. | Planifica cada feature (diseño, contratos, microtareas, tests ocultos); revisa los tests y los diffs de AGY; revisa la feature completa; diagnostica escalamientos; genera imágenes. | Ejecutar tareas comunes. |
| **Claude** | — | Aclara con el creador, escribe `spec.md`, aprueba el plan, despacha, audita por hito (tests, navegador, capturas), integra, decide y lleva las métricas. | Programar, arreglar, diseñar, revisar código línea por línea; usar subagentes. |

## 2. Ciclo por feature
Una tarea **trivial** va directo al §4 con una sola misión: 1 o 2 archivos, sin clase S4 y sin decisiones de diseño. Todo lo demás pasa por estos pasos:

1. **Aclarar (Claude).**
   - Como máximo 3 preguntas al creador, solo si algo es ambiguo.
   - Escribe `spec.md` con:
     - objetivo;
     - alcance;
     - lo que queda **fuera** de alcance;
     - criterios de aceptación medibles.
2. **Explorar (AGY, misión de lectura).**
   - Archivos, funciones y llamadores relevantes, y los riesgos.
   - Salida: `_ref/exploracion.md`.
3. **Planificar (Codex).** Lee `spec.md`, la exploración y el repo (en solo lectura). Escribe `plan.md` con:
   - el diseño y los contratos (firmas, endpoints, textos exactos);
   - las microtareas ordenadas: contratos, después tests, después código;
   - el tipo y la clase de cada microtarea (§3);
   - los **tests de aceptación ocultos**, que van en `holdout/` de la carpeta de la feature (`$FEATURES_DIR/<feature>/`, fuera del repo). AGY nunca los ve.
   - Tras generar el plan y las misiones, Codex hace una segunda pasada (`PLAN_REVIEW`, por defecto 1) para volcar en `casos-limite.md` los riesgos no cubiertos que luego reciben los ejecutores en su contexto.
4. **Aprobar el plan (Claude).**
   - Lee `plan.md`, no el código.
   - Lo contrasta con `spec.md` usando `cobertura.md`: todo criterio de aceptación tiene que tener una microtarea o un test oculto. `plan.md` no menciona los tests ocultos, porque se copia al worktree de AGY.
5. **Ejecutar las microtareas (§4)**, en el orden del plan.
6. **Cerrar la feature (§6):**
   - se corren los tests ocultos;
   - Codex revisa la feature completa;
   - Claude hace la auditoría del hito.

## 3. Microtarea
- **Objetivo:** uno solo.
- **Archivos:** de 1 a 6. La misión los lista en una línea parseable: `Archivos: ruta/a, ruta/b`.
- **Feature:** el nombre de la carpeta de la feature. Antes de despachar, el loop copia `spec.md` y `plan.md` (nunca `holdout/`) a `<worktree>/_ref/feature/`, porque AGY no puede leer fuera de su worktree. Las misiones citan esas rutas.
- **Compuerta:** `total` (por defecto) o `incremental`. La incremental es solo para implementaciones intermedias de una feature con varios pares, donde otros tests siguen en rojo hasta que lleguen los pares siguientes. Queda en verde si pasan los «Tests bloqueados» de la misión (con `GATE_FILE_CMD`) y no aparece ningún fallo nuevo frente a la foto de fallos tomada antes de que AGY trabaje. La foto de fallos describe siempre el último commit: sale de la foto que dejó ese commit o, si no hay, de correr la compuerta en un checkout limpio. La compuerta corrida completa guarda su árbol y fallos normalizados para que commit_task los registre como foto base de la siguiente misión si el árbol de HEAD coincide. Nunca sale de restos sin commitear de un intento anterior. Los fallos se comparan completos, sin recortar. No cuentan las líneas de tests que pasan (`GATE_IGNORE`) ni las de resultado que dicen PASS sin ningún FAIL («contenedor justo: PASS»). Las fechas y horas ISO y las direcciones `0x…` se ignoran siempre al comparar. Con `FAIL_SPLIT`, una línea agregada («48 desbordes: a; b; c») se compara por partes: arreglar un elemento no crea un fallo nuevo, y uno que aparece sí. Con `FAIL_NUM_MASK=1` en `repo.env`, la comparación ignora los números de cada línea de fallo: una medida que mejora sin quedar perfecta sigue siendo el mismo fallo, y AGY igual recibe las medidas reales. La última implementación de la feature y el cierre (tests ocultos) exigen verde total.
- **Tipo:**
  - `tests`: escribe tests en rojo, sin tocar código de producción;
  - `implementación`: los tests de la misión quedan **bloqueados**;
  - `lectura`: no edita nada y escribe solo en `_ref/`.
- **Clase:** `normal` o `S4-<clase>` (§5).
- **Criterio de «hecho»:** medible.
- **Duración:** menos de unos 30 minutos.
- **UI:** un defecto por tarea, con medidas (px, selector, captura de referencia).
- **Si no entra en esto:** se parte antes de despachar.

Plantilla de misión (`.mision.md`):
```
# Misión: <título>
Feature: <spec.md / plan.md>
Tipo: tests | implementación | lectura
Clase: normal | S4-<clase>
Objetivo: <una frase>
Archivos: <ruta1>, <ruta2>
Tests bloqueados: <archivos de test que esta misión NO puede tocar>
Contrato: <firmas / endpoints / textos exactos>
Tests (rojos primero): <archivo::test> …
Hecho cuando: <criterio medible>
Comandos permitidos (exactos): <lista de la allowlist del repo>
Restricciones: <normas del repo>
```

**Invariantes en los tests:** en una misión de tests, las aserciones de invariante van marcadas (comentario `invariante`) y con la referencia base medida del estado actual. Con `INVARIANT_CHECK=1` en `repo.env`, el loop corre los tests de la misión con esa variable (el test corta antes de sus aserciones de defecto) y exige verde: si un invariante falla sobre la base, vuelve a AGY sin gastar Codex.

**Tests inestables:** si la compuerta queda en rojo con pocos fallos, el loop los repite una vez (solo los archivos que fallaron, o la compuerta entera si no se identifican). Lo que no reaparece se registra en `_ref/inestables.txt` y no cuenta, tanto en la compuerta total como en la incremental. Para arreglar la causa existe `Tipo: tests-fix`: edita tests existentes listados en «Archivos:» solo para hacerlos estables (esperas, carga de recursos). Exige compuerta en verde (incremental por defecto), y Codex revisa que ninguna aserción haya cambiado; el control de integridad sigue frenando aserciones borradas y marcas de skip. Si el test que se estabiliza es de un defecto todavía no implementado, la misión lleva `Defecto pendiente: sí`: sus invariantes tienen que pasar (con `INVARIANT_CHECK`), la parte de defecto puede seguir en rojo, y no puede aparecer ningún fallo nuevo en el resto.

**Granularidad (pedido del creador, 06/11/2026):** como máximo 8 misiones por feature, agrupadas por zona o archivo. En las clases `normal` y `S4-ui-visual`, los tests y el código van en una sola misión: el ejecutor escribe primero los tests y Codex revisa los dos. El par separado (misión «tests» + misión «implementación» con los tests bloqueados) queda para `S4-concurrencia`, envío/aprobación y migraciones. Los tests ocultos de la feature siguen siendo el control independiente.

Así AGY no escribe el examen y la respuesta en la misma misión.

### Modo solo AGY
- contrato exacto;
- hasta 3 archivos de producción y unas 80 líneas;
- los tests los escribe Codex, o van en una misión aparte, bloqueados;
- «Hecho cuando» con ids de test más compuerta total;
- nunca datos reales ni el puerto 1111;
- en planes nuevos, primero una misión de lectura de AGY, y el coordinador escribe las misiones sobre esa exploración.

## 4. Ciclo por microtarea (`tools/agents/review_loop.sh`, sin gastar Claude)
1. **AGY ejecuta** la misión (`agy_loop.sh`).
2. **Compuerta gratis.** En las misiones de implementación, cada ronda corre primero la **compuerta rápida**: solo los tests de la misión («Tests bloqueados» y tests tocados) con `GATE_FILE_CMD`, más el control de integridad. Si queda en rojo, vuelve a AGY sin correr la suite completa. Si queda en verde, se corre `GATE_CMD` completo antes de Codex (`FAST_GATE=0` en `repo.env` la desactiva). Cada ejecución de compuerta del kit (completa, rápida, foto base y holdout, incluido el QA que ejecuten) tiene un máximo de `GATE_TIMEOUT_MIN` (30 por defecto, en `repo.env` o el entorno; `GATE_TIMEOUT_SEC` tiene prioridad para tests): al vencer, el kit corta todo el árbol y registra «compuerta colgada» como fallo. El tiempo máximo efectivo se ajusta proporcionalmente a 4 veces la última duración sin corte (mínimo 300 s, tope `GATE_TIMEOUT_MIN`). AGY nunca corre la suite completa: el kit saca `GATE_CMD` de «Comandos permitidos» antes de mandarle la misión (el lint avisa), y si un comando de AGY queda más de 5 min sin dar señales (`AGY_CMD_STALL_SEC`, 300) se corta AGY con todo su árbol de procesos y la ronda sigue en la compuerta. Siempre se corre el **control de integridad**. Cualquiera de estos casos la pone en rojo y se marca como S3:
   - cambió un archivo protegido (`PROTECTED` en `repo.env`: tests, configuración del runner) que no está en `Archivos:` o que figura en `Tests bloqueados:`;
   - aparecieron marcas nuevas de `skip`, `xfail`, `only` o `todo`, o se borraron aserciones;
   - hay archivos fuera de `Archivos:`.
   - Al empezar, el loop saca una **foto base** del worktree. Lo que ya estaba cambiado antes de la misión (archivos sueltos del creador, por ejemplo) no cuenta para la integridad, no se commitea y no se borra: ni el reintento limpio ni la revisión UI lo tocan.
3. **Compuerta en rojo:** los fallos medidos vuelven a AGY. La devolución de la compuerta y el reintento limpio entregan la lista completa de fallos con su aserción `E`, separados entre tests de la misión y regresiones ajenas. No se llama a Codex.
4. **Compuerta en verde:** Codex revisa, como máximo 2 veces por microtarea.
   - Usa la checklist del §6 y su informe vuelve tal cual.
   - La primera línea es `VEREDICTO: APROBADO` o `VEREDICTO: CAMBIOS`.
   - **Lógica:** `-s read-only`. Revisa el diff y puede leer el repo para verificar los llamadores afectados.
   - **UI:** `-s workspace-write` con la instrucción «solo escribí en `_ref/`», porque corre el QA y saca capturas a 360, 375 y 1200 px.
5. **Commit:** con `APROBADO`, el loop commitea en la rama de la feature, un commit por microtarea con el título de la misión. Si la auditoría del hito rechaza algo, el coordinador revierte ese commit.
6. **Códigos de salida.** Claude despierta solo por estos códigos o por el watchdog:
   - `0`: aprobado y commiteado;
   - `2`: compuerta en verde, pero sin aprobación después de 2 revisiones. Decide el coordinador; **no** es una aprobación;
   - `3`: AGY sin cuota;
   - `4`: ni Codex lo logró;
   - `5`: Codex no pudo revisar;
   - `6`: se escaló por una señal;
   - `8`: misión mal armada (`mision_lint.sh`) o la rama no se pudo poner al día con la integración: no se gastó ninguna ronda;
   - `7`: AGY no dejó cambios y tuvo permisos denegados. Es una falla del sistema, no se escala: el coordinador ajusta la allowlist o la misión.

   Si la ronda quedó vacía porque AGY intentó leer o buscar con un comando de shell denegado (Select-String, grep, cat…), el loop lo reintenta gratis una vez con la corrección «usá tus herramientas de lectura», sin contarlo para S1. Si se repite, sale con 7.

   Una ronda de AGY **sin cambios** no corre la compuerta ni llama a Codex: vuelve a AGY y cuenta para S1. En best-of-2, un candidato sin cambios queda descartado antes de la compuerta.
7. **Fila de métricas** en cada salida (§9).

## 5. Escalar por señales, no por número de rondas

**Primero la causa, nunca el modelo (regla del creador, 10/11/2026).** Cuando AGY falla, antes de escalar se busca la causa: misión ambigua o incompleta, datos fuera de su worktree, permisos, compuerta o kit. Nunca se asume que falló por ser un modelo inferior. Si la causa es la misión o el kit, se corrige eso y no cuenta como falla de AGY.

Señales:
- **S1:** el mismo test o defecto falla en 2 rondas seguidas de AGY.
- **S2:** la cantidad de fallos de la compuerta no baja entre dos rondas.
- **S3:** el diff se sale de lo pedido o falla el control de integridad (§4.2).
- **S4:** la clase de tarea tiene historial bajo de AGY. Se trata **de entrada** con el plan de Codex y con best-of-2 (§7). Clases:
  - concurrencia o cancelación;
  - migraciones;
  - rutas de envío o aprobación;
  - cambios en muchos módulos;
  - ajuste visual de UI/CSS (píxeles, contención, tamaños). En este caso Codex diagnostica con medidas del navegador.

  La lista se ajusta con las métricas (§9).
- **S5:** el mismo defecto visual vuelve después de 2 rondas.

Cómo se escala:
0. **Reintento limpio (gratis):** ante S1 o S2, una sola vez, AGY corre en una sesión nueva solo con la misión y el último fallo, sin el historial. Si la señal se repite, se sigue con el paso 1.
1. **Codex diagnostica**, no implementa. Recibe el test que falla, el stderr, el diff y la misión. Devuelve la causa raíz y las microtareas con sus tests en rojo.
2. AGY implementa esas microtareas, con el ciclo normal.
3. Si la señal se repite después del diagnóstico, **Codex ejecuta** y, si la compuerta queda en rojo sin fallos de integridad, recibe una ronda de corrección solo de esos fallos (`CODEX_EXEC_FIX_ROUNDS` en `repo.env` o el entorno, por defecto 1; 0 desactiva la corrección).
4. Si Codex también falla, el coordinador decide:
   - si encontró una ambigüedad, reescribe la `spec.md` o la misión;
   - si la tarea es difícil de verdad, Claude implementa **solo con confirmación del creador**.
5. **No se escala** si la falla es del sistema: falta una herramienta, se negó un permiso (`denied_actions`) o falta un dato. Eso lo resuelve el coordinador.

## 6. Revisión
- Revisa siempre alguien que **no escribió** el código. Si escribió AGY, revisa Codex.
- **Si escribió Codex:** la revisión es la compuerta, más los tests ocultos, más la auditoría de Claude. No se le pide a AGY que revise a Codex.
- **Si Codex no tiene cuota:** la revisión queda en la compuerta, los tests ocultos y la auditoría de Claude, y el informe al creador aclara que faltó la revisión cruzada.

**Checklist de Codex para tests** (recibe la evidencia del rojo: el loop corre los tests de la misión con `GATE_FILE_CMD`). Además de APROBADO y CAMBIOS, puede responder «APROBADO CON MEJORAS»: el rojo es por el defecto correcto y solo quedan mejoras de endurecimiento. Se aprueba, y las mejoras van a `_ref/mejoras-test.md` y a `mejoras-pendientes.md` de la feature. Ese veredicto no existe en la revisión de implementación.
- ¿Prueban el contrato y los criterios de `spec.md`?
- ¿Pueden fallar? Ninguna aserción tiene que ser tautológica.
- ¿Cubren los bordes del contrato?

**Checklist de Codex para implementación:**
- ¿Cumple el contrato?
- ¿Hardcodea la salida que esperan los tests?
- ¿Debilita aserciones o configuración?
- ¿Rompe llamadores fuera del diff? (grep en el repo)
- ¿Cambia algo que no se pidió?

**Revisión de la feature (Codex, una vez por feature):**
- Revisa el diff completo `base...HEAD` contra `spec.md` y `plan.md`: coherencia entre microtareas, duplicación y huecos frente a los criterios de aceptación.
- Usa `high` si la feature tiene clase S4 de envío, seguridad o concurrencia.
- Antes de esta revisión se corren los tests ocultos. Si el worktree está limpio y la compuerta del commit pasó en 0 fallos, el holdout corre solo los tests ocultos con `GATE_FILE_CMD` sin repetir la suite completa (`HOLDOUT_FULL=1` fuerza la suite). Si alguno falla, el resultado es `CAMBIOS` directo.
- Desde la segunda revisión el prompt suma la revisión anterior, el diff incremental y la regla de mejoras no bloqueantes; a partir de la revisión `FEATURE_REVIEW_MAX + 1` (o con `--solo-p1`), Codex revisa en modo solo P1 (bloquea únicamente por criterios de spec incumplidos, bugs o seguridad/datos) y ya no sale con 6.

**Auditoría de Claude (auditor final, por hito o feature, no por microtarea):**
- nunca acepta solo por la compuerta en verde o por el «ok» de otro agente;
- en UI compara contra la última versión aprobada en 360, 375 y 1200 px; las capturas de cada microtarea las saca Codex en `_ref/`;
- la captura a 1200 px tiene que tener resolución suficiente para leer texto de 11 px;
- cualquier retroceso o cambio no pedido es rechazo, y se revierte el commit de esa microtarea.

## 7. Best-of-2 (solo S4 de concurrencia o envío/aprobación, y solo si AGY tiene cuota)
- `best_of_2.sh` se niega con cualquier otra clase: el doble costo solo paga donde un error es caro (pedido del creador, 06/11/2026).
- Dos AGY con la misma misión, cada uno en **su propio worktree**.
- La compuerta descarta al que quede en rojo.
- Si los dos quedan en verde, Codex revisa ambos diffs en una sola llamada, elige uno y lo justifica. Cuenta como una revisión.
- No aplica a migraciones: ahí se hace un solo intento, con más cuidado en el plan.

## 8. Cuota agotada
- **AGY (Flash High) sin cuota:**
  1. `agy_loop.sh` sale con 3. No hay fallback a otros modelos por cuota (el respaldo es solo para saturación 503, §1).
  2. El coordinador frena y le avisa al creador, que cambia de cuenta si tiene otra.
  3. Si no hay otra cuenta, el creador avisa y decide entre tres opciones:
     - esperar;
     - otro modelo de AGY con cuota, en su variante de máximo razonamiento: `AGY_MODEL=claude-opus-4-6-thinking` al despachar (así se hizo el 06/11/2026 con el visto bueno del creador);
     - Codex como ejecutor: `EXECUTOR=codex` o `--codex`.
     Con un modelo distinto de Flash, revisar las denegaciones: Opus intenta más comandos fuera de la allowlist. El loop reintenta con la corrección y el chequeo previo frena si falta algo declarado.
- **Codex sin cuota:** AGY ejecuta con `CODEX_REVIEW=diferida`, y nada de clase S4 llega a `BASE_BRANCH` sin un `feature.sh review --high` de Codex.
  - Una revisión vacía no cuenta como rechazo. Para una revisión pendiente, el loop puede esperar de a 30 minutos.
  - Para planificar, ejecutar o diagnosticar, el coordinador parte la tarea en microtareas para AGY, cada una con su test en rojo.
  - Si la tarea no se puede partir, Claude ejecuta **solo con confirmación del creador**.
- **Claude sin cuota:** no avanza nadie. Por eso su cuota es la última que se gasta.

## 9. Métricas (costo por tarea aceptada)
- El loop agrega una fila por microtarea a `~/.claude/agent-workflow/metricas/<repo>.csv` (fuera del repo, para no generar conflictos entre ramas):
  `fecha,repo,feature,mision,tipo,clase,ejecutor,rondas_agy,reintento_limpio,revisiones_codex,senal,salida,minutos,auditoria`
- Claude completa la columna `auditoria` en el hito: `metrics.sh audit <worktree> <feature> aceptada|rechazada`. El resumen por clase sale de `metrics.sh report <worktree>`.
- Cada unas 20 microtareas, Claude revisa el archivo:
  - una clase `normal` con menos del 70 % de aceptación en la primera pasada de AGY pasa a S4;
  - una clase S4 con más del 90 % sale de S4.
- Si una feature tiene más revisiones de Codex que microtareas, el plan estaba mal partido y se mejora en la próxima.

## 10. Permisos de AGY
- **Allowlist exacta** en `~/.gemini/antigravity-cli/settings.json`.
  - Es global: incluye los comandos de los dos repos.
  - El match es literal, sin glob, carácter por carácter.
  - La misión lista esos comandos exactos.
- AGY se despacha con `--mode accept-edits`: edita archivos, y los comandos quedan limitados a la allowlist. Nada de `--dangerously-skip-permissions` ni `--mode auto`.
- AGY no puede leer fuera de su worktree en modo headless (por ejemplo, el código de librerías instaladas). Por eso el plan copia en el «Contrato» de la misión las firmas de las APIs externas que hagan falta.
- Si AGY intenta un comando que no está en la lista, aparece `denied_actions` en `status.txt` y el watchdog avisa: la ronda «terminó bien», pero vacía.
- La carpeta de la feature (spec, plan, misiones y tests ocultos) queda fuera de cualquier worktree de AGY.

## 11. Vigilancia
- Cada despacho en segundo plano va con `tools/agents/watchdog.sh "nombre|log|carpeta|minutos"`. Es silencioso y sale solo ante un evento:
  - espera de stdin;
  - `denied_actions` o permiso negado;
  - cuota agotada;
  - error fatal;
  - N minutos sin cambios;
  - todos los trabajos terminados.
- Para `review_loop.sh` y `best_of_2.sh`, usá el **modo loop**: `watchdog.sh "nombre|<worktree>|loop|minutos"`.
  - Cuenta como actividad cualquier línea nueva de `run-loop.log` o `review-loop.log`, o un avance del stream de AGY (las rondas largas de lectura cuentan).
  - Termina solo con la salida final del loop («salida N»), no con el «salida=» de cada ronda de AGY.
- Un 503 de AGY («No capacity available», modelo saturado) no es cuota: `agy_run.py` lo registra como «AGY saturado (503)» y reintenta. A los 10 min pasa al respaldo (§1), y a los 20 min escribe «saturado hace más de 20 min», con lo que el watchdog despierta al coordinador para que avise al creador. Si la saturación supera 60 min, sale con 1 (no con 3). Las métricas registran la ronda como `agy(respaldo:<modelo>)`.
- El watchdog cuenta solo las líneas nuevas del log y da una gracia inicial de N minutos.
- AGY se despacha con `--output-format stream-json`: `_ref/run-loop.log` muestra una línea por paso en vivo (herramienta, archivo, estado), y un permiso denegado aparece al instante como `auto-denied`. El stream crudo queda en `_ref/agy-stream.jsonl`.
- Codex siempre corre con `codex exec … < /dev/null`.
- Nunca dos AGY en el mismo worktree: `review_loop.sh` y `best_of_2.sh` toman un candado (`_ref/loop.pid`) y se niegan a arrancar si hay otro loop vivo. Best-of-2 usa dos worktrees.
- **Sin huérfanos:** si el loop termina por cualquier motivo, corta su AGY. Best-of-2 tiene un tope de `BEST_OF_2_MAX_MIN` (90 min) y cada misión tiene un tope de `MAX_MISSION_MIN` (180 min), que escala con salida 6. Cada intento de AGY tiene un tope de 45 min. Si la sesión coordinadora murió de golpe, `cleanup.sh <worktree>` limpia lo que quedó.
- **Paralelismo:** las features independientes corren a la vez, cada una en su worktree con `feature.sh run` y su watchdog. AGY tiene cuota y se agota: «sin ahorro» significa usarla toda en trabajo útil, no que sea ilimitada. Los frentes en paralelo no pueden tocar los mismos archivos.
- **Relecturas:** el loop arma `_ref/mapa-archivos.md` (secciones con número de línea de los archivos grandes de la misión) y le pide a AGY que lea por rangos. `run-loop.log` registra «lecturas/ediciones» por ronda; el objetivo es 10:1 o menos.

## 12. Ahorro
**Claude:**
- `spec.md`, aprobar el plan y auditar el hito. No audita cada microtarea.
- Misiones en archivo con la plantilla, y prompts cortos que apuntan al archivo.
- Leer solo el código de salida, el `tail` y el informe final; nunca logs completos.
- Un despertar por evento.
- La investigación o exploración larga va a AGY en misiones de lectura, salvo pedido del creador.
- Capturas reducidas, excepto la auditoría a 1200 px.
- Respuestas breves.

**Codex:**
- `medium` por defecto.
- Los gastos que suma la v2 (plan, revisión de tests, revisión de feature) son pocos por feature y compran la calidad que antes dependía de Claude.
- Revisión de microtarea solo con la compuerta en verde, como máximo 2.
- Diagnóstico antes que implementación.

**AGY:**
- Sin ahorro (su cuota se usa entera en trabajo útil, pero se agota): exploración, tests, implementación, reintento limpio y best-of-2.

## 13. Imágenes
Las genera Codex. En buscatrabajo4 no aplica.

## 14. Comandos (`tools/agents/`)
| Paso | Comando | Quién lo lanza |
|---|---|---|
| Crear la spec | `bash tools/agents/feature.sh new <worktree> <feature>` (después Claude la completa) | Claude |
| Explorar | `review_loop.sh <worktree> <mision-lectura.md>` (salida en `_ref/exploracion.md`) | Claude |
| Planificar | `feature.sh plan <worktree> <feature> [--high]` → `plan.md`, `misiones/`, `holdout/` | Claude |
| Microtarea | `review_loop.sh <worktree> <mision.md> [--revisar-primero] [--codex]` | Claude, en segundo plano y con watchdog |
| Todas las misiones de la feature | `feature.sh run <worktree> <feature> [--high]`: encadena las misiones sin despertar al coordinador, frena en la primera salida distinta de 0, retoma donde quedó y al final corre `review` | Claude, en segundo plano con `watchdog.sh "nombre\|<worktree>\|loop\|min"` |
| Limpiar huérfanos | `cleanup.sh <worktree> [...]`: corta el AGY y el loop que hayan quedado corriendo y libera el candado | Claude |
| Best-of-2 | `best_of_2.sh <worktree1> <worktree2> <mision.md>` | Claude |
| Tests ocultos | `feature.sh holdout <worktree> <feature>` | Claude |
| Cerrar la feature | `feature.sh review <worktree> <feature> [--high]` (holdout + revisión de Codex contra `BASE_BRANCH`) | Claude |
| Métricas | `metrics.sh audit <worktree> <feature> aceptada\|rechazada` · `metrics.sh report <worktree>` | Claude |
| Vigilar | `watchdog.sh "nombre\|log\|carpeta\|minutos"` | Claude |

- Las misiones viven fuera del worktree, por ejemplo en la carpeta de la feature. Si estuvieran dentro, el control de integridad las contaría como cambios.
- Cada worktree necesita `_ref/` en `.gitignore`.
- Para la regla de «salida 2», ver el §4.6.

**Proyecto nuevo:**
1. `bash ~/.claude/agent-workflow/install.sh <repo>`.
2. Completar `tools/agents/repo.env` (`GATE_CMD`, `PROTECTED`, `BASE_BRANCH`).
3. Sumar `GATE_CMD` a la allowlist de AGY.
4. Commitear.

**Actualizar el kit en un repo:** volver a correr `install.sh`, que no pisa `repo.env`.

**Tests del kit:** `bash ~/.claude/agent-workflow/tests/run.sh`, con AGY y Codex simulados (no gastan cuota).

**Para evaluar (opcional, sin adoptar):**
- Claudexor (orquestador con adaptador para AGY, revisión entre familias de modelos y rotación de cuentas), en una tarea descartable;
- antes de automatizar más a AGY, revisar los términos de uso de la CLI de Antigravity.

## Por repo
Todo lo que cambia entre proyectos está en `tools/agents/repo.env`:
- `GATE_CMD`, `GATE_GREP` y `GATE_RESTORE`;
- `PROTECTED`;
- `BASE_BRANCH`;
- `HOLDOUT_CMD`;
- `AUDIT_NOTE`;
- las variables de entorno extra.

La allowlist de AGY es global, en `~/.gemini/antigravity-cli/settings.json`.
