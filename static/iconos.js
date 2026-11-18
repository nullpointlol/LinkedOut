// Íconos del rediseño: <svg class="ico"><use href="/static/iconos.svg#…"> (sprite local, tools/armar_iconos.py).
// Se carga antes que app.js y radar.js; los dos usan estas funciones.
(function () {
  const NS = 'http://www.w3.org/2000/svg';

  /** Crea un <svg class="ico"> con un ícono del sprite local. */
  function crearIcono(nombre, clase) {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'ico' + (clase ? ' ' + clase : ''));
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(NS, 'use');
    use.setAttribute('href', '/static/iconos.svg#' + nombre);
    svg.appendChild(use);
    return svg;
  }

  /** Cambia el ícono de un <svg><use> ya dibujado. */
  function ponerIcono(svg, nombre) {
    const use = svg && svg.querySelector('use');
    if (use) use.setAttribute('href', '/static/iconos.svg#' + nombre);
  }

  window.crearIcono = crearIcono;
  window.ponerIcono = ponerIcono;
})();
