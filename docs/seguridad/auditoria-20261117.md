# Auditoría de vulnerabilidades — 17/11/2026

Base: `develop` 4ccdc33. Solo lectura sobre el código, en un worktree; producción no se tocó. Nada se arregló (ningún hallazgo era de una línea fuera de los archivos que edita S5).

**Resumen:** 3 altas, 4 medias, 4 bajas. Ninguna crítica.

## Cómo se hizo
| Control | Herramienta | Alcance | Resultado |
|---|---|---|---|
| Dependencias Python | `pip-audit` 2.x (base PyPI/OSV) | `requirements.txt` + `requirements-dev.txt` (26 paquetes, `--no-deps`) y las 101 instaladas en el Python de la PC (transitivas) | 18 avisos en 1 paquete fijado; 73 avisos únicos en 7 paquetes instalados (ver A3, B1, B2) |
| JS por CDN | OSV API (npm) + `data.jsdelivr.com` | marked, html2canvas 1.4.1, Font Awesome 6.4.0, Tailwind | sin CVE abiertas en las versiones de hoy; sin versión fija ni SRI (M1, M2) |
| Análisis estático | `bandit` 1.9.4 | `core`, `server.py`, `tools` (sin `tools/agents`) | 112 avisos, todos falsos positivos o ya cubiertos (ver «Bandit») |
| Secretos | `detect-secrets` 1.5.0 sobre `git ls-files` + regex de claves sobre `git log --all -p` (973 commits) | árbol e historial | 19 avisos en el árbol, todos falsos positivos; 0 en el historial. `gitleaks` no está instalado: no se corrió |
| Superficie | revisión a mano | guardas, subprocess, SQL, deserialización, rutas de archivo, SSRF, CORS, cookie del celular, `innerHTML` | ver hallazgos |

## Altas

### A1. XSS por manejador inline en los chips de requisitos
- **Evidencia:** `static/app.js:1405`, `:1413`, `:1421` y `:1468`: `onclick="ciclarEstadoTag(${activeId}, '${safeNombre}')"`, con `safeNombre = escapeHtml(nombre)` (`static/app.js:1396`).
- **Por qué falla:** el navegador decodifica las entidades del atributo antes de ejecutarlo. `escapeHtml` convierte `'` en `&#39;`, que vuelve a ser `'` dentro del JavaScript. Verificado con un parser HTML: `x&#39;);alert(document.domain);//` queda como `ciclarEstadoTag(1, 'x');alert(document.domain);//')`.
- **Origen del dato:** `nombre` es un requisito de `matriz_oferta`, que extrae la IA del texto de la oferta (terceros: LinkedIn, portales, clipper).
- **Impacto:** quien publica una oferta con un requisito armado ejecuta JavaScript en el portal cuando el creador toca el chip. Ese JavaScript es del mismo origen, así que la guarda lo trata como la PC: lee todo el perfil, edita búsquedas y reglas, y puede aprobar (enviar) mails.
- **Recomendación:** `data-id` y `data-nombre` con `textContent`/`dataset` y un `addEventListener`; nunca datos dentro de `on*=""`. El test `tests/test_seguridad_frontend.py` ya impide sumar casos nuevos.

### A2. XSS por `marked.parse` sin sanitizar del CV generado
- **Evidencia:** `static/app.js:970`: `cvContainer.innerHTML = marked.parse(cvMarkdown)`. marked no sanitiza: deja pasar HTML crudo (`<img onerror=…>`, `<iframe>`), según su documentación. No hay DOMPurify en el portal. `sanitize_cv_formatting` (`core/writer.py:156`) solo ordena negritas, no saca HTML.
- **Origen del dato:** `cv_markdown` lo escribe la IA, con el texto de la oferta (terceros) en el contexto. El prompt lo marca como datos (`server.py:853`), pero no impide que el modelo copie HTML.
- **Impacto:** el mismo de A1, sin necesidad de un clic: basta abrir la oferta. **Probabilidad no demostrada:** requiere que el modelo devuelva el HTML inyectado.
- **Recomendación:** DOMPurify con versión fija y SRI sobre la salida de `marked`, o escapar el HTML antes (`marked.use({ renderer: { html: escapar } })`). Lo más barato y sin librería nueva: lo segundo.

### A3. Pillow 12.3.0 con corrupción de memoria, y el OCR abre imágenes de terceros
- **Evidencia:** `pillow==12.3.0` instalado (transitiva de `reportlab`, `rapidocr`; no está fijada en `requirements.txt`). `pip-audit`: 42 avisos, todos corregidos en 13.0.0, entre ellos escrituras fuera de buffer al decodificar JPEG (PYSEC-2026-4332 / CVE-2026-112403) y FlashPix (PYSEC-2026-4330 / CVE-2026-112398), buffers de TIFF (CVE-2026-112395, CVE-2026-112404), memoria sin tope con BMP (CVE-2026-96638) y la libopenjp2 empaquetada (GHSA-78g4-xqwv-q3g8).
- **Camino:** `core/image_reader.py:50-77` baja las imágenes de los posts (`media.licdn.com/…/feedshare…`, filtrado en `core/linkedin_parser.py:481`) y se las pasa a RapidOCR, que las abre con `Image.open(BytesIO(img))` (`rapidocr/utils/load_image.py:84`). Cualquiera que publique en LinkedIn controla esa imagen.
- **Mitigación posible, no verificada:** LinkedIn recomprime las imágenes que se suben; eso reduce, no anula, el riesgo.
- **Recomendación:** fijar `pillow==13.0.x` en `requirements.txt` (hoy no figura) y correr la suite: no está verificado que `reportlab` 5.1.0 y `rapidocr` 3.10.0 acepten 13.

## Medias

### M1. Scripts de CDN sin versión fija
- **Evidencia:** `static/index.html:9` `https://cdn.tailwindcss.com` (hoy redirige a 3.4.17; es el «Play CDN», que Tailwind no recomienda fuera de desarrollo) y `static/index.html:10` `https://cdn.jsdelivr.net/npm/marked/marked.min.js` (hoy sirve 18.2.1).
- **Impacto:** cualquier versión nueva, o un paquete npm comprometido, corre en el portal con el mismo acceso que A1, sin que nadie lo apruebe. También puede romper la interfaz sin aviso (cadena de suministro).
- **Recomendación:** fijar la versión exacta en la URL (`marked@18.2.1`) y, mejor, servir las cuatro librerías desde `static/vendor/` (el portal funciona además sin internet). Tailwind: compilar el CSS (CLI) en vez del Play CDN.

### M2. Ningún recurso de CDN tiene `integrity` (SRI)
- **Evidencia:** `static/index.html:9-11` y `static/tickets.js:13` + `:208-213` (html2canvas 1.4.1 se carga dinámico, con `crossOrigin` pero sin `s.integrity`).
- **Impacto:** si el CDN entrega otro archivo (compromiso o error), el navegador lo ejecuta igual.
- **Recomendación:** `integrity="sha384-…"` + `crossorigin="anonymous"` en los cuatro, después de fijar la versión (M1). Con `static/vendor/` deja de hacer falta.

### M3. SSRF desde los links de los posts de terceros
- **Evidencia:** `server.py:2345-2354` lee hasta 2 links `https://` de cada post (texto o hrefs, cualquier host); `server.py:2316-2330` sigue además el destino del aviso de LinkedIn; `core/scraper.py:478-489` los baja con `urllib`:
  - sin filtro de destino: IPs privadas, loopback o link-local pasan;
  - `urllib` sigue redirecciones, incluso de `https` a `http`: un link externo puede redirigir a `http://127.0.0.1:1111/api/…`. Esa request llega con cliente loopback, `Host` local y sin `Origin`, y la guarda la acepta como de la PC (solo GET);
  - sin tope de tamaño (`response.read()`);
  - TLS desactivado (`check_hostname = False`, `CERT_NONE`, `core/scraper.py:482-483`).
- **Impacto:** quien publica un post hace que la PC haga GET a la red local o al propio portal. La respuesta (p. ej. datos del perfil) entra como texto de la oferta al análisis IA. Saldría del equipo solo si el creador aprueba un mail que la contenga. También permite colgar la memoria con una respuesta enorme.
- **Recomendación:** un lector común que, en cada salto, resuelva el host y rechace IPs no públicas; redirecciones limitadas; tope de bytes (como `image_reader._MAX_BYTES`); TLS verificado. `POST /api/ofertas/url` (URL que pega el creador) puede usar el mismo lector.

### M4. Sin protección contra clickjacking ni cabeceras de seguridad
- **Evidencia:** ni `X-Frame-Options` ni `Content-Security-Policy` en ninguna respuesta (`grep` vacío en `server.py`, `core/`, `static/`); `/` en `server.py:3051-3057`.
- **Impacto:** una página que el creador abre en la misma PC puede poner el portal (`http://localhost:1111`) en un iframe invisible y hacerle tocar «Aprobar» (envío de mail) o «Regenerar clave». Las requests del iframe son del mismo origen y pasan la guarda.
- **Recomendación:** middleware con `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'` y `X-Content-Type-Options: nosniff`. Una CSP completa (`script-src`) exige primero sacar los `onclick` inline (A1).

## Bajas

### B1. pypdf 6.19.0: 18 avisos de denegación de servicio
- **Evidencia:** `requirements.txt` `pypdf==6.19.0`; CVE-2026-113786…113795, 115572, 115580, 115585, 119203…119209 (bucles infinitos y memoria con PDF armados), corregidos hasta 6.21.4.
- **Por qué baja:** el único uso (`core/cv_maestro.py:1468`) lee el PDF que genera el propio portal. No entra PDF de terceros.
- **Recomendación:** subir a `pypdf==6.21.4` cuando se toquen las dependencias.

### B2. Otras transitivas con avisos
- `soupsieve` 2.10 (de `beautifulsoup4`), CVE-2026-126190/126191/126193: ReDoS y recursión en el selector. Los selectores son constantes del código; el HTML es de LinkedIn.
- `certifi` 2026.7.22 (de `requests`, `httpx`, `curl_cffi`), PYSEC-2026-4320: confía en una raíz de CA retirada.
- `opentelemetry-api` 1.45.0 (de `fastapi`), CVE-2026-114603/126360: ReDoS al leer `tracestate`/`traceparent`. **No verificado** que FastAPI parsee esa cabecera de las requests entrantes.
- Ninguna está fijada en `requirements.txt`. **Recomendación:** un archivo de restricciones (`constraints.txt`) o fijarlas, y que Dependabot las suba.

### B3. Cookie del celular por HTTP plano, sin vencimiento de la clave
- **Evidencia:** `core/radar_api.py:941-951` (la cookie es la clave misma; `secure` solo con https) y `core/acceso_remoto.py:20-34` (la clave dura hasta regenerarla, la cookie un año). El QR lleva la clave en la URL (`core/radar_api.py:927`), que queda en el historial del celular.
- **Impacto:** en una red compartida, quien capture el tráfico al `http://<IP>:<puerto>` obtiene acceso completo hasta que se regenere la clave. En la red de la casa, poco probable.
- **Recomendación:** cookie con un token propio por dispositivo (no la clave), revocable; el link del QR, de un solo uso.

### B4. Enlaces de Computrabajo concatenados a la base
- **Evidencia:** `core/portales.py:224`: `base.rstrip("/") + link.group(1)`. Un `href` que empiece con `@otro.host/…` daría `https://ar.computrabajo.com@otro.host/…` (otro destino).
- **Por qué baja:** el `href` lo escribe Computrabajo, no el autor de la oferta.
- **Recomendación:** `urljoin` y verificar que el host final sea el del portal.

## Bandit: por qué no son hallazgos
- **B608 (31, SQL armado):** los nombres de columna salen de constantes del código o de modelos pydantic con campos fijos (`core/radar_api.py:1048`); los valores van con `?`. `update_post` rechaza columnas desconocidas (`core/radar_store.py:1125`). `update_postulacion` (`core/database.py:274`) no las valida, pero ninguna ruta le pasa claves del usuario. Conviene una lista blanca igual.
- **B310 (6, urlopen):** URLs fijas de APIs de portales (`core/portales.py`) o la del scraper e imágenes, ya cubiertas en M3 y A3.
- **B324 (alta, md5):** huella para agrupar tickets duplicados (`core/tickets.py:251`), no es criptografía.
- **B603/B607/B404 (33):** `subprocess` con lista de argumentos, sin `shell`, comandos fijos (`git`, `taskkill`, `powershell`, Edge headless) y rutas internas.
- **B110/B112 (37):** `try/except: pass`; calidad, no seguridad.
- **B104, B311, B406:** comparación con `"0.0.0.0"`, `random` para pausas (no criptográfico) e `xml.sax.saxutils.escape` (no parsea XML).

## Secretos: por qué no son hallazgos
Los 19 avisos de `detect-secrets` son fixtures HTML (`componentkey` inventados), hashes SHA-256 de plantillas (`tests/test_plantilla_sin_datos.py`), contraseñas de ejemplo de tests (`hunter2`, `usuario:clave@…`) y un comentario de `tools/backup_local.ps1:224`. El historial tiene solo el patrón del tapado de secretos (`core/tickets.py`).

## Lo que está bien (verificado)
- Toda ruta de la API tiene guarda (`_GUARDA`, router del Radar y de tickets). Las abiertas son `/`, `/static` y `/api/radar/emparejar` (clave con `compare_digest`). `tests/test_guardas_rutas.py` lo controla.
- La guarda rechaza túnel de Cloudflare, `Host` ajeno (DNS rebinding), `Sec-Fetch-Site: cross-site` y `Origin` distinto.
- CORS solo en el clipper, solo `https` de 4 dominios, nunca `*`.
- Sin `pickle`, `yaml.load`, `eval` ni `shell=True`.
- Rutas de archivo: el perfil usa lista blanca (`core/perfil_candidato.py:62`), los aprendizajes son claves de un dict, los tickets van por id entero y los nombres de export son solo `[A-Za-z0-9]`.
- El PDF del CV escapa todo antes del HTML (`core/pdf_generator.py:106`); el HTML de los tickets se sirve como texto plano con `nosniff`.
- El resto de los `innerHTML` revisados usan `escapeHtml` en texto (no en atributos `on*`).

## Fuera de alcance
El Python global de la PC tiene además `pip` 26.1.2, `setuptools` 78.1.0, `pyasn1` 0.6.4 (de `google-auth`) y `torch` (no auditable), que no son dependencias de LinkedOut. Producción usa ese mismo Python: actualizarlo es decisión del creador.
