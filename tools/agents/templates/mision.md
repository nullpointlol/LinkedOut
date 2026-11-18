# Misión: <título>
Feature: <nombre exacto de la carpeta de la feature, o «—» si es suelta; el loop copia su spec.md y plan.md a _ref/feature/>
Carril: <rápido (≤2 archivos y ~20 líneas, sin S4: sin revisión de Codex) | normal (tests y código juntos, 1 revisión) | s4 (proceso completo)>
Pedido: <fecha y hora del pedido del creador, AAAA-MM-DD HH:MM: mide el tiempo hasta el despliegue>
Tipo: <tests | implementación | tests-fix (arregla tests existentes inestables sin cambiar aserciones) | lectura>
Clase: <normal | S4-concurrencia | S4-migraciones | S4-envio | S4-multimodulo | S4-ui-visual>
Revisión: <lógica | ui>
Defecto pendiente: <solo en tests-fix: «sí» si el test que se estabiliza es de un defecto todavía no implementado (sigue en rojo; sus invariantes tienen que pasar)>
Compuerta: <total | incremental (solo implementaciones intermedias de una feature: alcanza con que pasen los «Tests bloqueados» y no aparezcan fallos nuevos)>
Objetivo: <una frase>
Archivos: <ruta1>, <ruta2>
Tests bloqueados: <archivos de test que esta misión NO puede tocar, o «ninguno»>
Tests heredados afectados: <una línea por test existente que el cambio rompe: «tests/x.py::test_y — actualizar expectativa» o «— corregir código»; si no rompe ninguno: «ninguno»>
Contrato: <firmas / endpoints / textos exactos>
Cambia el texto: «texto viejo» → «texto nuevo» <una línea por texto visible que cambia; el lint verifica que sus tests estén en Archivos:>
Conservar: <invariantes de la spec que toca esta misión, con su valor base; cada uno tiene una aserción en los tests>
Si hay conflicto: <qué cede primero, copiado de «Prioridad ante conflicto» de la spec>
Tests (rojos primero): <archivo::test> …
Hecho cuando: <criterio medible>
Comandos permitidos (exactos): <lista de la allowlist del repo>
Restricciones: <normas del repo>

## Notas para el ejecutor
- Trabajá solo en los archivos de «Archivos:». Si necesitás otro, no lo toques: explicá por qué en tu respuesta final.
- No modifiques, saltees ni debilites tests. No hagas commit.
- Tests heredados afectados: para tests existentes que el cambio rompe, respetá la decisión («actualizar expectativa» o «corregir código»). Si dice actualizar expectativa, actualizá la aserción en el test; si dice corregir código, ajustá tu implementación.
- Pasar los tests no alcanza: no empeores nada de «Conservar:». Si dos reglas no entran juntas, aplicá «Si hay conflicto:» y explicalo en tu respuesta final; nunca sacrifiques un invariante en silencio.
- Si te niegan un comando, no abandones ni lo reformules: seguí con tus herramientas de lectura y terminá la misión.
- Para leer y buscar usá SOLO tus herramientas (view_file, grep_search, find_by_name, list_dir). Nunca comandos de shell como Select-String, Get-Content, grep, cat, ls o dir: se deniegan y la ronda se pierde. Los únicos comandos que podés correr son los de «Comandos permitidos».
- No leas archivos fuera de tu worktree (librerías instaladas, site-packages, node_modules globales, carpetas del usuario): en modo headless se deniegan y la ronda se pierde. Lo que necesites de una API externa está en «Contrato» o en el código del repo que ya la usa; si falta, decilo en tu respuesta final.
- Una misión de lectura escribe su resultado solo en `_ref/`.
- Misión de tests: separá las aserciones de **invariante** (marcadas con un comentario `invariante`) de las de **defecto**. Las referencias base de los invariantes se miden del estado ACTUAL, no se inventan. Con la variable de entorno `INVARIANT_CHECK=1`, el test corre solo los invariantes y tiene que pasar HOY: cortá antes de las aserciones de defecto con un `if`/`return` (por ejemplo `if (process.env.INVARIANT_CHECK) return;`), nunca con `skip`. El rojo tiene que venir solo de las aserciones de defecto.
