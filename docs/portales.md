# Portales: reglas de dominio

Ofertas de otros portales de empleo, además de LinkedIn. Valen igual que las reglas de `AGENTS.md`.

## Fuentes
- **Jooble**: API oficial (`POST https://jooble.org/api/{key}`, 1 página por consulta, 100 consultas por día por key).
  Jooble da una sola key, sin elegir región (no existe una key de Argentina: ); vive en el `.env` del portal (`JOOBLE_API_KEY`, ver
  `core/mailer.ruta_env`): nunca en código, tests, logs ni tickets. Trae solo un resumen: el aviso completo se lee de
  su página con el lector de ofertas por URL (`scrape_job_url`) antes de clasificar; si no se puede, sigue el resumen.
- **Indeed Argentina**: JobSpy (`python-jobspy`, `country_indeed="argentina"`), sin proxies. Trae el aviso completo. Un aviso corto de un portal que trae el aviso completo (regla sin `leer_pagina_aviso`) se analiza igual: no es una captura incompleta.
- Google Jobs no se usa: JobSpy no puede leerlo (muro de JavaScript, prueba del 13/11). Nada de evadir bloqueos.

## Mismo circuito
Las ofertas van a `linkedin_posts` con `fuente` = `jooble` o `indeed` y `query` = nombre de la búsqueda, y siguen el
circuito de siempre: clasificación, análisis, CV y mail (`core/portales_store.py`). No aparecen en la vista LinkedIn ni
en la manual: tienen su vista, Portales (`/api/radar/posts?fuente=portales`).

## Duplicados
Una oferta ya vista (`portales_vistos`) no vuelve a entrar, aunque se haya borrado por no ser oferta. Una con el mismo
link (del portal o directo) o el mismo título y empresa (sin tildes ni mayúsculas) que otra de un portal se une a esa:
se le suma el link.

## Búsquedas y reglas en el perfil
- En `busquedas.json` cada búsqueda puede llevar `portales` (`["jooble", "indeed"]`) y `palabras` (palabras simples, sin
  operadores ni comillas). Se editan en Mi perfil (tarjeta «Búsquedas del Radar»). Un error ahí solo saltea la
  búsqueda en ese portal, nunca la de LinkedIn.
- `reglas_portales.json` (Mi perfil, «Reglas de Portales»): por portal, `activo`, `consultas_por_dia` (Jooble: hasta
  100), `minutos_entre_consultas` (ritmo), `ubicacion`, `resultados_por_consulta` y `dias`. Sin un valor, ese portal
  no corre y se abre un ticket `portales/reglas_invalidas`. El código no trae ningún valor por defecto.

## Dónde corre
En el hilo del Radar, en su propia vuelta después de la de LinkedIn (`RadarLoop.vuelta_portales`), sin depender de
LinkedIn ni de sus cuentas: corre aunque LinkedIn no tenga reglas. Por vuelta, a lo sumo una consulta por portal, si
le toca por su ritmo y le quedan consultas del día; rota entre sus búsquedas (la que hace más que no se consulta
primero). Cada consulta queda en `portales_lecturas` (con su error) y no gasta el presupuesto de búsquedas de LinkedIn.
Un error del portal abre un ticket `portales/consulta_fallida`.

## Vista Portales
Chips por portal con su cuenta, el sello del portal en cada tarjeta y en el detalle, y «Postular en el sitio» (link
`https`, el directo primero) cuando la oferta no tiene mail. En el celular no tiene botón (por ahora, solo monitor).
