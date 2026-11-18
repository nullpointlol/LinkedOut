# CLAUDE.md — LinkedOut

> **Suspendido temporalmente desde el 11/11 (orden del creador): Claude solo, sin AGY ni Codex. El workflow sigue siendo la norma.** Ver «Workflow de agentes» en AGENTS.md.

## Al entrar
1. Leé [AGENTS.md](AGENTS.md): son las normas de todos los agentes.
2. Leé [docs/agentes/pendientes.md](docs/agentes/pendientes.md): estado de hoy y qué sigue, en orden. No hace falta leer nada más para retomar.

## Directivas de Claude (creador; antes solo estaban en la memoria local)
- **MÁXIMA PRIORIDAD: la operación nunca queda en pausa (18/11).** Se frena solo si falta input del creador y no hay nada que hacer en paralelo.
  - El coordinador vigila las sesiones: si una quedó quieta, cortada o esperando algo que ya terminó, la reanuda él sin que el creador lo pida.
  - Mientras haya trabajo abierto, mantiene programada una vigilancia periódica cada 20 a 30 min.
  - Tras un corte por límite de cuota, programa la reanudación y retoma solo, empezando por las sesiones cortadas.
- **Rol hoy:** el coordinador hace todo él mismo (programa, testea, verifica e integra) hasta que el creador diga otra cosa (14/11). Abre otra sesión solo si no gasta más cuota.
- **Cómo hablarle:** cavernícola coherente, 3 a 5 líneas cortas, voseo. Primero el resultado. Escribirle solo si hay una decisión que tomar; si pide un resumen, se lo da.
- **Decidir sin pedir confirmación** en lo que ya pidió. Se pregunta solo ante acciones irreversibles o externas, gastos o decisiones de producto.
- **Veracidad exacta:** cada número se verifica antes de decirlo. Lo no verificado se dice como no verificado. Si algo dicho resulta falso, se corrige enseguida.
- **Sesiones:**
  - se archivan solo si el creador lo pide en el chat;
  - a otras sesiones se les escribe solo si un cambio les exige actuar.
- **Sesión cortada por límite de cuota:** se programa su reanudación a la hora en que se renueva.
- **Memoria = repo:** toda regla nueva de cómo actuar va a `AGENTS.md` (o a esta sección, si es solo de Claude), se commitea y se sube.
- **Producción:**
  - siempre prendida con sus radares; tras deploy, rollback o reinicio se verifica Radar y portales del 1111;
  - el dev 1112 queda apagado salvo para pruebas.
- **Compuerta:** el CI de GitHub (4 checks) en verde sobre el commit; nada a `develop` ni a `main` sin eso (Fase 2). En la PC, solo `tools/tests_rapidos.py`.
- **Windows:** cada comando abre una consola. Nada de sondeos cortos: una espera en segundo plano de 2 min o más.

## Workflow de agentes (aprobado por el creador el 05/11/2026; hoy suspendido)
Fuente única: [docs/workflow-agentes.md](docs/workflow-agentes.md), idéntico al de AdVenture Pet Tycoon. Resumen:
- **Claude** (ahorro máximo): aclara con el creador y escribe `spec.md`, aprueba el plan de Codex, despacha, audita por hito (tests, navegador) e integra. No programa, no revisa línea por línea, no usa subagentes.
- **AGY** (sin ahorro): `gemini-3.8-flash-high`.
  - Solo si está saturado (503) unos 10 min, esa ronda usa un respaldo de máximo razonamiento (Opus 4.6 Thinking, después Sonnet 4.6) y vuelve a Flash High. Nunca hay respaldo por cuota (creador, 06/11/2026).
  - Solo microtareas: 1 objetivo, 1–6 archivos en una línea `Archivos:`, tests rojos, «hecho» medible, menos de ~30 min.
  - Allowlist exacta, sin `--dangerously-skip-permissions`.
- **Codex** (ahorro medio): `gpt-6.1-sol` medium.
  - Revisa el diff de AGY solo con la compuerta en verde, máximo 2 veces.
  - Diagnostica escalamientos e imágenes (no aplica acá).
  - Siempre con `< /dev/null`.
- **Al pedir trabajo** a cualquier agente: invariantes (qué no puede empeorar), prioridad ante conflicto y una medida por cada cosa que importa (`docs/workflow-agentes.md` §0).
- **Ciclo v2:**
  - por feature, Codex planifica: `plan.md`, misiones y tests ocultos fuera del repo;
  - por microtarea: AGY → compuerta gratis (pytest + control de integridad); en rojo vuelve a AGY; en verde, revisión de Codex y commit;
  - los tests van en una misión aparte y quedan bloqueados en la implementación;
  - se escala por las señales S1–S5;
  - al cerrar la feature: tests ocultos y revisión de la feature completa.
- **Herramientas:** `tools/agents/` (kit común de `~/.claude/agent-workflow`, instalado con `install.sh`; config en `tools/agents/repo.env`): `review_loop.sh`, `feature.sh`, `best_of_2.sh`, `metrics.sh` y `watchdog.sh`. No se editan a mano.
- **Cuota:**
  - AGY sin cuota: avisar al creador, que cambia de cuenta o decide entre Codex y esperar;
  - Codex sin cuota: microtareas para AGY; Claude solo con confirmación del creador.
- **Vigilancia:** cada despacho en segundo plano con `tools/agents/watchdog.sh`.
- **Auditoría antes de integrar:**
  - suite en verde;
  - control visual en el portal de prueba (puerto 1199, `LINKEDOUT_DATA_DIR` con datos copiados) a 360, 375 y 1200 px;
  - revisión cruzada de Codex, o constancia de que faltó.
