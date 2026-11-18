# Plan de seguridad: controles permanentes y arreglos

Origen: [auditoria-20261117.md](auditoria-20261117.md). Hoy no había ningún control de vulnerabilidades; los tests de guardas (`tests/test_guardas_rutas.py`) cubren solo la superficie de la API.

## Controles permanentes

| Control | Qué atrapa | Dónde corre | Cuándo | Estado |
|---|---|---|---|---|
| `tests/test_seguridad_frontend.py` | CDN sin versión exacta, CDN sin `integrity`, `on*="…${…}"` en `static/` (trinquete: lo conocido pasa, lo nuevo falla, lo arreglado obliga a bajarlo de la lista) | suite (compuerta nocturna hoy; CI después), `tools/tests_rapidos.py` si se toca | cada corrida | **hecho** (esta rama) |
| `pip-audit -r requirements.txt -r requirements-dev.txt` | CVE en dependencias fijadas | CI público, job `seguridad` | push, PR y semanal (`schedule`) | Fase 2 |
| `pip-audit` sobre el entorno instalado del CI (`pip install -r requirements-dev.txt` y después `pip-audit`) | CVE en transitivas (Pillow, certifi, soupsieve…) | CI público, mismo job | igual | Fase 2 |
| `bandit -r core server.py tools -x tools/agents` con baseline (`bandit -f json -o .bandit-baseline.json` hoy; en CI `-b .bandit-baseline.json`) | código nuevo con patrones peligrosos (`shell=True`, `pickle`, `eval`, SQL armado nuevo) | CI público | push y PR | Fase 2 |
| CodeQL (`github/codeql-action`, Python y JavaScript) | XSS, SSRF, path traversal, inyección, con flujo de datos | GitHub (gratis en repo público) | push, PR y semanal | Fase 2 |
| Dependabot (`.github/dependabot.yml`: `pip` y `github-actions`, semanal) | versiones con CVE: abre PR con la suba | GitHub | semanal y ante aviso | Fase 2 |
| Dependabot alerts + security updates | aviso de CVE nuevo sobre lo fijado | GitHub (configuración del repo) | continuo | Fase 2, con OK del creador (configuración externa) |
| Secret scanning + push protection | claves en un push | GitHub (configuración del repo) | cada push | Fase 2, con OK del creador |
| `gitleaks` sobre la semilla | claves en todo el historial | PC, una vez antes de publicar (precondición de la Fase 2) | una vez | Fase 2 (ya está en `plan-publico.md`) |
| `tools/auditar_datos.py` | datos personales (mails, teléfonos, rutas) | compuerta y CI | ya existe | sin cambios |

Notas:
- `pip-audit` y `bandit` van en `requirements-dev.txt` con versión exacta (como el resto) recién en la Fase 2; hoy no, para no cambiar lo que instala la compuerta.
- El job `seguridad` falla con un CVE de severidad alta o crítica en algo que usa la app. Lo bajo se reporta sin frenar (`pip-audit --ignore-vuln <id>` documentado acá, con motivo y fecha, para lo aceptado).
- CodeQL y Dependabot no tocan producción ni la PC: corren en GitHub.

## Arreglos, en orden de prioridad
Criterio: primero lo que un tercero (autor de un post u oferta) puede disparar sin que el creador haga nada raro; después la cadena de suministro; al final lo que pide acceso a la red local o es solo DoS sobre datos propios. Cada arreglo, con test rojo antes (AGENTS.md).

| # | Hallazgo | Arreglo | Archivos | Choca con | Medida de «hecho» |
|---|---|---|---|---|---|
| 1 | A1 XSS en chips | `dataset` + `addEventListener`, sin `onclick` inline | `static/app.js` | S5 (edita `app.js`) | `HANDLERS_CONOCIDOS` vacío; test de que un requisito `x');alert(1);//` queda como texto |
| 2 | A2 XSS en `marked` | escapar HTML crudo en `marked` (renderer `html`) | `static/app.js` | S5 | test de navegador: `<img src=x onerror=…>` en el CV no crea `img` |
| 3 | A3 Pillow | `pillow==13.0.x` en `requirements.txt` | `requirements.txt` | nadie | `pip-audit` sin avisos de Pillow; suite completa en verde (reportlab y OCR) |
| 4 | M4 clickjacking | middleware: `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff` | `server.py` | S5 (edita `server.py`) | test: `/` y `/api/...` traen las tres cabeceras |
| 5 | M1 + M2 CDN | servir marked, html2canvas y Font Awesome desde `static/vendor/` con versión fija; Tailwind compilado | `static/index.html`, `static/tickets.js`, `static/vendor/` | S5 y el rediseño (que ya cambia la interfaz) | `SIN_VERSION_CONOCIDOS` y `SIN_INTEGRITY_CONOCIDOS` vacíos |
| 6 | M3 SSRF | lector común: IP pública en cada salto, redirecciones limitadas, tope de bytes, TLS verificado | `core/scraper.py` (+ uso en `server.py`) | S5 en `server.py`, poco | tests: `https://127.0.0.1`, IP privada y redirección a loopback rechazadas; respuesta de más de N MiB cortada |
| 7 | B1, B2 | `pypdf==6.21.4`; `constraints.txt` con certifi, soupsieve, opentelemetry-api | `requirements*.txt` | nadie | `pip-audit` en verde |
| 8 | B3 cookie del celular | token por dispositivo, revocable; QR de un solo uso | `core/acceso_remoto.py`, `core/radar_api.py` | nadie | tests de emparejamiento existentes + revocación |
| 9 | B4 Computrabajo | `urljoin` y host verificado | `core/portales.py` | nadie | test con `href="@otro.host/x"` |

Los arreglos 1, 2 y 4 dependen de que S5 termine con `app.js` y `server.py`. El 5 conviene hacerlo dentro del rediseño. Los 3, 7, 8 y 9 no chocan con nadie.

## Decisiones para el creador
1. **Orden y momento:** ¿se arreglan las 3 altas antes de publicar el repo (Fase 2), o después? Publicar no las empeora (el riesgo es por contenido de terceros, no por el código visible), pero quedan a la vista.
2. **Librerías locales:** ¿`static/vendor/` (sin CDN, funciona sin internet) o CDN con versión fija + SRI?
3. **Configuración de GitHub** (Dependabot alerts, secret scanning, push protection, CodeQL): se activa en la Fase 2 con tu OK.
4. **Python global de la PC** (lo usa producción): `pip`, `setuptools` y `pyasn1` tienen CVE pero no son de LinkedOut. ¿Se actualizan?

## Estado (18/11)
Aprobado por el creador el 17/11 con estas decisiones: altas y medias antes de publicar el repo; librerías del navegador en `static/vendor/`; Tailwind por CDN con versión fija hasta el rediseño; M4 solo `X-Frame-Options` y `frame-ancestors`; la configuración de GitHub en la Fase 2; el Python global lo actualiza el coordinador.
- **Arreglados:** A1, A2, A3, M1, M2, M3 y M4, cada uno con su test (detalle en `ESTADO.md` de la rama).
- **Excepción:** Tailwind (`cdn.tailwindcss.com/3.4.17`) no puede llevar `integrity`: ese CDN no manda CORS y el navegador bloquearía el script. Se cierra con el rediseño (CSS compilado) o copiándolo a `static/vendor/`.
- **Pendientes:** B1–B4.
