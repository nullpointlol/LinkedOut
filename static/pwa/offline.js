// Pantalla «LinkedOut no está corriendo»: prueba si el portal volvió y recarga sola.
// Cada cuánto se prueba
const REINTENTO_SEGUNDOS = 5;
const estado = document.getElementById("estado");

async function probar() {
  try {
    const r = await fetch("/", {cache: "no-store"});
    if (r.ok) { location.reload(); return; }
  } catch (e) { /* sigue apagado */ }
  estado.textContent = "Sin respuesta a las " + new Date().toLocaleTimeString("es-AR") + ". Reintentando…";
}

document.getElementById("reintentar").addEventListener("click", probar);
setInterval(probar, REINTENTO_SEGUNDOS * 1000);
