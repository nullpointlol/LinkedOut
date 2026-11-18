# App de escritorio de LinkedOut: plan (aprobado por el creador el 17/11)

**Pedido del creador (17/11):** abrir LinkedOut como una aplicación de la PC, sin escribir `localhost` en el navegador; no un acceso directo. En el celular se sigue entrando por QR.
**Invariantes:** el portal anda igual en el navegador; producción no se toca; nada nuevo expone datos sin la guarda de S1.
**Prioridad ante conflicto:** simple y sin mantenimiento antes que vistoso.

**Recomendación: PWA instalada desde Edge sobre `http://localhost:1111`** (opción a), con un service worker mínimo que solo muestra «LinkedOut no está corriendo» si el portal está apagado.

| Qué se mide | Meta | Cómo |
|---|---|---|
| Instalable | Chromium no da errores de instalabilidad | test Playwright: CDP `Page.getInstallabilityErrors` → `[]` en el portal de prueba |
| Portal apagado | se ve la pantalla propia, no el error del navegador | test Playwright: con la red cortada, navegar a `/` muestra «LinkedOut no está corriendo» |
| Cero datos nuevos expuestos | las rutas nuevas no devuelven datos; el SW no guarda nada de `/api` | `test_guardas_rutas.py` (lista blanca con motivo) + test del contenido de `sw.js` |
| Navegador igual que hoy | suite verde, sin cambios en los tests de UI | compuerta |
| Cada deploy se ve al abrir la app | `/` sigue `no-cache` y el SW nunca guarda `/` | test de cabeceras + test del SW |

---

## 1. Opciones evaluadas (Windows 11)

### (a) PWA instalable desde Edge o Chrome. **Recomendada**
- **Por qué funciona por http:** `localhost` y `127.0.0.1` son contexto seguro para el navegador; manifest, instalación y service worker andan sin certificado.
- **Qué necesita:** un manifest (`name`, `start_url: "/"`, `scope: "/"`, `display: "standalone"`, íconos PNG 192 y 512 px, uno `maskable`), un `<link rel="manifest">` y un `theme-color`. Desde 2023, Chromium no exige service worker para instalar (dato de la documentación de Chrome, no verificado acá; lo verifica el test de instalabilidad).
- **Resultado:** ventana propia sin barra de direcciones, ícono de LinkedOut en Inicio y en la barra de tareas, `Alt+Tab` como app aparte. Edge permite además abrirla al iniciar sesión (`edge://apps`); no verificado en esta PC.
- **Portal apagado:** sin service worker se ve el error de Edge («No se puede acceder a este sitio»). Con un SW de ~30 líneas que solo atiende navegaciones: si la red falla, muestra `offline.html` propia («LinkedOut no está corriendo; el vigilante lo levanta solo») y reintenta cada `REINTENTO_SEGUNDOS` (constante con nombre); al volver el portal, recarga sola. No puede arrancar el portal: un navegador no lanza procesos (y eso es del vigilante).
- **Actualización con cada deploy:** la app es el portal vivo. `/` ya se sirve con `Cache-Control: no-cache` y el SW no guarda ni `/` ni la API: cada deploy se ve en la próxima apertura. El SW solo guarda `offline.html` y el ícono, en una caché con versión; el navegador compara `sw.js` byte a byte en cada navegación. Nombre o ícono nuevos del manifest los toma Edge solo (puede tardar hasta un día).
- **Instalación:** un clic. El botón «Instalar app» aparece solo si el navegador disparó `beforeinstallprompt` (Edge o Chrome, la app aún no instalada, en `localhost`). También se puede desde el menú de Edge (*Aplicaciones → Instalar LinkedOut*) o desde el ícono de la barra de direcciones.
- **Contras:** depende de Edge o Chrome (en Windows 11, Edge viene siempre). `localhost:1111` y `127.0.0.1:1111` son orígenes distintos: se instala desde la que se usa hoy. Mientras el portal cargue Tailwind, Font Awesome y marked por CDN, sin internet la app se ve rota igual que la pestaña (lo arregla la E5 del rediseño).
- **Mantenimiento:** casi nulo. Cuatro archivos estáticos y una ruta; nada que compilar ni empaquetar.

### (b) Ventana nativa con pywebview y WebView2
- **Pros:** ventana 100 % propia; puede mostrar su propio aviso si el portal no responde.
- **Contras:** dependencias nuevas (`pywebview`, `pythonnet`), un proceso Python más que el guardián tiene que conocer, un lanzador `.pyw` o un `.exe` empaquetado (PyInstaller) y, para abrirlo, igual hace falta un acceso directo. Si además arrancara el portal, chocaría con la tarea `LinkedOut-portal` y el vigilante. Más código, más mantenimiento, y no sirve para el celular.
- **Veredicto:** no compensa; lo que da de más (aviso propio) la PWA lo logra con el SW.

### (c) Edge en modo `--app`
- **Pros:** cero código: un `.lnk` con `msedge.exe --app=http://localhost:1111`.
- **Contras:** es justo el acceso directo que el creador descartó; ícono genérico salvo un `.ico` aparte; sin pantalla propia si el portal está apagado; queda fuera del repo (un `.lnk` por PC).
- **Veredicto:** plan B si Edge no ofreciera instalar la PWA.

---

## 2. Qué cambia en el código

Todo nuevo y aislado; en archivos existentes, unas pocas líneas.

| Archivo | Cambio | Pública |
|---|---|---|
| `static/pwa/manifest.webmanifest` (nuevo) | `id`, `name` y `short_name` «LinkedOut», `start_url` y `scope` `/`, `display: standalone`, `background_color` y `theme_color` del fondo de hoy (`#0f1115`; el rediseño lo cambia a su token), íconos | sí, ya está bajo `/static` (lista blanca de S1); Python 3.14 le da `application/manifest+json` (verificado) |
| `static/pwa/icono-192.png`, `icono-512.png`, `icono-maskable-512.png`, `icono.svg` (nuevos) | marca LinkedOut (hoy no hay logo en el repo: se dibuja uno simple, monograma sobre `#0B0E13` con el acento `#78A9F8`). Los PNG se versionan ya generados; nada se compila | sí, `/static` |
| `static/pwa/offline.html`, `offline.css`, `offline.js` (nuevos) | sin CDN ni código en línea, sin datos; mensaje y reintento | sí, `/static` |
| `static/pwa/pwa.js` (nuevo) | registra el SW solo si `isSecureContext`; muestra «Instalar app» solo si llega `beforeinstallprompt`, el host es `localhost` o `127.0.0.1` y no está en `display-mode: standalone`. `createElement` + `textContent` | sí, `/static` |
| `server.py` | una ruta `GET /sw.js` que sirve `static/pwa/sw.js` con `no-cache` y `text/javascript`. Hace falta en la raíz: un SW en `/static/` solo controla `/static/` y no las navegaciones a `/` | **sí, nueva**: entra a `ABIERTAS` de `tests/test_guardas_rutas.py` con su motivo («código del service worker, sin datos») |
| `static/pwa/sw.js` (nuevo) | navegaciones (`request.mode === "navigate"`): red primero, y si falla, `offline.html`. Fuera de las navegaciones, solo atiende los 3 archivos de esa pantalla (red primero, caché con el portal apagado); API, SSE y el resto pasan sin tocar | vía `/sw.js` |
| `static/index.html` | 4 líneas en `<head>` (`<link rel="manifest">`, `theme-color`, ícono, `<script src="/static/pwa/pwa.js">`), `<title>` «LinkedOut» (hoy dice el nombre viejo, y es lo que muestra la ventana) y un `<span id="pwaInstalar" hidden>` en el header donde `pwa.js` pone el botón | — |
| `tests/inventario_botones.json` | la clave del botón «Instalar app» | — |

**Guardas (S1):** la API sigue igual, solo para la PC o el celular emparejado. Lo nuevo es código público (como `app.js`) y no lee nada de `<DATA_DIR>`. El SW no guarda respuestas de `/api` ni de `/`, así que no queda ningún dato del candidato en la caché del navegador. Si un día hiciera falta sacar el SW, `/sw.js` pasa a servir uno que se desregistra solo (kill switch) y `pwa.js` deja de registrarlo.

**Convivencia con S5 y el rediseño:** S5 toca el menú Herramientas (cuerpo de `index.html`, `app.js`, `server.py`); esto toca el `<head>`, una línea del header y una ruta al final de `server.py`, lejos de esas zonas. Propuesta: **entrega mínima y aislada, después de que S5 llegue a `develop`**, traer `develop` y entregar. El rediseño E1 rehace el header y conserva todos los ids (regla de su §9): conserva `#pwaInstalar` y el `<head>`. Si el creador prefiere no tocar `index.html` antes del rediseño, la alternativa es meter estas 6 líneas en la E1; la app de escritorio quedaría esperando la Fase 2.

---

## 3. Celular y otros dispositivos

Hoy el celular entra por `http://<IP de la PC>:1111` (QR del botón «Celular», cookie de emparejamiento). **Por http en una IP no es contexto seguro:** no hay service worker ni `beforeinstallprompt`, así que Chrome no la instala como PWA. «Agregar a la pantalla de inicio» crea un acceso que abre el navegador; en iPhone, Safari suele abrirla a pantalla completa con el manifest (no verificado).

| Opción | Qué da | Costo | Riesgo |
|---|---|---|---|
| **1. Seguir con QR + «Agregar a pantalla de inicio»** (recomendada hoy) | ícono en el celular; abre en el navegador | cero; ya anda | ninguno nuevo |
| 2. HTTPS local con certificado propio (mkcert) | PWA instalable en la LAN | CA propia instalada y confiada en cada celular (en iPhone, a mano en Ajustes); certificado para la IP o un nombre local (si cambia la IP del router, se rehace); uvicorn con TLS → cambia cómo arranca producción (OK del creador) | la clave de la CA queda en la PC: si se filtra, permite interceptar cualquier sitio en esos celulares |
| 3. Túnel con HTTPS real: Tailscale (`tailscale serve`) | PWA instalable; anda también fuera de casa; certificado válido sin CA propia | cuenta y app de Tailscale en PC y celular; un servicio externo ve metadatos de la conexión | bajo: solo los dispositivos de la red privada llegan; la guarda de S1 sigue exigiendo el emparejamiento (para el portal, el Host del túnel no es local) |
| 4. Túnel público (Cloudflare Tunnel) | ídem, desde cualquier lugar | cuenta y dominio | alto: el portal queda en internet y solo lo protege la cookie; S1 ya detecta sus cabeceras como «no local» |

Ninguna se implementa en este plan. Si el creador quiere la app también en el celular, la más simple y segura es la 3; es una decisión suya (servicio externo).

---

## 4. Pruebas

- **TDD**, en `tests/test_app_escritorio.py`, con el candidato inventado:
  - manifest: JSON válido, `name` «LinkedOut», `start_url` y `scope` `/`, `display` `standalone`, íconos 192 y 512 que existen y son PNG del tamaño declarado (se leen los bytes del encabezado, sin Pillow), uno `maskable`;
  - `/sw.js`: 200 desde afuera, `text/javascript`, `no-cache`; el código no menciona `/api` ni guarda `/`;
  - `offline.html` y `pwa.js`: sin URLs externas ni `innerHTML`; texto de 12 px como mínimo;
  - `index.html`: tiene el `<link rel="manifest">` y el script;
  - `test_guardas_rutas.py`: `/sw.js` en `ABIERTAS`, con motivo (falla si se suma sin anotarla).
- **Playwright (Chromium), marcados como lentos:**
  - instalabilidad: CDP `Page.getInstallabilityErrors` vacío (Lighthouse ya no tiene categoría PWA desde la v12, así que no sirve);
  - SW activo tras la primera carga; con `context.set_offline(True)`, navegar a `/` muestra «LinkedOut no está corriendo»; al volver la red, recarga sola;
  - el botón «Instalar app» no aparece con un Host que no sea `localhost` ni `127.0.0.1` (un Host de test de `ESTADO_HOSTS_DE_TEST`, como el celular).
- **Prueba manual en Edge** (portal de prueba 1199 con datos inventados de `config/ejemplo/`, lanzado con `tools/temporal.py`):
  1. abrir `http://localhost:1199`: aparecen «Instalar app» y el ícono de instalar en la barra de direcciones;
  2. instalar: ventana propia, ícono en Inicio y en la barra de tareas;
  3. vencer el temporal (portal apagado) y abrir la app: pantalla propia;
  4. relanzar el portal: la app recarga sola.
  Capturas a 1200 px. El 1199 se instala como app aparte (otro puerto, otro origen); se desinstala desde `edge://apps` al terminar.
- **En producción**, recién con el deploy aprobado por el creador: él la instala desde `http://localhost:1111` con un clic.

**Prueba de concepto:** reemplazada por los tests de navegador de arriba, que corren con la entrega.

---

## Decisiones del creador (17/11)
1. **PWA** desde Edge sobre `localhost:1111`.
2. **Entrega chica** apenas S5 llegue a `develop` (`index.html` y `server.py` recién ahí, rebasando sobre `origin/develop`).
3. **Celular:** sigue el QR + «Agregar a la pantalla de inicio»; sin Tailscale.

**Ajuste por la auditoría de seguridad (17/11):** como se van a sumar cabeceras anti-clickjacking y quizá una CSP, `offline.html` no lleva estilos ni scripts en línea (`offline.css`, `offline.js`) y nada de la PWA usa CDN. El SW guarda esos dos y el ícono, y los sirve desde la caché solo con el portal apagado.
