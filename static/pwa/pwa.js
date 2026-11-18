// App de escritorio (PWA): registra el service worker y ofrece «Instalar app» solo en la PC.
// En el celular por http (IP de la red) no hay contexto seguro: ni service worker ni oferta de instalar.
(function () {
  "use strict";

  const HOSTS_PC = ["localhost", "127.0.0.1"];

  function instalada() {
    return window.matchMedia("(display-mode: standalone)").matches;
  }

  /** El botón se ofrece solo en la propia PC y si la app no está abierta ya como app. */
  function puedeOfrecer(hostname, yaInstalada) {
    return HOSTS_PC.includes(hostname) && !yaInstalada;
  }

  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("[App] sin service worker:", e));
  }

  let pedido = null; // el evento beforeinstallprompt guardado hasta el clic

  function ocultar() {
    const lugar = document.getElementById("pwaInstalar");
    if (lugar) lugar.hidden = true;
  }

  async function instalar() {
    if (!pedido) return;
    const evento = pedido;
    pedido = null;
    ocultar();
    await evento.prompt();
  }

  function mostrar() {
    const lugar = document.getElementById("pwaInstalar");
    if (!lugar) return;
    if (!document.getElementById("pwaInstalarBtn")) {
      const boton = document.createElement("button");
      boton.type = "button";
      boton.id = "pwaInstalarBtn";
      boton.title = "Instalar LinkedOut como app de la PC";
      boton.className = "px-2.5 py-1.5 rounded-md bg-[#1e2026] hover:bg-[#262930] text-zinc-300 border border-[#262930] text-xs font-medium transition-colors";
      const icono = document.createElement("i");
      icono.className = "fa-solid fa-desktop mr-1.5";
      icono.setAttribute("aria-hidden", "true");
      boton.appendChild(icono);
      boton.appendChild(document.createTextNode("Instalar app"));
      boton.addEventListener("click", instalar);
      lugar.appendChild(boton);
    }
    lugar.hidden = false;
  }

  window.addEventListener("beforeinstallprompt", (evento) => {
    if (!puedeOfrecer(location.hostname, instalada())) return;
    evento.preventDefault();
    pedido = evento;
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mostrar);
    else mostrar();
  });

  window.addEventListener("appinstalled", () => { pedido = null; ocultar(); });

  window.LinkedOutApp = { puedeOfrecer };
})();
