// Tickets de errores (core/tickets.py): captura de errores del navegador, botón flotante para reportar y vista «Tickets».
// Se carga antes que el resto de los scripts para capturar también los errores de su carga.
// Sin innerHTML con datos: todo se arma con createElement + textContent.
(function () {
  'use strict';

  const fetchOriginal = window.fetch.bind(window);
  const consola = [];
  const llamadas = [];
  const enviados = new Map();
  let reportesAutomaticos = 0;
  const MAX_AUTOMATICOS = 30;
  const URL_HTML2CANVAS = '/static/vendor/html2canvas-1.4.1/html2canvas.min.js';  // copia local (seguridad)

  function recortar(lista, max) { while (lista.length > max) lista.shift(); }
  function texto(v) {
    if (v == null) return '';
    if (typeof v === 'string') return v;
    if (v instanceof Error) return `${v.name}: ${v.message}`;
    try { return JSON.stringify(v); } catch (_) { return String(v); }
  }
  function rutaDe(url) {
    try { const u = new URL(String(url), location.href); return u.origin === location.origin ? u.pathname + u.search : u.href; }
    catch (_) { return String(url); }
  }
  function esPropia(ruta) { return ruta.startsWith('/api/tickets') || ruta.startsWith('/api/stream'); }

  // ------------------------------------------------------------ contexto

  function vistaActual() {
    if (window.vistaActual) return window.vistaActual;
    const t = document.getElementById('vista-tickets');
    if (t && !t.hidden) return 'tickets';
    const p = document.getElementById('vista-perfil');
    if (p && !p.hidden) return 'perfil';
    return 'manual';
  }
  function contexto() {
    const datos = { vista: vistaActual(), url: location.pathname + location.search + location.hash,
      pantalla: `${window.innerWidth}x${window.innerHeight}`, navegador: navigator.userAgent,
      consola: consola.slice(-30), llamadas: llamadas.slice(-20) };
    try {
      const S = window.Radar && window.Radar._test && window.Radar._test.S;
      if (S && S.detalle && S.detalle.id) datos.post_id = S.detalle.id;
    } catch (_) { /* sin Radar */ }
    try {
      // `ofertaActual` es global de app.js (let de nivel superior)
      // eslint-disable-next-line no-undef
      if (typeof ofertaActual !== 'undefined' && ofertaActual && ofertaActual.id) datos.oferta_id = ofertaActual.id;
    } catch (_) { /* sin app.js */ }
    return datos;
  }

  async function enviarTicket(cuerpo) {
    const r = await fetchOriginal('/api/tickets', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo),
    });
    if (!r.ok) throw new Error(`El portal respondió ${r.status}`);
    return r.json();
  }

  function reportarAutomatico(tipo, mensaje, traza) {
    const clave = `${tipo}|${mensaje}`;
    const ahora = Date.now();
    if (reportesAutomaticos >= MAX_AUTOMATICOS) return;
    if (enviados.has(clave) && ahora - enviados.get(clave) < 10000) return;
    enviados.set(clave, ahora);
    reportesAutomaticos += 1;
    const datos = contexto();
    datos.consola = datos.consola.slice(-10);
    datos.llamadas = datos.llamadas.slice(-10);
    enviarTicket({ componente: 'navegador', tipo, mensaje: String(mensaje).slice(0, 2000), traza: traza || null, datos })
      .then(() => setTimeout(actualizarBoton, 300))
      .catch(() => { /* sin portal: no hay a dónde reportar */ });
  }

  // ------------------------------------------------------------ captura temprana

  ['error', 'warn'].forEach((nivel) => {
    const original = console[nivel];
    console[nivel] = function (...args) {
      try {
        consola.push({ nivel, texto: args.map(texto).join(' ').slice(0, 500), hora: new Date().toISOString() });
        recortar(consola, 50);
      } catch (_) { /* nunca romper la consola */ }
      return original.apply(this, args);
    };
  });

  window.fetch = async function (recurso, opciones) {
    const metodo = ((opciones && opciones.method) || (recurso && recurso.method) || 'GET').toUpperCase();
    const ruta = rutaDe(recurso && recurso.url ? recurso.url : recurso);
    const inicio = performance.now();
    const registro = { metodo, url: ruta, estado: null, ms: null, hora: new Date().toISOString() };
    if (!esPropia(ruta)) { llamadas.push(registro); recortar(llamadas, 40); }
    try {
      const r = await fetchOriginal(recurso, opciones);
      registro.estado = r.status;
      registro.ms = Math.round(performance.now() - inicio);
      if (r.status === 404 && ruta.startsWith('/api/') && !esPropia(ruta)) {
        // El servidor no registra el 404 de una ruta inexistente (bots); si la pide la pantalla, es un error
        r.clone().json().then((j) => {
          if (j && j.detail === 'Not Found') reportarAutomatico('ruta_inexistente', `La pantalla pidió ${metodo} ${ruta} y esa ruta no existe`);
        }).catch(() => {});
      }
      return r;
    } catch (e) {
      registro.estado = 'falló';
      registro.ms = Math.round(performance.now() - inicio);
      if (!esPropia(ruta) && !(e && e.name === 'AbortError')) {
        reportarAutomatico('fetch_fallido', `Falló la llamada ${metodo} ${ruta}: ${texto(e)}`, e && e.stack);
      }
      throw e;
    }
  };

  window.addEventListener('error', (e) => {
    if (e.target && e.target !== window) return; // recursos (imágenes, scripts) que no cargaron
    const lugar = e.filename ? ` (${rutaDe(e.filename)}:${e.lineno}:${e.colno})` : '';
    reportarAutomatico('js_error', `${e.message || 'Error de JavaScript'}${lugar}`, e.error && e.error.stack);
  }, true);
  window.addEventListener('unhandledrejection', (e) => {
    const r = e.reason;
    reportarAutomatico('promesa_rechazada', `Promesa rechazada sin manejar: ${texto(r)}`, r && r.stack);
  });

  // ------------------------------------------------------------ utilidades de DOM

  function nodo(tag, contenido, clase) {
    const el = document.createElement(tag);
    if (contenido != null) el.textContent = contenido;
    if (clase) el.className = clase;
    return el;
  }
  function boton(textoBoton, alPulsar, atributos) {
    const b = nodo('button', textoBoton);
    b.type = 'button';
    for (const [k, v] of Object.entries(atributos || {})) b.setAttribute(k, v);
    b.addEventListener('click', alPulsar);
    return b;
  }
  const NOMBRES_ESTADO = { abierto: 'Abierto', en_curso: 'En curso', resuelto: 'Resuelto' };


  // ------------------------------------------------------------ botón flotante

  async function actualizarBoton() {
    const b = document.getElementById('btnReportarError');
    if (!b) return;
    try {
      const r = await fetchOriginal('/api/tickets/resumen');
      if (!r.ok) return;
      const resumen = await r.json();
      const hay = Number(resumen.sin_resolver) > 0;
      b.dataset.hayAbiertos = hay ? '1' : '0';
      b.title = hay ? `Reportar un error (hay ${resumen.sin_resolver} sin resolver)` : 'Reportar un error';
    } catch (_) { /* portal caído: queda como estaba */ }
  }

  function cargarHtml2canvas() {
    if (window.html2canvas) return Promise.resolve(window.html2canvas);
    return new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = URL_HTML2CANVAS;
      s.crossOrigin = 'anonymous';
      s.onload = () => resolve(window.html2canvas || null);
      s.onerror = () => resolve(null);
      document.head.append(s);
      setTimeout(() => resolve(window.html2canvas || null), 8000);
    });
  }

  async function capturarPantalla() {
    try {
      const h2c = await cargarHtml2canvas();
      if (!h2c) return null;
      const lienzo = await Promise.race([
        h2c(document.body, {
          logging: false, useCORS: true, scale: Math.min(1, window.devicePixelRatio || 1),
          x: window.scrollX, y: window.scrollY, width: window.innerWidth, height: window.innerHeight,
          ignoreElements: (el) => el.id === 'modalTicket' || el.id === 'btnReportarError',
        }),
        new Promise((resolve) => setTimeout(() => resolve(null), 10000)),
      ]);
      return lienzo ? lienzo.toDataURL('image/png') : null;
    } catch (_) { return null; }
  }

  function htmlDeLaPantalla() {
    try {
      const copia = document.documentElement.cloneNode(true);
      copia.querySelectorAll('script, #modalTicket').forEach((n) => n.remove());
      return copia.outerHTML.slice(0, 3 * 1024 * 1024);
    } catch (_) { return null; }
  }

  function abrirFormulario() {
    const modal = document.getElementById('modalTicket');
    if (!modal) return;
    modal.hidden = false;
    const estado = document.getElementById('ticketEstado');
    if (estado) estado.textContent = '';
    const area = document.getElementById('ticketTexto');
    if (area) area.focus();
  }
  function cerrarFormulario() {
    const modal = document.getElementById('modalTicket');
    if (modal) modal.hidden = true;
  }

  async function enviarFormulario() {
    const area = document.getElementById('ticketTexto');
    const estado = document.getElementById('ticketEstado');
    const enviar = document.getElementById('btnTicketEnviar');
    const mensaje = (area && area.value || '').trim();
    if (!mensaje) { estado.textContent = 'Contanos qué pasó antes de enviar.'; area && area.focus(); return; }
    enviar.disabled = true;
    estado.textContent = 'Guardando el ticket con una captura de la pantalla…';
    try {
      const datos = contexto();
      const [captura, html] = [await capturarPantalla(), htmlDeLaPantalla()];
      const r = await enviarTicket({ componente: 'navegador', tipo: 'reporte_usuario', mensaje, datos, captura, html });
      estado.textContent = `Listo: quedó el ticket #${r.id}.`;
      area.value = '';
      actualizarBoton();
      setTimeout(cerrarFormulario, 1500);
    } catch (e) {
      estado.textContent = `No se pudo guardar el ticket: ${texto(e)}`;
    } finally {
      enviar.disabled = false;
    }
  }

  // ------------------------------------------------------------ vista «Tickets»

  const V = { raiz: null, lista: null, detalle: null, filtros: {}, abierto: null, tickets: [] };

  async function pedirJson(url, opciones) {
    const r = await fetchOriginal(url, opciones);
    let j = null;
    try { j = await r.json(); } catch (_) { /* sin cuerpo */ }
    if (!r.ok) throw new Error((j && typeof j.detail === 'string' && j.detail) || `El portal respondió ${r.status}`);
    return j;
  }

  function opciones(select, valores, todos) {
    const actual = select.value;
    select.replaceChildren(new Option(todos, ''));
    for (const [valor, etiqueta] of valores) select.append(new Option(etiqueta, valor));
    select.value = [...select.options].some((o) => o.value === actual) ? actual : '';
  }

  function montarVista() {
    V.raiz = document.getElementById('vista-tickets');
    if (!V.raiz) return false;
    const contenido = nodo('div', null, 'tk-contenido');
    // Celular y tableta (tickets.css): una pantalla por vez, lista → detalle
    V.raiz.dataset.paso = 'lista';
    const intro = nodo('div', null, 'tk-intro');
    intro.append(nodo('h2', 'Tickets'),
      nodo('p', 'Cada error del portal queda acá con lo necesario para arreglarlo. No se arreglan solos: copiá el ticket y pedí el arreglo.'));
    contenido.append(intro);
    const filtros = nodo('div', null, 'tk-filtros');
    for (const [clave, etiqueta] of [['estado', 'Estado'], ['componente', 'Componente'], ['tipo', 'Tipo']]) {
      const l = nodo('label', etiqueta);
      const s = document.createElement('select');
      s.id = `ticketsFiltro${clave[0].toUpperCase()}${clave.slice(1)}`;
      s.addEventListener('change', cargarLista);
      l.append(s);
      filtros.append(l);
      V.filtros[clave] = s;
    }
    opciones(V.filtros.estado, [['sin_resolver', 'Sin resolver'], ['abierto', 'Abiertos'], ['en_curso', 'En curso'], ['resuelto', 'Resueltos']], 'Todos');
    V.filtros.estado.value = 'sin_resolver';
    V.estado = nodo('p', '');
    V.estado.setAttribute('role', 'status');
    const grilla = nodo('div', null, 'tk-grilla');
    V.lista = nodo('div', null, 'tk-lista');
    V.detalle = nodo('div', null, 'tk-detalle');
    V.detalle.append(nodo('p', 'Elegí un ticket para ver el detalle.'));
    grilla.append(V.lista, V.detalle);
    contenido.append(filtros, V.estado, grilla);
    V.raiz.append(contenido);
    return true;
  }

  async function cargarLista() {
    try {
      const resumen = await pedirJson('/api/tickets/resumen');
      opciones(V.filtros.componente, resumen.componentes.map((c) => [c, c]), 'Todos');
      opciones(V.filtros.tipo, resumen.tipos.map((t) => [t, t]), 'Todos');
      const q = new URLSearchParams();
      for (const [k, s] of Object.entries(V.filtros)) if (s.value) q.set(k, s.value);
      V.tickets = await pedirJson(`/api/tickets?${q}`);
      V.estado.textContent = `${V.tickets.length} ticket${V.tickets.length === 1 ? '' : 's'} · ${resumen.sin_resolver} sin resolver en total.`;
      V.lista.replaceChildren(...V.tickets.map(itemLista));
      if (!V.tickets.length) V.lista.append(nodo('p', 'No hay tickets con estos filtros.'));
    } catch (e) {
      V.estado.textContent = `No se pudo cargar la lista: ${texto(e)}`;
    }
  }

  function itemLista(t) {
    const b = boton('', () => abrirDetalle(t.id, true), { 'data-ticket-id': String(t.id) });
    b.className = 'tk-item';
    if (V.abierto === t.id) b.setAttribute('aria-current', 'true');
    const cabeza = nodo('span');
    const insignia = nodo('span', NOMBRES_ESTADO[t.estado] || t.estado, 'tk-insignia');
    insignia.dataset.estado = t.estado;
    cabeza.append(insignia, document.createTextNode(`#${t.id} · ${t.componente} · ${t.tipo}`));
    b.append(cabeza, nodo('span', t.mensaje.length > 180 ? `${t.mensaje.slice(0, 180)}…` : t.mensaje),
      nodo('span', `${t.ocurrencias} ${t.ocurrencias === 1 ? 'vez' : 'veces'} · última: ${t.ultima_vez.replace('T', ' ')}`, 'tk-sub'));
    return b;
  }

  function seccion(titulo, valor) {
    if (valor == null || valor === '' || (typeof valor === 'object' && !Object.keys(valor).length)) return [];
    return [nodo('h3', titulo), nodo('pre', typeof valor === 'string' ? valor : JSON.stringify(valor, null, 2))];
  }

  // «‹ Tickets»: solo se ve en el celular, con el detalle abierto
  function volverALaLista() {
    V.raiz.dataset.paso = 'lista';
    window.scrollTo(0, 0);
    const item = V.lista.querySelector(`[data-ticket-id="${V.abierto}"]`);
    if (item) item.focus({ preventScroll: true });
  }

  async function abrirDetalle(id, desdeLista = false) {
    V.abierto = id;
    if (desdeLista) V.raiz.dataset.paso = 'detalle';
    V.lista.querySelectorAll('[data-ticket-id]').forEach((b) => {
      if (Number(b.dataset.ticketId) === id) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
    let t;
    try { t = await pedirJson(`/api/tickets/${id}`); } catch (e) {
      V.detalle.replaceChildren(nodo('p', `No se pudo abrir el ticket: ${texto(e)}`));
      return;
    }
    const aviso = nodo('p', '');
    aviso.setAttribute('role', 'status');
    const acciones = nodo('div', null, 'tk-acciones');
    acciones.append(boton('Copiar ticket', () => copiar(t.texto, aviso), { 'data-ticket-copiar': String(t.id) }));
    const destinos = t.estado === 'resuelto' ? [['abierto', 'Reabrir']]
      : t.estado === 'en_curso' ? [['resuelto', 'Marcar resuelto'], ['abierto', 'Volver a abierto']]
        : [['en_curso', 'Marcar en curso'], ['resuelto', 'Marcar resuelto']];
    for (const [estado, etiqueta] of destinos) {
      acciones.append(boton(etiqueta, () => cambiarEstado(t.id, estado, aviso), { 'data-ticket-estado': estado }));
    }
    const insignia = nodo('span', NOMBRES_ESTADO[t.estado] || t.estado, 'tk-insignia');
    insignia.dataset.estado = t.estado;
    const titulo = nodo('h2');
    titulo.append(insignia, document.createTextNode(`Ticket #${t.id}`));
    const volver = boton('', volverALaLista, { 'aria-label': 'Volver a Tickets' });
    volver.className = 'tk-volver';
    volver.innerHTML = '<svg class="ico" aria-hidden="true"><use href="/static/iconos.svg#chevron-left"></use></svg><span>Tickets</span>';
    const partes = [volver, titulo, nodo('p', t.mensaje),
      nodo('p', `${t.componente} · ${t.tipo} · ${t.ocurrencias} ${t.ocurrencias === 1 ? 'vez' : 'veces'}`
        + (t.reaperturas ? ` · reabierto ${t.reaperturas} ${t.reaperturas === 1 ? 'vez' : 'veces'}` : ''), 'tk-sub'),
      nodo('p', `Primera vez: ${t.creado_at.replace('T', ' ')} · Última: ${t.ultima_vez.replace('T', ' ')}`, 'tk-sub'),
      nodo('p', `Commit: ${t.commit || 'desconocido'}`, 'tk-sub'), acciones, aviso,
      ...seccion('Pasos para reproducir', t.pasos), ...seccion('Datos involucrados', t.datos),
      ...seccion('Petición', t.peticion), ...seccion('Traza', t.traza)];
    if (t.tiene_captura) {
      const img = document.createElement('img');
      img.alt = `Captura del ticket ${t.id}`;
      img.loading = 'lazy';
      img.src = `/api/tickets/${t.id}/captura`;
      partes.push(nodo('h3', 'Captura'), img);
    }
    if (t.tiene_html) {
      const a = nodo('a', 'Ver el HTML de la página (texto)');
      a.href = `/api/tickets/${t.id}/html`;
      a.target = '_blank';
      a.rel = 'noopener';
      partes.push(nodo('h3', 'HTML'), a);
    }
    partes.push(...seccion('Ocurrencias recientes', t.ocurrencias_detalle), ...seccion('Últimas líneas del log', t.log));
    V.detalle.replaceChildren(...partes);
    // En el celular el detalle reemplaza a la lista: se empieza arriba
    if (desdeLista && window.innerWidth < 1024) window.scrollTo(0, 0);
  }

  async function cambiarEstado(id, estado, aviso) {
    try {
      await pedirJson(`/api/tickets/${id}/estado`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ estado }),
      });
      await cargarLista();
      await abrirDetalle(id);
      actualizarBoton();
    } catch (e) {
      aviso.textContent = `No se pudo cambiar el estado: ${texto(e)}`;
    }
  }

  async function copiar(contenido, aviso) {
    try {
      await navigator.clipboard.writeText(contenido);
      aviso.textContent = 'Ticket copiado: pegalo en el chat.';
      return;
    } catch (_) { /* sin permiso: se copia a mano */ }
    const area = document.createElement('textarea');
    area.value = contenido;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
    area.remove();
    aviso.textContent = ok ? 'Ticket copiado: pegalo en el chat.' : 'No se pudo copiar solo: seleccioná el texto de abajo.';
  }

  async function mostrar() {
    if (!V.raiz && !montarVista()) return;
    await cargarLista();
    if (V.abierto && V.tickets.some((t) => t.id === V.abierto)) await abrirDetalle(V.abierto);
  }

  function iniciar() {
    const b = document.getElementById('btnReportarError');
    if (b) b.addEventListener('click', abrirFormulario);
    const enviar = document.getElementById('btnTicketEnviar');
    if (enviar) enviar.addEventListener('click', enviarFormulario);
    const verTodos = document.getElementById('btnTicketVerTodos');
    if (verTodos) verTodos.addEventListener('click', () => {
      cerrarFormulario();
      if (typeof window.cambiarVista === 'function') window.cambiarVista('tickets');
    });
    const cancelar = document.getElementById('btnTicketCancelar');
    if (cancelar) cancelar.addEventListener('click', cerrarFormulario);
    const modal = document.getElementById('modalTicket');
    if (modal) {
      modal.addEventListener('click', (e) => { if (e.target === modal) cerrarFormulario(); });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.hidden) cerrarFormulario(); });
    }
    actualizarBoton();
    setInterval(actualizarBoton, 30000);
  }

  window.Tickets = { mostrar, actualizarBoton, contexto, _test: { llamadas, consola, capturarPantalla } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
