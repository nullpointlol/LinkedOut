// Service worker de la app de escritorio (servido en /sw.js para controlar las navegaciones a "/").
// Solo atiende navegaciones: red primero y, si el portal no responde, la pantalla «LinkedOut no está corriendo».
// No guarda la página ni nada de la API: cada deploy se ve al abrir y ningún dato del candidato queda en caché.
const VERSION = "1";
const PREFIJO = "linkedout-sin-portal-";
const CACHE = PREFIJO + VERSION;
const SIN_PORTAL = "/static/pwa/offline.html";
const GUARDADOS = [SIN_PORTAL, "/static/pwa/offline.css", "/static/pwa/offline.js", "/static/pwa/icono.svg"];

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(CACHE).then((c) => c.addAll(GUARDADOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(claves.filter((k) => k.startsWith(PREFIJO) && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (evento) => {
  const pedido = evento.request;
  if (pedido.mode === "navigate") {
    evento.respondWith(fetch(pedido).catch(() => caches.match(SIN_PORTAL).then((r) => r || new Response(
      "LinkedOut no está corriendo.", {status: 503, headers: {"Content-Type": "text/plain; charset=utf-8"}}))));
    return;
  }
  // Lo que usa la pantalla sin portal (estilos, script, ícono): red primero y, apagado el portal, la copia guardada
  const url = new URL(pedido.url);
  if (pedido.method === "GET" && url.origin === self.location.origin && GUARDADOS.includes(url.pathname)) {
    evento.respondWith(fetch(pedido).catch(() => caches.match(url.pathname)));
  }
  // Todo lo demás (API, SSE, resto de los estáticos) pasa sin tocar
});
