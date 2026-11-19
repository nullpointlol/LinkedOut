# Perfil del candidato: reglas de dominio

Detalle que `AGENTS.md` resume en un renglón. Valen igual que las reglas de `AGENTS.md`.

## Mi perfil
Los datos del candidato (experiencia, personales, restricciones, años por área fijados, voz del mail y aprendizajes) se editan desde la vista «Mi perfil» del portal (`core/perfil_candidato.py`, `/api/candidato/perfil/{clave}`). Cada guardado valida igual que el motor, rechaza versiones viejas (409) y deja antes un respaldo de toda la carpeta en `data/backups/candidato-AAAAMMDD-HHMMSS/`. Rige desde la próxima generación; los CV ya generados no cambian (botón «Regenerar CV listos sin aprobar»). Editar los archivos a mano sigue funcionando, pero sin respaldo.

## Datos del motor en el perfil (creador, 13/11)
Lo que el motor sabía del candidato por código ahora se lee de `<DATA_DIR>/candidato` (`core/candidato_datos.py`) y se edita en Mi perfil. Sin el dato, el motor no inventa: deja vacío o no aplica la regla.
- **Rol por defecto** (CV, docx, mail): el `Puesto:` de `personales.txt`. Sin `Puesto:`, el CV va sin rol y el mail sin «Soy …».
- **`stack.json`** (tarjeta «Stack y transferibles»): coincidencias directas y transferibles que el análisis sin IA valida por palabra, y las habilidades base del CV de respaldo. Sin archivo, el análisis sin IA no valida nada.
- **`roles_objetivo.json`** (tarjeta «Roles objetivo y áreas»): los roles del grupo A (prioridades y prompt del analista; «=rol» exige el título completo) y las áreas de trayectoria: títulos que las detectan, sinónimos de afinidad, nombres en años por área y si son el perfil actual (las que no lo son van a caso 3 en el titular). El orden desempata el titular.
- **`conocimientos_ausentes.json`** (tarjeta «Conocimientos que no tenés»).
- **Zona**: `localidades_permitidas` en `restricciones.json`. Sin localidades no se descarta por ubicación.
- **Inglés sin ubicación compatible** (creador, 19/11; `core/descartes.py`, `condicion_ubicacion`): una oferta escrita en inglés sin señal de que acepte candidatos de donde trabaja el candidato queda no apta («En inglés y sin indicar que acepte candidatos de Argentina»), en el Radar y los portales; la carga manual solo queda marcada como condición, y «Postular igual» la libera (`ubicacion_aceptada`). Señales: los lugares del perfil (`remoto_desde`, `base`, `residencia`, `residencia_cerca_de`, `localidades_permitidas`, también con otra terminación: «Argentine»), `ubicaciones_compatibles` (opcional; por defecto LATAM, Latin America, South America, worldwide, anywhere, global remote y similares), `husos_compatibles` (opcional, enteros; por defecto `[-3]`: «GMT-3», «UTC -3») y un contacto argentino (+54, dominio .ar). Que diga «remote» no alcanza. Lo ya analizado se re-chequea con `tools/rechequear_ubicacion.py --data-dir <copia> [--aplicar]` (LISTO y ANALIZADA; las aprobadas y las manuales solo quedan marcadas; se niega con los datos de producción).

Los archivos nuevos no existen en `data/` real hasta que el creador los cargue en Mi perfil, después del deploy. El perfil de ejemplo (`config/ejemplo/candidato`) trae los cuatro.

## Datos personales fuera del repo (creador)
El repo es privado hoy, pero no se confía en eso. Todo dato del candidato (nombre, contacto, experiencia, empresas, preferencias de búsqueda, reglas sobre su trayectoria) vive en `<DATA_DIR>/candidato/`, que no se versiona (`data/` está en `.gitignore`). Las reglas del candidato están en `data/candidato/reglas_candidato.md`: leelas junto con `AGENTS.md`. El repo trae un perfil de ejemplo ficticio en `config/ejemplo/candidato/`. Nunca escribas un dato real del candidato en código, tests, documentos o mensajes de commit: en los tests se usa el perfil ficticio (Alex Rivera, Nexo Data, Omni).

## Datos del candidato solo en el perfil (creador)
Toda información sobre el candidato (años, cargos, áreas, rol, nombre, datos personales) vive solo en `<DATA_DIR>/candidato` y es configurable y editable en Mi perfil. El código y los tests no llevan datos del candidato: los tests usan un candidato inventado.

## La base es la verdad, pero no la única fuente (creador)
`data/candidato/experiencia.txt` es el CV completo aprobado por el creador (`## Empresa` / `### Rol | AAAA - AAAA`). Los títulos de rol, empresas y fechas se copian tal cual; nunca se inventan ni se fusionan (`core/cv_guardas.py::corregir_encabezados` los corrige y bloquea lo que falte). «La ausencia de evidencia no es evidencia de ausencia»: al generar no se es conservador. Las responsabilidades, conocimientos y herramientas de la oferta que coinciden o son transferibles van al CV tal como los detalla la oferta (viñeta del rol donde encajan y HABILIDADES), sin mentir. Todas, sin excepción (creador, 16/11): cada herramienta o conocimiento MATCH o TRANSFERIBLE aparece en el CV; va en HABILIDADES TÉCNICAS o integrado en la viñeta cuyo contexto encaja, aunque el maestro no lo nombre: nunca se inventan años, clientes, cifras, certificaciones ni títulos académicos, y rubro, formación o años de la oferta no son habilidad (`TRANSFERENCIA_DE_CONOCIMIENTOS`, `es_requisito_no_habilidad`). `Puesto:` de `personales.txt` es el titular del candidato. Cambiar la base solo con aprobación del creador. Cifras (creador, 14/11): las cifras lógicas y coherentes que cubren faltas para los filtros ATS se cargan **en el CV maestro**, con aprobación del creador, y desde ahí son datos del candidato; el flujo por oferta no inventa cifras nuevas. Tildes y comas imperfectas no se corrigen: dan humanidad.

## Formato del CV (creador)
Por defecto, CV completo sin límite de páginas: todos los roles, detalle en lo relevante para la oferta y síntesis en el resto. El de 1 página es opcional (casilla al regenerar), agrupado por empresa con uno de sus títulos reales y el rango completo.

## Años de experiencia del mail (creador)
Salen de analizar el perfil (años por área según el dolor de la oferta), no de que el candidato los declare. Se analiza una vez cuando cambia el perfil.

## Datos reales recién con el deploy
`<DATA_DIR>` (perfil, búsquedas, base) no se cambia antes de que esté desplegado el código que usa el formato nuevo. Si no, producción falla (sacar `idioma` antes del merge abrió 10 tickets). Lo ideal es una migración tolerante en el código.

## Pendiente: desglose de cargos por empresa (creador, 14/11)
Hoy, en el CV desde el maestro (v2, `core/cv_maestro.py`), una empresa con varios cargos sale con un solo encabezado:
uno de sus cargos reales (el más afín a la oferta) y el período total, por ejemplo «### Analista de Soporte |
Acme Datos (2016 - 2024)», aunque en el maestro tenga 3 cargos. El creador lo quiere resolver más adelante; no se toca sin
consultarlo. Opciones probadas:
- **Un solo cargo para todo el período** (la v2, la que está en uso): pendiente, porque muestra un cargo que no cubre el
  período entero.
- **La empresa con su período y todos los cargos en una línea debajo** («Ingeniero de Plataforma · Analista de Soporte ·
  Analista Funcional»; la v3, rama `cv-maestro`, commit a34b778): **rechazada** por el creador.
