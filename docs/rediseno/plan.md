# Rediseño mobile-first de LinkedOut: plan (aprobado el 17/11)

Rama `rediseno-plan` (desde `develop` 0bc1ecd). Nada de esto toca el portal: es plan, prototipo y capturas.
Se implementa en el repo público después de la Fase 2, por etapas (§9).

**Pedido del creador (17/11):** rediseño mobile-first, minimalista, moderno y sofisticado, oscuro. Mi perfil reorganizado de cero.
**Invariantes:** paridad total de funciones; contenido de las ofertas literal; ningún dato del creador (prototipos con una persona inventada de otro rubro: Sam Ortega, desarrolladora web, en «Ciudad Ejemplo»).
**Prioridad ante conflicto:** claridad de Mi perfil y uso en celular, después estética.

| Qué se mide | Resultado | Cómo repetirlo |
|---|---|---|
| Cada acción actual tiene lugar nuevo | **154 de 154** (inventario de botones: fijos, dinámicos y cubiertos en otro test) | `python docs/rediseno/mapa_completo.py` |
| Cada campo de Mi perfil tiene lugar nuevo | **102 de 102** (tarjetas y claves que dibuja `perfil.js`) | ídem; tabla en `mapa-campos.json` |
| Contraste AA por par de tokens | **54 pares, 0 fallas** | `python tools/contraste_tokens.py` → `contraste.md` (y test en `tests/test_rediseno_base.py`) |
| Capturas sin scroll horizontal | **19 de 19** (8 de LinkedIn, 11 de Mi perfil; 375 y 1200 px) | `python docs/rediseno/capturas.py` |

Prototipo navegable: `docs/rediseno/prototipo/index.html` (abrir el archivo; no necesita servidor ni red).

---

## 0. Las 3 decisiones del creador: aprobadas el 17/11

**Aprobado por el creador el 17/11** («Sí, las 3»). Resoluciones:
1. **Stack:** CSS propio con tokens, sin compilación.
2. **Búsquedas editables:** sí, como etapa **E6**, después de la paridad.
3. **Nombres visibles:** se aprueba la tabla completa de §8.

La implementación va después de la Fase 2, en el repo público. Abajo queda el planteo original de cada decisión.


1. **Stack de estilos.** Recomiendo **CSS propio con tokens, sin paso de compilación** (§5). Alternativa: Tailwind compilado en local (necesita Node en cada cambio de estilos).
2. **Búsquedas editables desde Mi perfil.** Hoy solo se editan los portales y las palabras de cada búsqueda; nombre, consulta, orden y «activa» se cambian a mano en `busquedas.json`. Editarlas en la pantalla (bloques «contiene / y / sin», como en el prototipo) **es una función nueva**: necesita un `PUT /api/candidato/busquedas` con versión y respaldo. Recomiendo **sí, como etapa aparte (E6), después de la paridad**. Mientras tanto se muestran como hoy, legibles y de solo lectura.
3. **Nombres visibles nuevos** (§8): por ejemplo «Feed general» → «Búsquedas», «Pendientes» → «Por analizar», «Match/Brecha» → «Coincide/Falta», «Restricciones» → «Dónde y cómo trabajás». Los valores internos no cambian. Recomiendo aprobar la tabla entera; si alguno no te gusta, se cambia solo ese.

---

## 1. Inventario de vistas y estados

| Vista | Dónde está hoy | Estados que hay que conservar |
|---|---|---|
| Barra superior | `index.html` `<header>`: 5 vistas, Ajustes (modelo de IA y telemetría), Herramientas (4), Volver a analizar, Nueva oferta | vista activa; pulso del modelo; contador de tickets |
| HUD de progreso | `#agenticProgressHUD`, `#statusAlert`, `#batchAlert` (`app.js`) | 3 pasos (Estratega, Redactor, Auditor), %, tiempo, modelo; lote con % |
| **Manual** (editor de 3 columnas) | `<main>`: `#panel-ofertas`, `#panel-medio` (vacante + matriz), `#panel-cv` (CV + auditoría); `#navMobilePostulaciones` con 4 pestañas (`app.js`) | 5 etapas, 3 filtros de CV, 3 órdenes, búsqueda; estado de la postulación (5); seguimiento; texto limpio/original; 7 filtros de requisitos; CV/auditoría; modo contingencia |
| **LinkedIn / Portales** | `#vista-radar` con `data-modo` (`radar.js`); lo propio de cada modo con `data-solo-radar`, `data-solo-linkedin`, `data-solo-portales`, `data-solo-manual` | Radar corriendo/detenido/en espera/bloqueado; 10 filtros de estado + No apto; ubicación (3), canal (3), portal (5), orden (2); Búsquedas y Mi feed; Historial anterior / Postulaciones sin post; tarjeta con estado, sub-estado, afinidad, alcance, canal, «en cola», progreso, sello No apto, acciones; detalle (autor, chips, enlaces, tiempos, canal, análisis, cómo postularse, error, enviado, requisitos que ciclan, «Me descarta», texto de la imagen); mail (Para con sugerencias, Asunto, Cuerpo, Así lo recibe, adjuntos, guardado, acciones por canal, 9 acciones según estado) |
| Avisos del Radar | `#radarCuotaAviso`, `#radarGmailAviso`, `#radarEnviosPausados`, `#radarBusquedasPausadas`, `#radarCuentas`, `#radarMensaje` | cada uno con su acción |
| Diálogo Aprobar listos | `#radarAprobarModal` | lista con casillas, duplicados con «Enviar igual»/«Descartar», Aprobar N |
| **Drawer Historial de procesos** | `#radarProcesosPanel` | topes del día, cuota, interruptores Feed/Búsquedas, interruptores de portales, Análisis/Cola prioritaria/Cola de fondo (Pausar/Reanudar), lista de procesos e historial |
| Celular, Por revisar | `#radarCelularPanel` (QR, desemparejar), `#radarRevisionPanel` (Abrir, Visto) | |
| **Mi perfil** | `#vista-perfil`, todo dibujado por `perfil.js` | 14 tarjetas (13 con su «Guardar» + Búsquedas con «Guardar portales»), borradores, versión por tarjeta (409 → Recargar), respaldo en cada guardado, vista previa de años, regenerar CV listos |
| **Tickets** | `#vista-tickets` (`tickets.js`), botón flotante `#btnReportarError`, `#modalTicket` | filtros Sin resolver/Abiertos/En curso/Resueltos/Todos; detalle con captura, traza, pasos, ocurrencias; copiar; cambiar estado |
| Modales | `#modalNueva` (Texto, Link, Varios links), `#modalToolkit` (4 herramientas) | carga, errores |

Detalle de cada acción y su lugar nuevo: `mapa-acciones.json` (154 claves, las mismas de `tests/inventario_botones.json`).

---

## 2. Auditoría UX (priorizada)

| # | Problema | Dónde | Efecto | Arreglo |
|---|---|---|---|---|
| 1 | **Mi perfil es una pared de 14 tarjetas en el orden de los archivos**, no de las tareas | `perfil.js` | no se encuentra nada; jerga de archivo («Reglas de Portales», «Stack y transferibles») | 6 secciones por tarea (§6) |
| 2 | **13 botones Guardar + «Guardar portales»**, sin indicador de cambios ni aviso al salir | Mi perfil | se pierden cambios; no se sabe qué falta guardar | una barra «N cambios sin guardar · Descartar · Guardar cambios» y aviso al salir |
| 3 | **Experiencia y Datos personales son texto crudo** con formato `##`/`###`/`•` | Mi perfil | un error de formato rompe el CV | formularios que leen y escriben el mismo archivo; el texto crudo queda a un toque |
| 4 | Rótulos-instrucción larguísimos («Dónde vivís: localidad, zona, región y país (uno por renglón; …)») y listas «una por renglón» | Mi perfil | difícil de leer y editar en celular | rótulo corto + ayuda debajo; chips en vez de renglones |
| 5 | En celular, la navegación por pestañas no tiene vuelta atrás clara y la barra de operación ocupa media pantalla | LinkedIn | lista → detalle → mail confuso | flujo de 3 pasos con «‹ Ofertas», barra fija con la acción principal |
| 6 | 10 filtros de estado + 3 grupos de chips siempre visibles | lista del Radar | ruido; empuja la lista hacia abajo | 4 estados frecuentes + «Más»; el resto en «Filtros» con chips removibles |
| 7 | La barra superior mezcla navegación con acciones de una postulación («Volver a analizar») | header | acción fuera de contexto | navegación arriba; acciones en su panel |
| 8 | Colores saturados por todos lados (violeta, ámbar, esmeralda, cielo) y MAYÚSCULAS («DETENIDO», «PENDIENTE») | tarjetas, chips | todo compite | 4 colores de estado con texto o ícono; sentence case |
| 9 | Ajustes de automatización (números del Radar y 7 portales) mezclados con datos personales | Mi perfil | riesgo de tocar algo crítico | sección «Automatización», marcada Avanzado |
| 10 | Íconos de Font Awesome y emojis mezclados (📥🎯🤝📋) | header, herramientas | inconsistencia | una sola familia (Lucide) |
| 11 | Dependencias de red en runtime (Tailwind CDN, Font Awesome, marked) | `index.html` | el portal se ve roto sin internet; Tailwind CDN no es para producción | todo local (§5) |
| 12 | Texto de la oferta y análisis con la misma jerarquía | detalle | no se distingue lo original de lo generado | «Publicación original · texto tal como se publicó» separado del análisis |

---

## 3. Dirección visual

Oscuro, sobrio, con jerarquía por tamaño y peso, no por color. Separación tonal (canvas → surface → elevated) y bordes de 1 px de bajo contraste.
El acento azul solo marca la acción principal, la selección y el foco. Los estados usan color **y** texto o ícono.

## 4. Sistema visual

**Tokens** (`static/css/tokens.css`, fuente única; el prototipo también lo usa). Partí de la paleta de la guía; dos ajustes medidos:

| Token | Guía | Final | Por qué |
|---|---|---|---|
| `--text-muted` | #778395 | **#8591A3** | con #778395 no llegaba a 4.5 sobre `elevated` (4.22) ni `hover` (3.77) |
| `--border-control` | (no había) | **#626F88** | borde de campos y casillas: 3:1 contra canvas, surface y elevated |
| `--bg-selected`, `--*-soft` | (no había) | acento o estado al 12 % sobre surface | fondo de fila elegida y de chips; texto del estado ≥ 4.9 encima |

Superficies `#0B0E13` / `#121720` / `#19212C` / `#202A37`; texto `#E8EDF4` / `#A4AFBE` / `#8591A3`; acento `#78A9F8` con texto `#0B0E13` (8.13); éxito `#48B99A`, atención `#D9A84E`, error `#E47777`.
**Contraste medido:** 54 pares, todos AA (tabla completa en `contraste.md`). El peor par de texto: `text-muted` sobre `bg-hover`, 4.54.

**Tipografía, una familia: la del sistema** (Segoe UI Variable en Windows, SF en iPhone, Roboto en Android). Cero bytes, cero red, cero licencia, y en el celular se ve nativa.
Escala 12 / 13 / 14 / 16 / 18 / 22 / 28 px; mínimo 12 px (regla del repo); 16 px en campos del celular (evita el zoom de iOS); números tabulares.

**Íconos, una familia: Lucide** (licencia ISC), copiados a `static/` como sprite SVG. 18 px, trazo 1.75. Íconos sin texto solo si son universales, y siempre con `aria-label`.

**Espaciado** múltiplos de 4 (4, 8, 12, 16, 20, 24, 32, 48). **Radios** 6 / 8 / 12. **Controles** 36 px en escritorio, 44 px táctiles. **Sombras** solo en hojas y menús.

**Estados de cada componente:** normal, hover, foco (anillo de 2 px con separación), presionado, deshabilitado (50 %), cargando, vacío, error, éxito, editado (borde de acento + «Sin guardar»). `prefers-reduced-motion` apaga las transiciones.

## 5. Stack (hipótesis confirmada)

- **Sigue sin framework.** El JS actual (`app.js`, `radar.js`, `perfil.js`, `tickets.js`) se queda; cambia el marcado y las clases.
- **Tailwind CDN → `static/css/tokens.css` + `static/css/componentes.css`** (como los del prototipo, que ya son la base). Sin compilación, sin Node.
  Por qué no Tailwind compilado: hoy `radar.js` y `app.js` arman HTML con clases en strings; compilar exige escanear esos strings y un paso de build en cada cambio. Con clases semánticas (`.oferta`, `.chip-ok`) el JS se vuelve más legible.
- **Font Awesome → sprite Lucide local. `marked` → copia local** en `static/vendor/` (versión fija).
- Resultado: **ninguna dependencia de red en runtime**; el portal anda sin internet (salvo LinkedIn, la IA y el mail, que son el producto).
- Durante la migración conviven los dos: `tokens.css` se carga antes y cada etapa retira el Tailwind de lo que reescribe. La E5 saca el CDN.

---

## 6. Mi perfil, reorganizado por tareas

**Seis secciones** en este orden (índice a la izquierda en escritorio; en celular, el índice es la primera pantalla y cada sección se abre con «‹ Mi perfil»).
Cada fila del índice dice qué hay («Sam Ortega · 2 empresas · 3 cargos») y avisa si hay cambios sin guardar o algo incompleto («1 portal»).

| Sección | Qué contiene (tarjetas de hoy) |
|---|---|
| **Mis datos** | Datos personales (formulario), Experiencia (editor), Años de experiencia por área (+ «Años que se usan hoy») |
| **Lo que sé** | Habilidades que tenés (coincidencias), Habilidades transferibles, Habilidades del CV de respaldo, Lo que no sabés |
| **Qué busco** | Puestos que buscás (roles), Áreas de tu trayectoria, **Dónde y cómo trabajás** (Restricciones + la ubicación de cada portal), Búsquedas del Radar (con sus portales y palabras) |
| **Mi CV y mi mail** | Voz del mail, Cargos en el CV, CV general, «Regenerar CV listos sin aprobar» |
| **Lo que aprendió** | Lo que marcaste en las ofertas (aprendizajes), Ofertas con puntaje bajo |
| **Automatización** · Avanzado | Radar de LinkedIn (cuentas, cuántas búsquedas, pausas, largo de lectura, bloqueo, envíos), Portales (un panel plegable por portal con su interruptor), Ofertas repetidas |

**Guardado único.** Un borrador por archivo, como hoy (misma versión, mismo `PUT /api/candidato/perfil/{clave}`, mismo respaldo).
La barra «N cambios sin guardar» aparece con el primer cambio y dice en qué tarjetas. «Guardar cambios» guarda en orden cada archivo cambiado.
Si uno falla, los demás quedan guardados y ese queda marcado con su error; con 409, «Recargar» en esa tarjeta. Las búsquedas entran en el mismo guardado (`PUT …/busquedas/portales`).
**Aviso al salir** (cambiar de vista o cerrar la pestaña) con cambios: «Seguir editando · Salir sin guardar · Guardar y salir».

**Experiencia sin perder el formato del motor.** El editor (empresa → cargos con título, desde, hasta → viñetas reordenables; grupos de habilidades) lee y escribe `experiencia.txt` (`## Empresa`, `### Cargo | AAAA - AAAA`, `• viñeta`).
Reglas: lo que el editor no reconoce (comentarios, secciones raras) se guarda intacto en su lugar.
Garantía medible: `serializar(leer(texto)) == texto` byte a byte para el perfil de ejemplo y para fixtures con casos raros. Si un archivo real no cumple, ese archivo se abre en modo texto con un aviso, sin tocarlo.
Lo mismo para `personales.txt` (nombre, `Puesto:`, línea de contacto separada por « | », EDUCACIÓN, IDIOMAS; el resto, «Otros datos» en texto).

**Mapa de campos (102 de 102):**

| Hoy (tarjeta › campo) | Lugar nuevo |
|---|---|
| Experiencia (texto) | Mis datos › Experiencia: editor + «Editar como texto» |
| Datos personales (texto) | Mis datos › Datos personales: formulario + «Ver como texto» |
| Voz del mail (texto) | Mi CV y mi mail › Voz del mail |
| Restricciones › Base, Presencial o híbrido en, Remoto desde | Qué busco › Dónde y cómo trabajás |
| Restricciones › Acepta viajes / reubicación / rubro sin experiencia | ídem, interruptores con texto en primera persona |
| Restricciones › Edad, Edad al | ídem › Edad, Calculada al |
| Restricciones › Localidades, Dónde vivís, Lugares de los que vivís cerca | ídem › chips: Localidades donde aceptás…, Vivís en, Vivís cerca de |
| Años por área fijados › Área, Años, Reemplaza, Agregar área (8), Quitar; «Años por área que se usan hoy» | Mis datos › Años de experiencia por área (Reemplaza a = chips) |
| Roles objetivo y áreas › Roles que buscás | Qué busco › Puestos que buscás (chips; «= título exacto» como marca del chip) |
| Roles objetivo y áreas › Áreas: nombre, títulos, sinónimos, nombres, es tu perfil actual; Agregar área de trayectoria | Qué busco › Áreas de tu trayectoria (plegables reordenables; sinónimos en «avanzado») |
| Stack › Coincidencias (nombre, palabras, justificación) | Lo que sé › Habilidades que tenés (Por qué la tenés) |
| Stack › Transferibles (nombre, palabras, equivalente) | Lo que sé › Habilidades transferibles |
| Stack › Habilidades base (gestión, técnicas) | Lo que sé › Habilidades del CV de respaldo |
| Conocimientos que no tenés | Lo que sé › Lo que no sabés (chips) |
| Reglas del Radar › 13 números + 2 cuentas | Automatización › Radar de LinkedIn (pausas como «desde a hasta») |
| Cargos en el CV › N, empresas (empresa, cargos, títulos unificados es/en, 3 topes) | Mi CV y mi mail › Cargos en el CV (radio «Cada cargo… / Según la oferta»; empresa elegida de tu experiencia) |
| Cargos en el CV › CV general (10 campos) | Mi CV y mi mail › CV general (interruptor en el encabezado) |
| Reglas de Portales › 7 portales × (encendido + 10 reglas + propias) | Automatización › Portales, un panel por portal; **«Ubicación» pasa a Qué busco › Ubicación que usa cada portal** |
| Reglas de Portales › Repetidas (parecido, días) | Automatización › Ofertas repetidas entre portales |
| Aprendizajes (lista, Borrar) | Lo que aprendió › Lo que marcaste en las ofertas |
| Ofertas con puntaje bajo (umbral, mínimo, resultados) | Lo que aprendió › Ofertas con puntaje bajo |
| Búsquedas del Radar (nombre, pausada, consulta, promedio, portales, palabras, errores; Guardar portales) | Qué busco › Búsquedas del Radar (consulta en bloques legibles) |
| Regenerar CV listos sin aprobar | Mi CV y mi mail › aviso «N CV listos sin aprobar · Regenerarlos» |
| 13 Guardar + Guardar portales + Recargar | barra única + Recargar por tarjeta en conflicto |

Clave por clave: `mapa-campos.json`.

---

## 7. LinkedIn y Portales: celular y escritorio

**Celular (< 768 px): lista → detalle → mail.** Una pantalla por paso, sin perder nada:
- Barra de operación de una línea: estado («En ejecución»), **Aprobar listos · N** y «⋯». El «⋯» abre una hoja con Detener/Iniciar, Procesos, Por revisar, Celular, Afinidad mínima y las cuentas.
- Lista: pestañas Búsquedas / Mi feed; 4 estados frecuentes en un segmentado con scroll; «Filtros» con ubicación, canal, portal, orden y no aptas; los filtros activos quedan como chips removibles.
- Tocar una oferta abre el **Detalle** («‹ Ofertas», segmentado Detalle | Mail). Barra fija: «Ver el mail» y Descartar.
- **Mail:** Para, Asunto, Escribir | Así lo recibe, adjuntos, Regenerar, Abrir en el editor, Ya me postulé, Descartar. Barra fija: **Enviar**.
- Navegación global en una barra inferior: LinkedIn, Portales, Manual, Mi perfil, Más (Tickets, Herramientas, Ajustes, Nueva oferta).

**Tableta (768–1023 px):** lista + (detalle | mail con segmentado).
**Escritorio (≥ 1024 px):** 3 paneles (360 / flexible / 400 px; 320 / flexible / 360 hasta 1279).
Lo que cambia contra hoy: la barra de operación es una sola fila; «2 en curso · 13 por analizar» abre la hoja Procesos; las cuentas pasan a la hoja (y a la barra desde 1440 px).
El detalle ordena: estado → puesto y empresa → 4 cifras (afinidad, CV, ubicación, canal) → enlaces → qué necesitan → requisitos → **publicación original** (texto literal en `white-space: pre-wrap`, sin transformar nada).

**Hoja Procesos** (antes «Historial de procesos»), lateral en escritorio y pantalla completa en celular: Hoy (5 topes), Qué corre (feed, búsquedas, análisis, CV urgentes, CV del resto, con interruptor o Pausar/Reanudar), Portales, En curso y en cola, Terminados.

**Manual y Tickets** siguen el mismo patrón: Manual es lista → oferta → requisitos → CV (los 4 pasos de hoy), con «Volver a analizar» en la barra del detalle. Tickets es lista → detalle.

---

## 8. Microcopy (sentence case, voseo, sin jerga)

Solo cambia el texto visible; ids, `data-*` y valores internos quedan.

| Hoy | Nuevo |
|---|---|
| Nueva Oferta / Agregar | Nueva oferta |
| CORRIENDO / DETENIDO (pill) | En ejecución / Detenido |
| Revisar (N) | Por revisar · N |
| Aprobar todos los listos (N) | Aprobar listos · N |
| Sin trabajos en curso · Ver historial | Nada en curso · Ver procesos |
| Historial de procesos | Procesos |
| Feed general | Búsquedas |
| Pendientes | Por analizar |
| Listas para enviar | Listas |
| No apto | Mostrar también las no aptas |
| Ubicación: / Canal: / Portal: | Ubicación / Canal / Portal (dentro de Filtros) |
| Mayor match | Mayor afinidad |
| Recientes | Más recientes |
| Email (pestaña) / Email de postulación | Mail / Mail de postulación |
| Cuerpo | Escribir |
| Editor | Abrir en el editor |
| Analizar | Analizar ahora |
| Match / Transferibles / Brechas / Resp. | Coinciden / Transferibles / Faltan / Responsabilidades |
| Matriz de Requisitos & Afinidad | Requisitos |
| Dolores & Foco de la Vacante | Qué necesitan |
| Texto de la Vacante · Expandir | Texto de la oferta · Ver todo |
| Contacto & Notas · Guardar Seguimiento | Seguimiento · Guardar seguimiento |
| Actualizar CV con Selección | Actualizar el CV con esta selección |
| Auditoría · Directivas de Auditoría · Corregir CV | Revisión · Qué corregir · Corregir el CV |
| DOCX | Word |
| Modo Contingencia (Fallback) | CV hecho sin IA (la IA no respondió) |
| Herramientas opcionales · Captura en 1-Clic · Preparar Entrevista · Mensajes Networking · Pre-Flight Check | Herramientas · Capturar ofertas · Preparar la entrevista · Mensajes para contactar · Revisión antes de enviar |
| Pitch para Hiring Manager / Nota de Conexión a Reclutador | Mensaje para quien contrata / Nota para quien recluta |
| Mi perfil › Restricciones | Dónde y cómo trabajás |
| Roles objetivo y áreas | Puestos que buscás · Áreas de tu trayectoria |
| Stack y transferibles · Coincidencias directas | Lo que sé · Habilidades que tenés |
| Conocimientos que no tenés | Lo que no sabés |
| Reglas del Radar · Reglas de Portales | Automatización › Radar de LinkedIn · Portales |
| Aprendizajes | Lo que marcaste en las ofertas |
| Acepta reubicación | Mudarte por el trabajo |
| Acepta rubro o función exigida sin experiencia | Postularte aunque pidan experiencia en un rubro o función que no tenés |
| Guardado. Copia de respaldo: candidato-AAAAMMDD-HHMMSS. | Guardado. Respaldo: candidato-AAAAMMDD-HHMMSS (se sigue mostrando, más chico) |
| buscatrabajo4 · NATIVO (h1) | LinkedOut (lo pide la S4; el test que fija el h1 se migra) |

---

## 9. Tests de UI: qué se conserva y qué se migra

Hoy: **37 archivos, 315 funciones de test** tocan la interfaz. Tres grupos:

| Grupo | Archivos | Qué fijan | Estrategia |
|---|---|---|---|
| A. Playwright por id y `data-*` | `test_radar_ui` (112), `test_vista_manual` (17), `test_brechas_radar_ui` (14), `test_radar_ui_mensajes` (12), `test_ui_navegador`, `test_portales_ui`, `test_toolkit_ui`, `test_radar_pausa_panel`, `test_ui_pausas`, `test_ui_tickets`, `test_ui_interruptores`, `test_orquestacion_admision`, `test_ui_enviadas`, `test_imagenes_detalle`, `test_levantar_bloqueo_panel`, `test_ui_scoring_movil`, `test_ui_botones` | comportamiento | **Se conservan.** Regla de implementación: todo id, `data-*`, `onclick` y rol de hoy sigue existiendo en el elemento nuevo que hace lo mismo. Solo se tocan las aserciones de texto que cambian por §8, en el mismo commit |
| B. Contrato de estilo en el fuente | `test_ui_clean_dark_theme` (hex `#0f1115`), `test_legibilidad` (`text-zinc-500`, `whitespace-nowrap`, h1 «buscatrabajo4»), `test_jerarquia_texto`, `test_ui_refinements`, `test_scoring_afinidad_ui`, `test_minimalist_copy_and_buttons`, `test_ux_sin_jerga_y_ajustes`, `test_hover_dropdown_and_frontend_render`, `test_audit_panel_rendering`, `test_clean_job_display`, `test_matriz_brechas_ui`, `test_progress_hud`, `test_pipeline_progress_hud`, `test_ui_dom_contract`, `test_toolkit_e2e_integration` | clases de Tailwind y colores sueltos | **Se migran** a tests de tokens, que miden lo mismo mejor: contraste AA por par (`contraste.py` pasa a test), 12 px mínimo por estilo computado, sin scroll horizontal a 360/375/1200, botón solo-ícono con `aria-label`, sin colores fuera de `tokens.css`. Lo que fijaban de comportamiento (que exista la función, el endpoint) se conserva |
| C. Playwright por rótulo y rol, Mi perfil | `test_perfil_ui` (19), `test_holdout_mi_perfil_ui` (6), `test_perfil_navegacion`, `test_perfil_reglas_ui`, `test_perfil_busquedas_ui` (4 c/u) | 14 fieldsets, «Guardar» ×13, rótulos largos | **Se reescriben en la E3**, comportamiento por comportamiento: guardar con versión, 409 → Recargar, respaldo, borrador que sobrevive a la recarga, agregar/quitar área (tope 8), borrar aprendizaje con confirmación, regenerar listos, portales de búsquedas, resultados. Medida: cada uno de los 37 tests de hoy tiene su equivalente nuevo |

- `tests/inventario_botones.json` conserva sus 154 claves; cambia el campo `texto` según §8 y suma los controles nuevos (Filtros, Más, Guardar cambios, Descartar, Volver, aviso al salir).
- Tests nuevos: ida y vuelta byte a byte de `experiencia.txt` y `personales.txt`; aviso al salir; `mapa_completo.py` como test (154/154, 102/102); **ofertas literales**: el texto de cada oferta sembrada se compara con el DOM (`textContent`), antes y después.

---

## 10. Etapas (cada una entregable y verificable con paridad)

| Etapa | Qué | Verificación |
|---|---|---|
| **E0. Base** | `static/css/tokens.css` + `componentes.css`, sprite Lucide, `marked` local; se cargan junto a Tailwind sin cambiar nada visible | tests de tokens en verde; suite igual que antes |
| **E1. Marco** | barra superior, barra inferior en celular, «Más», avisos, HUD, botón de reportar error | inventario 154/154; 360/375/1200 sin scroll horizontal; grupo A verde |
| **E2. LinkedIn y Portales** | lista, filtros, tarjeta, detalle, mail, flujo celular, Aprobar listos, hoja Procesos, Celular, Por revisar | grupo A verde (con textos de §8); ofertas literales; capturas contra el prototipo |
| **E3. Mi perfil** | 6 secciones, formularios, editor de experiencia, guardado único, aviso al salir | ida y vuelta byte a byte; grupo C reescrito sin perder comportamientos; 102/102 |
| **E4. Manual, Tickets y modales** | editor de 4 pasos, Nueva oferta, Herramientas, Ajustes, Tickets | grupo A de Manual y Tickets verde |
| **E5. Limpieza** | fuera Tailwind CDN, Font Awesome y estilos sueltos; grupo B migrado; microcopy final | `index.html` sin URLs externas; suite completa verde; capturas finales para el README |
| **E6. Búsquedas editables** (si el creador aprueba la decisión 2) | `PUT /api/candidato/busquedas` con versión y respaldo; editor de bloques | TDD del endpoint; mismas búsquedas leídas antes y después de un guardado sin cambios |

Cada etapa: TDD, `tools/tests_rapidos.py`, prueba visual en el **1199** con datos inventados, rama propia, suite completa con autorización antes de develop.

**Hecho en E0 (18/11):** `static/css/tokens.css` (fuente única, también del prototipo) y `static/css/componentes.css` (solo clases, sin `@layer`: el preflight de Tailwind le ganaría); Lucide 1.66.0 en `static/vendor/` (solo los íconos usados, con SHA-256) y `static/iconos.svg` armado por `tools/armar_iconos.py` (trazo 1.75); `marked` ya estaba local. Medidas: `tests/test_rediseno_base.py`, y `docs/rediseno/control_visual.py` contra develop en el 1199 con `config/ejemplo`: 13 capturas a 360/375/1200, **0 píxeles distintos**, sin scroll horizontal ni errores de JS.

## 11. Riesgos

| Riesgo | Mitigación |
|---|---|
| `radar.js` y `app.js` arman HTML con clases de Tailwind en strings (`innerHTML`) | cada etapa reemplaza esas clases por las semánticas; mientras conviven, Tailwind sigue cargado; de paso se pasa a `createElement` (regla del repo) |
| El editor de experiencia rompe el formato que usa el motor | ida y vuelta byte a byte; si no se cumple, modo texto con aviso; nunca se reescribe un archivo sin cambios |
| Guardar varios archivos en una acción y que falle uno | se guarda por archivo con su versión y su respaldo, como hoy; el error queda en su tarjeta |
| 315 tests acoplados | grupo A se conserva por regla de ids; B y C se migran en la etapa que los toca, en el mismo commit |
| Se pierde una acción en celular | inventario 154/154 por etapa y prueba de cada acción a 375 px |
| Contenido de la oferta alterado por el nuevo render | solo `textContent` + `pre-wrap`; test de ofertas literales |
| Costumbre del creador | nombres nuevos aprobados en §8; el orden de las acciones principales no cambia |

## 12. Prototipos y capturas

`docs/rediseno/prototipo/`: `index.html`, `radar.html` (parámetros `?paso=lista|detalle|mail`, `?procesos=1`, `?filtros=1`, `?extra=1`), `perfil.html` (`#datos`, `#sabe`, `#busco`, `#cv`, `#aprendio`, `#automatizacion`, `?salir=1`).
Los íconos del prototipo están dibujados al estilo Lucide; la implementación usa el set real.

`docs/rediseno/capturas/` (todas sin scroll horizontal):
- LinkedIn: `radar-1200`, `radar-1200-filtros`, `radar-1200-procesos`, `radar-375-lista`, `radar-375-controles`, `radar-375-detalle`, `radar-375-mail`, `radar-375-procesos`.
- Mi perfil: `perfil-1200-{datos,sabe,busco,cv,aprendio,automatizacion,salir}`, `perfil-375-{indice,datos,busco,automatizacion}`.
