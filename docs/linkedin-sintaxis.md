# Sintaxis de la búsqueda de publicaciones de LinkedIn

Qué acepta de verdad la búsqueda de contenido de LinkedIn (ordenada por fecha, última semana), medido con la
cuenta 2 y `tools/sintaxis_linkedin.py`, de a una búsqueda y dentro del presupuesto (6 por hora). Cada prueba
guarda captura y HTML en `<DATA_DIR>/radar_debug/sintaxis/`. «Funciona» = trajo al menos una publicación.

La validación de `core/radar_consultas.py` (`validar_consulta`, `LARGO_MAXIMO`, `SEPARADOR_BLOQUES`) se
ajusta a esta tabla: una búsqueda que LinkedIn no acepta no se puede guardar.

Antecedente (11/11, Radar con la cuenta principal): `"cv a"` sola trajo 86 publicaciones y un OR de dos
frases en inglés trajo 29; las 3 búsquedas largas con OR y AND anidados terminaron «sin resultados» en tres
corridas seguidas.

## Conclusión (12/11)

- **El límite es de 5 operadores escritos (AND, OR y NOT): con 6 o más, «sin resultados»**, aun en
  consultas cortas (8 palabras con OR en 89 caracteres: cero) y sin importar la cantidad de términos (8
  términos con 5 OR: 24 publicaciones; 7 términos con 6 OR: cero). Los AND cuentan (4 OR + 2 AND: cero;
  3 OR + 2 AND: 21); el AND implícito (espacio) no. `validar_consulta` rechaza más de `MAX_OPERADORES = 5`.
- **Sin explicar todavía:** `"cv a"` sola trajo 18 el 11/11 y 0 en tres pruebas del 12/11, igual que
  `("cv a" OR "enviar cv")` sola; con un segundo bloque (rol o ubicación) sí traen. Hasta entenderlo,
  una búsqueda de un solo bloque con esas frases puede terminar en cero (y abrir un ticket).
- Funcionan: 2 y 3 bloques con OR (AND implícito), AND anidado dentro de un OR, NOT y un bloque de 4
  frases con OR. `or` en minúscula también trae resultados, pero la validación exige mayúsculas.
- El largo no fue el factor en lo medido (hasta ~140 caracteres con 5 OR).

## Pruebas

| Qué mide | Consulta | Resultado | Posts | Fecha |
|---|---|---|---|---|
| Frase exacta sola | `"cv a"` | no funciona (sin_resultados) | 0 | 2026-11-12 07:00 |
| Dos palabras sin comillas | `enviar cv` | pendiente | | |
| OR simple, sin paréntesis | `"cv a" OR "enviar cv"` | pendiente | | |
| OR entre paréntesis | `("cv a" OR "enviar cv")` | no funciona (sin_resultados) | 0 | 2026-11-12 06:31 |
| AND escrito | `"cv a" AND funcional` | pendiente | | |
| AND implícito (espacio) | `"cv a" funcional` | pendiente | | |
| Bloque OR + una palabra | `("cv a" OR "enviar cv") funcional` | pendiente | | |
| Dos bloques OR (AND implícito) | `("cv a" OR "enviar cv") (funcional OR soporte)` | funciona | 21 | 2026-11-11 17:43 |
| Tres bloques OR | `("cv a" OR "enviar cv") (funcional OR soporte) (Argentina OR LATAM)` | funciona | 24 | 2026-11-12 03:10 |
| AND anidado dentro de un OR | `"cv a" ((soporte AND aplicaciones) OR helpdesk)` | funciona | 21 | 2026-11-11 17:49 |
| NOT de una palabra | `"cv a" NOT pasantía` | funciona | 15 | 2026-11-11 18:03 |
| NOT de una frase exacta | `"cv a" NOT "sin experiencia"` | pendiente | | |
| Cantidad de términos: 6 frases con OR | `("cv a" OR "enviar cv" OR "enviá tu cv" OR "cv al mail" OR "cv al correo" OR "mandá tu cv")` | funciona | 21 | 2026-11-11 17:55 |
| Largo: unos 200 caracteres, dos bloques de 6 | `("cv a" OR "enviar cv" OR "enviá tu cv" OR "cv al mail" OR "cv al correo" OR "mandá tu cv") (funcional OR soporte OR helpdesk OR monitoreo OR incidentes OR analista)` | no funciona (sin_resultados) | 0 | 2026-11-11 18:00 |
| Largo: unos 300 caracteres, tres bloques con AND anidado | `("cv a" OR "enviar cv" OR "enviá tu cv" OR "cv al mail" OR "cv al correo" OR "mandá tu cv") (funcional OR (business AND analyst) OR (product AND owner) OR (mesa AND ayuda) OR helpdesk) (Argentina OR CABA OR "Buenos Aires" OR LATAM)` | pendiente | | |
| or en minúscula | `"cv a" or "enviar cv"` | funciona | 18 | 2026-11-12 03:17 |
| 6 frases + bloque de 2 (8 términos, 6 OR) | `("cv a" OR "enviar cv" OR "enviá tu cv" OR "cv al mail" OR "cv al correo" OR "mandá tu cv") (funcional OR soporte)` | no funciona (sin_resultados) | 0 | 2026-11-12 02:53 |
| 2 frases + bloque de 6 (8 términos, 6 OR, más corta) | `("cv a" OR "enviar cv") (funcional OR soporte OR helpdesk OR monitoreo OR incidentes OR analista)` | no funciona (sin_resultados) | 0 | 2026-11-12 02:58 |
| 6 frases + bloque de 4 (10 términos, 8 OR) | `("cv a" OR "enviar cv" OR "enviá tu cv" OR "cv al mail" OR "cv al correo" OR "mandá tu cv") (funcional OR soporte OR helpdesk OR monitoreo)` | no funciona (sin_resultados) | 0 | 2026-11-12 03:05 |
| 8 palabras con OR, corta (7 OR, ~70 caracteres) | `(funcional OR soporte OR helpdesk OR monitoreo OR incidentes OR analista OR ITIL OR ITSM)` | no funciona (sin_resultados) | 0 | 2026-11-12 03:02 |
| 7 palabras con OR (7 términos, 6 OR) | `(funcional OR soporte OR helpdesk OR monitoreo OR incidentes OR analista OR ITIL)` | no funciona (sin_resultados) | 0 | 2026-11-12 04:01 |
| 3 bloques 3+3+2 (8 términos, 5 OR) | `("cv a" OR "enviar cv" OR "cv al mail") (funcional OR soporte OR helpdesk) (Argentina OR LATAM)` | funciona | 24 | 2026-11-12 03:54 |
| 3 bloques 3+3+3 (9 términos, 6 OR) | `("cv a" OR "enviar cv" OR "cv al mail") (funcional OR soporte OR helpdesk) (Argentina OR LATAM OR CABA)` | no funciona (sin_resultados) | 0 | 2026-11-12 04:05 |
| AND anidados: 4 OR + 2 AND (6 operadores) | `("cv a" OR "enviar cv") ((soporte AND aplicaciones) OR (mesa AND ayuda) OR helpdesk OR monitoreo)` | no funciona (sin_resultados) | 0 | 2026-11-12 05:57 |
| AND anidado: 3 OR + 1 AND (4 operadores) | `("cv a" OR "enviar cv") ((soporte AND aplicaciones) OR helpdesk OR monitoreo)` | funciona | 21 | 2026-11-12 06:07 |
| AND anidados: 3 OR + 2 AND (5 operadores justos) | `("cv a" OR "enviar cv") ((soporte AND aplicaciones) OR (mesa AND ayuda) OR helpdesk)` | funciona | 21 | 2026-11-12 06:26 |
