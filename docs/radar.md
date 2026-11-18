# Radar: reglas de dominio

Detalle que `AGENTS.md` resume en un renglón. Valen igual que las reglas de `AGENTS.md`.

## Qué se busca es 100 % del usuario
`<DATA_DIR>/candidato/busquedas.json`: una lista de búsquedas con bloques de términos (OR dentro del bloque, AND entre bloques, frase exacta y excluir por término) o una consulta libre. Formato, armado y validación en `core/radar_consultas.py` (`armar_consulta`, `validar_busquedas`). Se edita en el archivo (Mi perfil la muestra, solo lectura) y rige desde la próxima vuelta. El código no tiene ninguna palabra de búsqueda propia, ni listas de empresas, ni datos personales; sin archivo, el Radar lee solo el feed.

## Sintaxis de LinkedIn
Lo que funciona está en `docs/linkedin-sintaxis.md` (`tools/sintaxis_linkedin.py`). Antes de cambiar el armado o la validación se prueba ahí, solo con la cuenta 2.

## Cero resultados en una búsqueda es un error
Se guarda captura y HTML en `radar_debug/sin_resultados/` y se abre un ticket `radar/sin_resultados` (`RadarLoop._leer_consulta`, `core/tickets.py`). No se reemplaza sola.

## Reglas del Radar en el perfil (creador, 12/11)
`<DATA_DIR>/candidato/reglas_radar.json` (`core/reglas_radar.py`), editable en Mi perfil («Reglas del Radar», con validación, versión y backup): búsquedas por hora y por día de cada cuenta y el porcentaje si LinkedIn pausó a las dos, pausas entre lecturas y entre vueltas, largo de cada lectura (uno solo para toda búsqueda y otro para el feed), pausa por bloqueo y tope de envíos. `core/radar.py` no tiene ningún valor de esas reglas (lo mide `tests/test_radar_sin_reglas_internas.py` con `ast`). Sin el archivo o con un campo de menos o inválido, el Radar no lee nada (ni feed ni búsquedas), muestra el motivo y abre el ticket `radar/reglas_invalidas`; sin tope de envíos válido no envía. Toda regla nueva va al perfil, nunca al código.

## Cuentas (creador, 13/11)
`cuenta_feed` y `cuenta_busquedas` en `reglas_radar.json` (hoy: feed con la principal, búsquedas con la secundaria). Si esa cuenta no puede (sin sesión, pausada o sin presupuesto), esa lectura no se hace: nunca se cae en la otra, ni tras un bloqueo. Si falta el campo, esa lectura no se hace y se abre un ticket `radar/reglas_invalidas`.

## Sin idioma (creador, 12/11)
Las búsquedas no llevan idioma; las palabras clave ya lo marcan. Nada distingue castellano de inglés: ni la lectura, ni lo que se guarda (todo post con texto), ni el procesamiento.

## Orden
Cada vuelta lee primero el feed (si está prendido) y después las búsquedas activas en el orden del archivo, por rondas: si el presupuesto corta, la vuelta siguiente sigue donde quedó (`radar_state.ronda_leidas`). El procesamiento va: priorizado, feed, cargados a mano y las búsquedas en el orden del archivo.

## Sin tope de posts por día (creador, 12/11)
El volumen lo limitan las búsquedas por día y el largo de cada lectura. `leidos_feed` y `leidos_busquedas` son contadores informativos.

## Resultados por búsqueda en Mi perfil
Promedio por lectura de las lecturas productivas (`core/radar_resultados.py`): búsquedas del Radar real, de cualquier cuenta, terminadas (sin `login`, `bloqueo` ni `bloqueo_levantado`), de los últimos 14 días. El feed no cuenta; las pruebas de sintaxis no escriben en `radar_lecturas`. Se asocia por consulta exacta: si cambia la consulta, no se muestra nada hasta que se lea. Sin lecturas, ni texto ni número.

## Vistas
Manual muestra solo ofertas manuales, LinkedIn (antes «Radar») solo las de LinkedIn y Portales solo las de Jooble e Indeed (decidido; Portales: `docs/portales.md`).

## El Radar corre en su propio hilo y event loop
`core/radar_hilo.py`: la API le habla solo por `RadarEnHilo` (lo async corre en el loop del Radar). Nada del event loop espera a otro hilo en forma síncrona (`executor.shutdown(wait=True)`, `join`, `.result()`): cerrar Edge tiene plazo (`PLAZO_CIERRE_NAVEGADOR_S`).

## Reintento automático del análisis
Un análisis que falló por la IA vuelve solo a OFERTA a los `ESPERA_REINTENTO_AUTOMATICO_MIN` (30) minutos, hasta `MAX_REINTENTOS_AUTOMATICOS` (2) veces (`core/radar.py`). Nunca con el análisis en pausa, sin cuota ni con AGY saturado. Reglas así van como constantes con nombre, nunca escondidas.

## Reintento y corrección automáticos de la generación (14/11)
- **Fallas de AGY** (timeout del Redactor o del plazo, «Fallo al ejecutar agy CLI», 503): el post vuelve solo a GENERANDO a los `ESPERA_REINTENTO_GENERACION_MIN` (30) minutos, hasta `MAX_REINTENTOS_GENERACION_AUTO` (2) veces (columna `reintentos_gen_auto`). Nunca con la generación en pausa, sin cuota ni con AGY saturado. Lo hace el ciclo del Radar (`reintentar_generacion_fallida`).
- **Rechazo de la revisión de calidad** (clichés, impacto vago, ATS bajo el piso, keywords aprobadas faltantes, mail que no cumple): antes de ir a ERROR, `CORRECCIONES_DE_CALIDAD` (1) pasada de lo que hace «remediar» (`remediate_cv_task`: el auditor reescribe con sus directivas). Si aprueba, sigue a LISTO; si no, ERROR con «Revisá el CV (tras una corrección automática): …». Las reglas de calidad no cambian, y un rechazo no se reintenta solo.

## Generar a mano corre siempre (creador)
«Generar CV y mail» y «Postular igual» de una oferta marcan `gen_manual = 1` en `linkedin_posts` y se generan aunque la generación esté en pausa (también si un reinicio los cortó). Una oferta cargada a mano (fuente `manual`) es un pedido manual: se genera siempre, con o sin pausa. La pausa frena solo lo automático del Radar: las colas prioritaria y de fondo, y lo que vuelve a GENERANDO por reprocesar, reintentar o regenerar (que ponen `gen_manual = 0`). Generar no es enviar: queda LISTO sin aprobar.

## Portal de prueba
Todo portal de prueba (1199 u otro) arranca con `LINKEDOUT_SIN_RADAR=1`: si no, reanuda el Radar de la copia de datos y abre Edge sin sesión de LinkedIn.

## Recalcular la afinidad sin IA
`python tools/recalcular_afinidad.py --db <base> --data-dir <datos> [--dry-run | --aplicar] [--no-oferta ID ...]` recalcula `match_score` y `desglose_afinidad` con `core/afinidad_reglas.py` y la fórmula de `core/strategist.py`, sin IA.
Por defecto simula (solo lectura) e imprime un JSON con los cambios; `--aplicar` escribe en una transacción. `--no-oferta` pasa esos posts a `IGNORADA` («no es una oferta»).
Siempre sobre una copia de la base, nunca sobre la de producción.

## CV desde el maestro (creador, 14/11)
`core/cv_maestro.py`, desde `server._generar_desde_maestro` (Radar, pipeline completo y recálculo). El CV maestro es `candidato/experiencia.txt`, completo y nunca recortado. En inglés se usa `experiencia_en.txt`: se traduce una sola vez, lleva la huella del castellano, se vuelve a traducir si el castellano cambia y queda pendiente de aprobación.
- **Selección por código:** todas las empresas, con el rol más afín y el rango completo; al menos 2 viñetas en roles de 4 años o más. La educación sale de lo que pide la oferta. La página se llena con el PDF real **antes** del ajuste; después solo se sacan viñetas.
- **Ajuste:** una llamada a la IA, con effort low, que devuelve JSON por línea. Las pautas salen de una sola fuente, `core/pautas_cv.py`, que comparte las reglas con la auditoría: palabras, patrones de la lente de estilo, keywords por su núcleo, perfil y años de `anios_por_area`.
- **Auditoría independiente:** el auditor, el control de inventos (cifras, años, nivel de idioma), los largos y el perfil. Si falla, hay **una sola** corrección; si sigue fallando, se abre un ticket `cv/cv_no_aprobado` con las líneas y la postulación queda en ERROR. No hay «Para revisar».
- **Título:** se decide una sola vez. **Mail:** arranca con el CV ajustado y corre en paralelo con la auditoría; si no pasa, abre un ticket `mail/mail_no_aprobado`. **No se generan pitch ni pitches multicanal.**
