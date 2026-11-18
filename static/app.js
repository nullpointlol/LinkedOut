// LinkedOut — lógica del frontend (modo oscuro, tres paneles)
let listaOfertasMemoria = [];
let ofertaActual = null;
let telemetriaData = null;
let currentMobileTab = 'ofertas';
let currentRightTab = 'cv';
let currentModalTab = 'manual';
let currentTagFilter = 'TODOS';
let isTextoOfertaExpanded = false;

// Estado de Búsqueda, Filtros y Ordenamiento
let filtroEstadoOfertas = 'TODAS'; // 'TODAS', 'SIN_CV', 'LISTAS'
let filtroLifecycle = 'TODAS'; // 'TODAS', 'POR_POSTULAR', 'POSTULADA', 'EN_PROCESO', 'FINALIZADAS'
let textoBusquedaOfertas = '';
let criterioOrdenOfertas = 'match_desc'; // 'fecha_desc', 'fecha_asc', 'match_desc'
let isDetalleSeguimientoOpen = false;

// Estado del botón Descartar In-Place
let descartarTimeout = null;
let descartarConfirmando = false;

// Estado del Hover en Selector de Modelo

// Función de sanitización para prevenir XSS y ReferenceError
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// A2 (auditoría 17/11): el CV lo escribe la IA con el texto de la oferta (terceros) en el contexto, y marked no
// sanitiza. El HTML crudo se muestra como texto, los links solo pueden ser http(s) o mailto y las imágenes, su texto.
const ESQUEMA_LINK_SEGURO = /^(https?:|mailto:)/i;
let _markedSeguro = null;
function markdownSeguro(md) {
  if (typeof marked === 'undefined') return `<pre>${escapeHtml(md)}</pre>`;
  if (!_markedSeguro) {
    _markedSeguro = new marked.Marked({
      renderer: {
        html: ({ text }) => escapeHtml(text),
        image: ({ text }) => escapeHtml(text || ''),
        link({ href, title, tokens }) {
          const texto = this.parser.parseInline(tokens);
          if (!ESQUEMA_LINK_SEGURO.test(String(href || '').trim())) return texto;
          const t = title ? ` title="${escapeHtml(title)}"` : '';
          return `<a href="${escapeHtml(href)}"${t} target="_blank" rel="noopener noreferrer">${texto}</a>`;
        },
      },
    });
  }
  return _markedSeguro.parse(md || '');
}

document.addEventListener('DOMContentLoaded', () => {
  cambiarVista('manual');
  cargarOfertas();
  cargarAprendizajes();
  conectarSSE();
  actualizarTelemetriaModelo();

  // Menú «Ajustes» (modelo de IA): se abre con el botón del engranaje, se cierra con Escape o al hacer clic afuera
  document.addEventListener('keydown', manejarTeclaAjustes);
  document.addEventListener('keydown', cerrarModalesConEscape);
  document.addEventListener('click', (e) => {
    const contenedorModelo = document.getElementById('contenedorSelectorModelo');
    if (contenedorModelo && !contenedorModelo.contains(e.target)) {
      ocultarDropdownTelemetria();
    }
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth >= 1024) {
      const subpanelVacante = document.getElementById('subpanel-vacante');
      const subpanelTags = document.getElementById('subpanel-tags');
      if (subpanelVacante) subpanelVacante.classList.remove('hidden');
      if (subpanelTags) subpanelTags.classList.remove('hidden');
    }
  });
});

// Variables de control del HUD Agéntico
let hudTimerInterval = null;
let hudStartTime = null;
let hudUltimoId = null;

function iniciarCronometroHUD(initialSeconds = null) {
  if (initialSeconds !== null && initialSeconds !== undefined) {
    hudStartTime = Date.now() - (initialSeconds * 1000);
  } else if (!hudStartTime) {
    hudStartTime = Date.now();
  }

  const timerEl = document.getElementById('hudTimer');
  if (timerEl && (!hudTimerInterval || initialSeconds !== null)) {
    const elapsedSecs = Math.max(0, Math.floor((Date.now() - hudStartTime) / 1000));
    const mins = String(Math.floor(elapsedSecs / 60)).padStart(2, '0');
    const secs = String(elapsedSecs % 60).padStart(2, '0');
    timerEl.textContent = `${mins}:${secs}`;
  }

  if (hudTimerInterval) return;

  hudTimerInterval = setInterval(() => {
    if (!hudStartTime) return;
    const elapsedSecs = Math.max(0, Math.floor((Date.now() - hudStartTime) / 1000));
    const mins = String(Math.floor(elapsedSecs / 60)).padStart(2, '0');
    const secs = String(elapsedSecs % 60).padStart(2, '0');
    const tEl = document.getElementById('hudTimer');
    if (tEl) {
      tEl.textContent = `${mins}:${secs}`;
    }
  }, 1000);
}

function detenerCronometroHUD() {
  if (hudTimerInterval) {
    clearInterval(hudTimerInterval);
    hudTimerInterval = null;
  }
}

function actualizarHUDProgreso(data) {
  const hud = document.getElementById('agenticProgressHUD');
  if (!hud) return;

  if (typeof data === 'string') {
    data = {
      step: data,
      detail: 'Ejecutando proceso en segundo plano...',
      percent: 10,
      phase: 1,
      agent: 'ESTRATEGA',
      agent_title: 'Subagente 1: Estratega'
    };
  }

  hud.classList.remove('hidden');

  // Sincronización precisa y verídica del cronómetro con telemetría del servidor
  if (data.elapsed_seconds !== undefined && data.elapsed_seconds !== null) {
    iniciarCronometroHUD(data.elapsed_seconds);
  } else {
    iniciarCronometroHUD();
  }

  if (data.id) {
    hudUltimoId = data.id;
  }

  const stepTitleEl = document.getElementById('hudStepTitle');
  const statusMsgEl = document.getElementById('statusMessage');
  if (stepTitleEl) stepTitleEl.textContent = data.step || 'Procesando la oferta...';
  if (statusMsgEl) statusMsgEl.textContent = data.step || 'Procesando...';

  const detailEl = document.getElementById('hudDetail');
  if (detailEl) {
    detailEl.textContent = data.detail || 'Analizando pautas, procesando inferencia en agy.exe...';
  }

  const agentNameEl = document.getElementById('hudAgentName');
  const agentIconEl = document.getElementById('hudAgentIcon');
  const agentBadgeEl = document.getElementById('hudAgentBadge');

  const agentCode = (data.agent || 'ESTRATEGA').toUpperCase();
  if (agentNameEl) {
    if (agentCode.includes('ESTRATEGA')) {
      agentNameEl.textContent = '1. Estratega';
      if (agentIconEl) agentIconEl.className = 'fa-solid fa-chess text-zinc-300';
    } else if (agentCode.includes('REDACTOR')) {
      agentNameEl.textContent = '2. Redactor';
      if (agentIconEl) agentIconEl.className = 'fa-solid fa-pen-nib text-zinc-300';
    } else if (agentCode.includes('AUDITOR')) {
      agentNameEl.textContent = '3. Auditor 100% LLM';
      if (agentIconEl) agentIconEl.className = 'fa-solid fa-shield-halved text-zinc-300';
    }
  }

  const modelNameEl = document.getElementById('hudModelName');
  if (modelNameEl) {
    const modelo = data.model || (telemetriaData ? telemetriaData.configured_model : 'gemini-3.8-flash-high');
    const esfuerzo = data.effort || 'high';
    modelNameEl.textContent = `${modelo} (${esfuerzo})`;
  }

  const pct = Math.min(100, Math.max(5, data.percent || (data.phase ? data.phase * 30 : 15)));
  const percentEl = document.getElementById('hudPercent');
  const barEl = document.getElementById('hudProgressBar');
  if (percentEl) percentEl.textContent = `${Math.round(pct)}%`;
  if (barEl) barEl.style.width = `${pct}%`;

  const currentPhase = data.phase || 1;
  actualizarNodosStepper(currentPhase);

  if (data.id) {
    const cardStatusEl = document.querySelector(`#card-oferta-${data.id} .status-badge`);
    if (cardStatusEl) {
      cardStatusEl.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin text-zinc-400 mr-1 text-[8px]"></i>Fase ${currentPhase}/3 (${Math.round(pct)}%)`;
      cardStatusEl.className = 'status-badge text-xs uppercase px-1.5 py-0.2 rounded bg-[#1e2026] text-amber-300 font-mono border border-amber-900/40';
    }
  }
}

function actualizarNodosStepper(phase) {
  for (let i = 1; i <= 3; i++) {
    const pill = document.getElementById(`stepPill-${i}`);
    const icon = document.getElementById(`stepIcon-${i}`);
    if (!pill || !icon) continue;

    let targetState = 'pending';
    if (i < phase) {
      targetState = 'done';
    } else if (i === phase) {
      targetState = 'active';
    }

    if (pill.dataset.phaseState === targetState) {
      continue;
    }
    pill.dataset.phaseState = targetState;

    if (targetState === 'done') {
      pill.className = 'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs sm:text-xs bg-[#121418] border border-emerald-900/60 text-emerald-400 transition-colors';
      icon.className = 'w-4 h-4 rounded-full flex items-center justify-center bg-emerald-950 text-xs font-bold text-emerald-400 border border-emerald-800';
      icon.innerHTML = '<i class="fa-solid fa-check text-xs"></i>';
    } else if (targetState === 'active') {
      pill.className = 'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs sm:text-xs bg-[#1e2026] border border-zinc-500 text-zinc-100 transition-colors font-medium';
      icon.className = 'w-4 h-4 rounded-full flex items-center justify-center bg-[#262930] text-xs font-bold text-zinc-200';
      icon.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-xs"></i>';
    } else {
      pill.className = 'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs sm:text-xs bg-[#121418] border border-[#262930] text-zinc-400 transition-colors';
      icon.className = 'w-4 h-4 rounded-full flex items-center justify-center bg-[#1e2026] text-xs font-bold text-zinc-400';
      icon.textContent = String(i);
    }
  }
}

function completarHUDProgreso(data) {
  const hud = document.getElementById('agenticProgressHUD');
  if (!hud) return;

  const barEl = document.getElementById('hudProgressBar');
  const percentEl = document.getElementById('hudPercent');
  const stepTitleEl = document.getElementById('hudStepTitle');
  const detailEl = document.getElementById('hudDetail');

  if (barEl) barEl.style.width = '100%';
  if (percentEl) percentEl.textContent = '100%';
  if (stepTitleEl) {
    stepTitleEl.innerHTML = `<span class="text-emerald-400 font-semibold"><i class="fa-solid fa-circle-check mr-1.5"></i>${data.message || '¡Documentación generada con éxito!'}</span>`;
  }
  if (detailEl) {
    detailEl.textContent = 'Listo. El CV y el mensaje quedaron optimizados.';
  }

  if (data.elapsed_seconds !== undefined && data.elapsed_seconds !== null) {
    const sec = Math.max(0, parseInt(data.elapsed_seconds, 10));
    const mins = String(Math.floor(sec / 60)).padStart(2, '0');
    const secs = String(sec % 60).padStart(2, '0');
    const timerEl = document.getElementById('hudTimer');
    if (timerEl) {
      timerEl.textContent = `${mins}:${secs}`;
    }
  }

  actualizarNodosStepper(4);
  detenerCronometroHUD();

  setTimeout(() => {
    ocultarHUDProgreso();
  }, 4500);
}

function ocultarHUDProgreso() {
  detenerCronometroHUD();
  const hud = document.getElementById('agenticProgressHUD');
  if (hud) hud.classList.add('hidden');
  const alertEl = document.getElementById('statusAlert');
  if (alertEl) alertEl.classList.add('hidden');
}

function mostrarAlertaEstado(msg) {
  actualizarHUDProgreso(msg);
}

function ocultarAlertaEstado() {
  ocultarHUDProgreso();
}

/** ¿El evento de pipeline viene de una postulación del Radar LinkedIn? (server.py agrega `origen`). */
function esEventoDelRadar(data) {
  return !!data && data.origen === 'linkedin';
}

// Server-Sent Events (SSE) Listener
function conectarSSE() {
  const eventSource = new EventSource('/api/stream');

  // Los pipelines del Radar (origen 'linkedin') corren en segundo plano: sus eventos no deben abrir el
  // HUD ni cambiar la oferta abierta en la vista manual (el Radar se entera por radar_post).
  eventSource.addEventListener('status', (e) => {
    const data = JSON.parse(e.data);
    if (esEventoDelRadar(data)) {
      if (window.Radar && data.radar_post_id !== undefined) window.Radar.onProgreso(data);
      return;
    }
    actualizarHUDProgreso(data);
  });

  eventSource.addEventListener('status_heartbeat', (e) => {
    const data = JSON.parse(e.data);
    if (esEventoDelRadar(data)) {
      if (window.Radar && data.radar_post_id !== undefined) window.Radar.onProgreso(data);
      return;
    }
    actualizarHUDProgreso(data);
  });

  eventSource.addEventListener('radar_progreso', (e) => {
    try {
      if (window.Radar) window.Radar.onProgreso(JSON.parse(e.data));
    } catch (err) {
      console.warn('Error procesando radar_progreso SSE:', err);
    }
  });

  eventSource.addEventListener('complete', (e) => {
    const data = JSON.parse(e.data);
    if (esEventoDelRadar(data)) return;
    completarHUDProgreso(data);
    restaurarBotonRegenerar();
    cargarOfertas(data.id);
    actualizarTelemetriaModelo();
  });

  eventSource.addEventListener('batch_progress', (e) => {
    const data = JSON.parse(e.data);
    mostrarAlertaLote(data);
  });

  eventSource.addEventListener('batch_complete', (e) => {
    const data = JSON.parse(e.data);
    completarAlertaLote(data);
    cargarOfertas();
  });

  eventSource.addEventListener('model_changed', (e) => {
    try {
      const data = JSON.parse(e.data);
      telemetriaData = data;
      actualizarTelemetriaModelo();
    } catch (err) {
      console.warn('Error procesando model_changed SSE:', err);
    }
  });

  eventSource.addEventListener('lifecycle_updated', (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data && data.oferta) {
        const idx = listaOfertasMemoria.findIndex(o => o.id === data.id);
        if (idx !== -1) {
          listaOfertasMemoria[idx] = data.oferta;
        }
        if (ofertaActual && ofertaActual.id === data.id) {
          ofertaActual = data.oferta;
          renderOferta(data.oferta);
        }
        renderizarListaOfertas(null, ofertaActual ? ofertaActual.id : null);
      }
    } catch (err) {
      console.warn('Error procesando lifecycle_updated SSE:', err);
    }
  });

  // Radar LinkedIn: se reenvían a static/radar.js (window.Radar)
  eventSource.addEventListener('radar_status', (e) => {
    try {
      if (window.Radar) window.Radar.onStatus(JSON.parse(e.data));
    } catch (err) {
      console.warn('Error procesando radar_status SSE:', err);
    }
  });

  eventSource.addEventListener('radar_post_removed', (e) => {
    try {
      if (window.Radar) window.Radar.onPostRemoved(JSON.parse(e.data));
    } catch (err) {
      console.warn('Error procesando radar_post_removed SSE:', err);
    }
  });

  eventSource.addEventListener('radar_post', (e) => {
    try {
      if (window.Radar) window.Radar.onPost(JSON.parse(e.data));
    } catch (err) {
      console.warn('Error procesando radar_post SSE:', err);
    }
  });

  eventSource.addEventListener('error', (e) => {
    console.warn('SSE Reconectando...', e);
  });
}

// Selector de vista: 'manual' | 'radar' | 'editor' | 'perfil'
function cambiarVista(vista) {
  // Manual, LinkedIn (radar) y Portales son la misma vista del Radar con otro modo (static/radar.js)
  const esRadarOManual = vista === 'manual' || vista === 'radar' || vista === 'portales';
  const esEditor = vista === 'editor';
  const main = document.querySelector('main');
  const navMovil = document.getElementById('navMobilePostulaciones');
  const vistaRadar = document.getElementById('vista-radar');
  const vistaPerfil = document.getElementById('vista-perfil');
  const vistaTickets = document.getElementById('vista-tickets');
  window.vistaActual = vista;

  if (main) main.style.display = esEditor ? '' : 'none';
  if (navMovil) navMovil.style.display = esEditor ? '' : 'none';
  if (vistaPerfil) vistaPerfil.hidden = vista !== 'perfil';
  if (vistaTickets) vistaTickets.hidden = vista !== 'tickets';
  if (vistaRadar) {
    vistaRadar.hidden = !esRadarOManual;
    vistaRadar.classList.toggle('flex', esRadarOManual);
    vistaRadar.classList.toggle('flex-col', esRadarOManual);
  }

  const activo = 'px-2 py-1 rounded font-medium whitespace-nowrap bg-[#1e2026] text-zinc-100 border border-zinc-600';
  const inactivo = 'px-2 py-1 rounded font-medium whitespace-nowrap text-zinc-400 hover:text-zinc-200 border border-transparent';
  const btnManual = document.getElementById('btnVistaManual');
  const btnRadar = document.getElementById('btnVistaRadar');
  const btnPerfil = document.getElementById('btnVistaPerfil');
  if (btnManual) btnManual.className = vista === 'manual' ? activo : inactivo;
  if (btnRadar) btnRadar.className = vista === 'radar' ? activo : inactivo;
  const btnPortales = document.getElementById('btnVistaPortales');
  // Como Tickets: en el celular no entra en la barra (por ahora el portal es para monitor)
  if (btnPortales) btnPortales.className = 'hidden sm:inline-block ' + (vista === 'portales' ? activo : inactivo);
  if (btnPerfil) btnPerfil.className = vista === 'perfil' ? activo : inactivo;
  const btnTickets = document.getElementById('btnVistaTickets');
  // En el celular no entra en la barra: se llega desde el botón flotante («Ver tickets»)
  if (btnTickets) btnTickets.className = 'hidden sm:inline-block ' + (vista === 'tickets' ? activo : inactivo);

  if (esRadarOManual && window.Radar && typeof window.Radar.mostrar === 'function') {
    window.Radar.mostrar(vista);
  }
  if (vista === 'perfil' && window.Perfil && typeof window.Perfil.mostrar === 'function') {
    window.Perfil.mostrar();
  }
  if (vista === 'tickets' && window.Tickets && typeof window.Tickets.mostrar === 'function') {
    window.Tickets.mostrar();
  }
}

/** Estado del CV de una postulación en castellano. Solo es texto visible: los valores de `status` y los filtros no cambian. */
function textoEstadoCv(status) {
  const textos = { READY: 'CV listo', PROCESSING: 'Generando', ERROR: 'Error', PENDING: 'Pendiente' };
  if (!status) return textos.PENDING;
  return Object.prototype.hasOwnProperty.call(textos, status) ? textos[status] : String(status);
}

/** ¿La postulación nació de un post del Radar LinkedIn? */
function esDeRadar(o) {
  return !!o && (o.origen === 'linkedin' || !!o.radar_post_ref);
}

/** «Ver en Radar» o «Ver en Manual»: cambia a la vista correspondiente y abre el post que originó esta postulación. */
function verEnRadar() {
  if (!ofertaActual || !esDeRadar(ofertaActual)) return;
  const esManual = (ofertaActual.radar_post_ref || '').startsWith('manual:');
  const vista = esManual ? 'manual' : 'radar';
  cambiarVista(vista);
  if (window.Radar) window.Radar.abrirPostDePostulacion(ofertaActual.id);
}

/** «Ver postulación» (lo llama el Radar): cambia a Editor y abre esa oferta. */
window.verPostulacion = function (postulacionId) {
  const id = Number(postulacionId);
  if (!id) return;
  cambiarVista('editor');
  seleccionarOferta(id, true);
};

function mostrarAlertaLote(data) {
  const alertEl = document.getElementById('batchAlert');
  const msgEl = document.getElementById('batchMessage');
  const percentEl = document.getElementById('batchPercent');
  const barEl = document.getElementById('batchProgressBar');
  if (alertEl && msgEl && percentEl && barEl) {
    alertEl.classList.remove('hidden');
    msgEl.textContent = `Lote: ${data.completed}/${data.total} (${data.current || ''})`;
    percentEl.textContent = `${Math.round(data.percent)}%`;
    barEl.style.width = `${Math.min(100, Math.max(5, data.percent))}%`;
  }
}

function completarAlertaLote(data) {
  const msgEl = document.getElementById('batchMessage');
  const percentEl = document.getElementById('batchPercent');
  const barEl = document.getElementById('batchProgressBar');
  if (msgEl && percentEl && barEl) {
    msgEl.textContent = `¡Lote de ${data.total} ofertas procesado!`;
    percentEl.textContent = `100%`;
    barEl.style.width = `100%`;
    setTimeout(() => {
      const alertEl = document.getElementById('batchAlert');
      if (alertEl) alertEl.classList.add('hidden');
    }, 3500);
  }
}

// Carga y renderizado del listado de ofertas en el panel izquierdo
async function cargarOfertas(targetId = null) {
  try {
    const res = await fetch('/api/ofertas');
    const ofertas = await res.json();
    listaOfertasMemoria = ofertas || [];

    const badge = document.getElementById('contadorOfertasBadge');
    if (badge) {
      badge.textContent = listaOfertasMemoria.length;
    }

    if (!listaOfertasMemoria || listaOfertasMemoria.length === 0) {
      renderizarListaOfertas([], null);
      limpiarDetalleOferta();
      return;
    }

    // targetId solo se selecciona si es una oferta de la lista manual (nunca una del Radar)
    const targetEnLista = targetId != null && listaOfertasMemoria.some(o => String(o.id) === String(targetId));
    const selectedId = targetEnLista ? targetId : (ofertaActual ? ofertaActual.id : listaOfertasMemoria[0].id);
    renderizarListaOfertas(listaOfertasMemoria, selectedId);
    await seleccionarOferta(selectedId, false);
  } catch (err) {
    console.error('Error cargando ofertas:', err);
  }
}

// Formateo de fecha discreta y opaca (sin sobrecarga visual)
function formatearFechaOpaca(dateStr) {
  if (!dateStr) return '';
  try {
    const cleanStr = String(dateStr).trim().replace(' ', 'T');
    const d = new Date(cleanStr);
    if (isNaN(d.getTime())) return String(dateStr).slice(0, 16);
    const meses = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
    const dia = d.getDate();
    const mes = meses[d.getMonth()];
    const hora = String(d.getHours()).padStart(2, '0');
    const min = String(d.getMinutes()).padStart(2, '0');
    return `${dia} ${mes}, ${hora}:${min}`;
  } catch (e) {
    return String(dateStr).slice(0, 16);
  }
}

// Formateo de días transcurridos desde fecha de postulación
function formatearDiasTranscurridos(fechaStr) {
  if (!fechaStr) return '';
  try {
    const cleanStr = String(fechaStr).trim().replace(' ', 'T');
    const d = new Date(cleanStr);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const dOnly = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const nowOnly = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const diffMs = nowOnly.getTime() - dOnly.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays <= 0) return 'hoy';
    if (diffDays === 1) return 'hace 1d';
    return `hace ${diffDays}d`;
  } catch (e) {
    return '';
  }
}

// Motor de Búsqueda, Filtrado por Estado, Lifecycle y Ordenamiento en Memoria
function filtrarYOrdenarOfertas(ofertas) {
  if (!ofertas || !Array.isArray(ofertas)) return [];
  let res = [...ofertas];

  // 1. Filtro textual
  if (textoBusquedaOfertas) {
    const q = textoBusquedaOfertas.toLowerCase();
    res = res.filter(o => 
      (o.titulo || '').toLowerCase().includes(q) || 
      (o.empresa || '').toLowerCase().includes(q) ||
      (o.contenido_oferta || '').toLowerCase().includes(q)
    );
  }

  // 2. Filtro por Lifecycle Pipeline
  if (filtroLifecycle && filtroLifecycle !== 'TODAS') {
    if (filtroLifecycle === 'EN_PROCESO') {
      res = res.filter(o => ['EN_ENTREVISTA', 'OFERTA'].includes(o.estado_postulacion));
    } else if (filtroLifecycle === 'FINALIZADAS') {
      res = res.filter(o => o.estado_postulacion === 'DESESTIMADA');
    } else {
      res = res.filter(o => (o.estado_postulacion || 'POR_POSTULAR') === filtroLifecycle);
    }
  }

  // 3. Filtro por estado documental (Sin CV / Listas)
  if (filtroEstadoOfertas === 'SIN_CV') {
    res = res.filter(o => o.status !== 'READY' || !o.cv_markdown);
  } else if (filtroEstadoOfertas === 'LISTAS') {
    res = res.filter(o => o.status === 'READY' && !!o.cv_markdown);
  }

  // 4. Ordenamiento
  if (criterioOrdenOfertas === 'fecha_desc') {
    res.sort((a, b) => new Date((b.created_at || '').replace(' ', 'T') || 0) - new Date((a.created_at || '').replace(' ', 'T') || 0));
  } else if (criterioOrdenOfertas === 'fecha_asc') {
    res.sort((a, b) => new Date((a.created_at || '').replace(' ', 'T') || 0) - new Date((b.created_at || '').replace(' ', 'T') || 0));
  } else if (criterioOrdenOfertas === 'match_desc') {
    res.sort((a, b) => (b.match_score || 0) - (a.match_score || 0) || b.id - a.id);
  }

  return res;
}

function filtrarOfertasLista(query) {
  textoBusquedaOfertas = (query || '').trim();
  const selectedId = ofertaActual ? ofertaActual.id : null;
  renderizarListaOfertas(null, selectedId);
}

function setFiltroLifecycle(filtro) {
  filtroLifecycle = filtro;
  const tabs = [
    { id: 'tabLifecycleTodas', val: 'TODAS' },
    { id: 'tabLifecyclePorPostular', val: 'POR_POSTULAR' },
    { id: 'tabLifecyclePostuladas', val: 'POSTULADA' },
    { id: 'tabLifecycleEnProceso', val: 'EN_PROCESO' },
    { id: 'tabLifecycleFinalizadas', val: 'FINALIZADAS' }
  ];

  tabs.forEach(t => {
    const el = document.getElementById(t.id);
    if (el) {
      if (t.val === filtro) {
        el.className = 'px-1.5 py-0.5 rounded bg-[#1e2026] text-zinc-100 border border-zinc-600 font-medium whitespace-nowrap';
      } else {
        el.className = 'px-1.5 py-0.5 rounded bg-[#121418] text-zinc-400 hover:text-zinc-200 border border-[#262930] whitespace-nowrap';
      }
    }
  });

  const selectedId = ofertaActual ? ofertaActual.id : null;
  renderizarListaOfertas(null, selectedId);
}

function setFiltroEstadoOfertas(estado) {
  filtroEstadoOfertas = estado;
  const chipTodas = document.getElementById('chipFiltroTodas');
  const chipSinCV = document.getElementById('chipFiltroSinCV');
  const chipListas = document.getElementById('chipFiltroListas');

  const chips = [
    { el: chipTodas, val: 'TODAS' },
    { el: chipSinCV, val: 'SIN_CV' },
    { el: chipListas, val: 'LISTAS' }
  ];

  chips.forEach(c => {
    if (c.el) {
      if (c.val === estado) {
        c.el.className = 'px-1.5 py-0.5 rounded bg-[#1e2026] text-zinc-100 border border-zinc-600 font-medium';
      } else {
        c.el.className = 'px-1.5 py-0.5 rounded bg-[#121418] text-zinc-400 hover:text-zinc-200 border border-[#262930]';
      }
    }
  });

  const selectedId = ofertaActual ? ofertaActual.id : null;
  renderizarListaOfertas(null, selectedId);
}

function ordenarOfertasLista(criterio) {
  criterioOrdenOfertas = criterio;
  const selectedId = ofertaActual ? ofertaActual.id : null;
  renderizarListaOfertas(null, selectedId);
}

// Renderizado de tarjetas en el panel vertical izquierdo con filtros, badges de lifecycle y fecha opaca
function renderizarListaOfertas(ofertas = null, selectedId = null) {
  const contenedor = document.getElementById('listaOfertasCards');
  if (!contenedor) return;

  const fuente = ofertas !== null ? ofertas : listaOfertasMemoria;
  const listaFiltrada = filtrarYOrdenarOfertas(fuente);

  const badge = document.getElementById('contadorOfertasBadge');
  if (badge) {
    if (listaFiltrada.length === listaOfertasMemoria.length) {
      badge.textContent = listaOfertasMemoria.length;
    } else {
      badge.textContent = `${listaFiltrada.length}/${listaOfertasMemoria.length}`;
    }
  }

  contenedor.innerHTML = '';

  if (!listaFiltrada || listaFiltrada.length === 0) {
    contenedor.innerHTML = `
      <div class="p-3 text-center text-zinc-300 text-xs italic">
        ${textoBusquedaOfertas || filtroEstadoOfertas !== 'TODAS' || filtroLifecycle !== 'TODAS' ? 'No hay postulaciones con estos filtros.' : 'No hay postulaciones registradas.<br>Hacé clic en "+ Nueva Oferta".'}
      </div>
    `;
    return;
  }

  listaFiltrada.forEach((o) => {
    const isSelected = selectedId && o.id === selectedId;
    const score = Math.round(o.match_score || 0);

    let scoreColor = 'bg-[#1e2026] text-zinc-400 border-[#262930]';
    if (score >= 80) {
      scoreColor = 'bg-emerald-950/40 text-emerald-400 border-emerald-900/40';
    } else if (score >= 60) {
      scoreColor = 'bg-amber-950/40 text-amber-300 border-amber-900/40';
    } else if (score > 0) {
      scoreColor = 'bg-rose-950/40 text-rose-300 border-rose-900/40';
    }

    const estado = o.estado_postulacion || 'POR_POSTULAR';
    const dias = formatearDiasTranscurridos(o.fecha_postulacion);

    let badgeColor = 'bg-[#121418] text-zinc-400 border-[#262930]';
    let estadoLabel = 'Por Postular';
    if (estado === 'POSTULADA') {
      badgeColor = 'bg-sky-950/40 text-sky-400 border-sky-900/40';
      estadoLabel = 'Postulada';
    } else if (estado === 'EN_ENTREVISTA') {
      badgeColor = 'bg-emerald-950/40 text-emerald-400 border-emerald-900/40';
      estadoLabel = 'En Entrevista';
    } else if (estado === 'OFERTA') {
      badgeColor = 'bg-purple-950/40 text-purple-300 border-purple-900/40';
      estadoLabel = 'Oferta';
    } else if (estado === 'DESESTIMADA') {
      badgeColor = 'bg-rose-950/40 text-rose-400 border-rose-900/40';
      estadoLabel = 'Desestimada';
    }
    const textoBadge = dias ? `${estadoLabel} · ${dias}` : estadoLabel;

    const card = document.createElement('div');
    card.id = `card-oferta-${o.id}`;
    card.onclick = () => seleccionarOferta(o.id, true);
    card.className = `p-2.5 rounded-lg border text-xs cursor-pointer transition-colors ${
      isSelected
        ? 'border-l-2 border-l-amber-500 bg-[#1e2129] border-[#363a45] shadow-sm'
        : 'border-l-2 border-l-transparent bg-[#16181d] hover:bg-[#1a1d23] border-[#262930]'
    }`;

    const fechaStr = formatearFechaOpaca(o.created_at);
    const afinidad = resumenAfinidad(o.requisitos);

    card.innerHTML = `
      <div class="flex items-center justify-between gap-1 mb-1">
        <h3 class="font-medium text-zinc-100 truncate">${escapeHtml(o.titulo || 'Posición Técnica')}</h3>
        <span class="text-xs font-mono px-1.5 py-0.2 rounded font-semibold border ${scoreColor}${afinidad && afinidad.mayormenteAprendido ? ' opacity-60' : ''}">${score}%</span>
      </div>
      ${htmlAfinidad(afinidad, 'mb-1')}
      <div class="flex items-center justify-between text-xs text-zinc-400">
        <span class="truncate text-zinc-300">${escapeHtml(o.empresa || 'Confidencial')}</span>
        ${fechaStr ? `<span class="text-zinc-400 font-mono text-xs ml-1 flex-shrink-0">${fechaStr}</span>` : ''}
      </div>
      <div class="flex items-center justify-between text-xs text-zinc-400 mt-1.5 pt-1.5 border-t border-[#262930]/40">
        <span class="text-xs font-mono px-1.5 py-0.2 rounded border ${badgeColor}">${escapeHtml(textoBadge)}</span>
        <span class="status-badge text-xs uppercase px-1.5 py-0.2 rounded ${o.status === 'PROCESSING' ? 'bg-[#1e2026] text-amber-300 border border-amber-900/40' : 'bg-[#121418] text-zinc-400 border border-[#262930]'} font-mono">${o.status === 'PROCESSING' ? `<i class="fa-solid fa-circle-notch fa-spin text-[8px] mr-1"></i>${textoEstadoCv('PROCESSING')}` : escapeHtml(textoEstadoCv(o.status || 'READY'))}</span>
      </div>
    `;

    contenedor.appendChild(card);
  });
}

// Cargar detalle de una oferta seleccionada
async function seleccionarOferta(id, fromUserClick = false) {
  if (!id) return;
  try {
    const res = await fetch(`/api/ofertas/${id}`);
    if (!res.ok) return;
    ofertaActual = await res.json();
    renderOferta(ofertaActual);

    // Actualizar estilos activos en las tarjetas
    listaOfertasMemoria.forEach((o) => {
      const card = document.getElementById(`card-oferta-${o.id}`);
      if (card) {
        const isSelected = o.id === id;
        card.className = `p-2.5 rounded-lg border text-xs cursor-pointer transition-colors ${
          isSelected
            ? 'border-l-2 border-l-amber-500 bg-[#1e2129] border-[#363a45] shadow-sm'
            : 'border-l-2 border-l-transparent bg-[#16181d] hover:bg-[#1a1d23] border-[#262930]'
        }`;
      }
    });

    if (fromUserClick && window.innerWidth < 1024) {
      setMobileTab('vacante');
    }
  } catch (err) {
    console.error('Error obteniendo oferta:', err);
  }
}

window.modoTextoVacante = 'limpio';

function formatearDescripcionVacante(data) {
  if (!data) return '';
  const crudo = data.contenido_oferta || '';
  if (window.modoTextoVacante === 'crudo') {
    return `<pre class="whitespace-pre-wrap font-mono text-xs text-zinc-300 select-all">${escapeHtml(crudo)}</pre>`;
  }

  const cleanText = data.contenido_limpio || crudo;
  if (!cleanText || !cleanText.trim()) {
    return '<p class="text-zinc-300 italic text-xs">Sin descripción disponible.</p>';
  }

  // Formatear texto limpio en bloques legibles y estructurados
  const lines = cleanText.split(/\r?\n/);
  let html = '<div class="space-y-2 text-xs text-zinc-300 leading-relaxed font-sans">';
  let inList = false;

  for (let line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      if (inList) {
        html += '</ul>';
        inList = false;
      }
      continue;
    }

    // Viñeta detectada
    if (/^[-*•]\s+/.test(trimmed)) {
      if (!inList) {
        html += '<ul class="space-y-1 my-1 pl-2 list-none">';
        inList = true;
      }
      const itemContent = trimmed.replace(/^[-*•]\s+/, '');
      html += `<li class="flex items-start gap-1.5"><span class="text-amber-400/80 text-xs mt-0.5 select-none">•</span><span>${escapeHtml(itemContent)}</span></li>`;
      continue;
    }

    if (inList) {
      html += '</ul>';
      inList = false;
    }

    // Encabezado o categoría clave
    const isHeader = /^([A-ZÁÉÍÓÚÑa-záéíóúñ\s/()¿?]+):$/.test(trimmed) || 
                     /^(Modalidad|Ubicación|Principales desafíos|Requisitos|Buscamos|Beneficios|Condiciones|Responsabilidades|Tareas|¿Te interesa|Qué ofrecemos)\b/i.test(trimmed);

    if (isHeader) {
      html += `<div class="pt-1.5"><span class="text-xs font-semibold text-zinc-200 tracking-wide block border-b border-[#262930] pb-0.5">${escapeHtml(trimmed)}</span></div>`;
    } else {
      html += `<p class="text-zinc-300/90">${escapeHtml(trimmed)}</p>`;
    }
  }

  if (inList) {
    html += '</ul>';
  }

  html += '</div>';
  return html;
}

window.cambiarModoTextoVacante = function(modo) {
  window.modoTextoVacante = modo;
  const btnLimpio = document.getElementById('btnModoVacanteLimpio');
  const btnCrudo = document.getElementById('btnModoVacanteCrudo');
  if (modo === 'limpio') {
    btnLimpio?.classList.add('bg-zinc-700', 'text-zinc-100', 'font-medium');
    btnLimpio?.classList.remove('text-zinc-400');
    btnCrudo?.classList.remove('bg-zinc-700', 'text-zinc-100', 'font-medium');
    btnCrudo?.classList.add('text-zinc-400');
  } else {
    btnCrudo?.classList.add('bg-zinc-700', 'text-zinc-100', 'font-medium');
    btnCrudo?.classList.remove('text-zinc-400');
    btnLimpio?.classList.remove('bg-zinc-700', 'text-zinc-100', 'font-medium');
    btnLimpio?.classList.add('text-zinc-400');
  }
  if (ofertaActual) {
    const cont = document.getElementById('ofertaContenido');
    if (cont) cont.innerHTML = formatearDescripcionVacante(ofertaActual);
  }
};

// Renderizado reactivo en el DOM del detalle
function renderOferta(data) {
  document.getElementById('ofertaTitulo').textContent = data.titulo || 'Posición Técnica';
  document.getElementById('ofertaEmpresa').textContent = data.empresa || 'Confidencial';
  
  const contVacante = document.getElementById('ofertaContenido');
  if (contVacante) {
    contVacante.innerHTML = formatearDescripcionVacante(data);
  }

  const badge = document.getElementById('ofertaStatusBadge');
  badge.textContent = textoEstadoCv(data.status);
  actualizarBotonFormatoCv(data);
  const btnRadar = document.getElementById('btnVerEnRadar');
  // style.display (no hidden): las utilidades de Tailwind del botón pisarían a [hidden]
  if (btnRadar) {
    const esDelRadar = esDeRadar(data);
    btnRadar.style.display = esDelRadar ? '' : 'none';
    if (esDelRadar) {
      const esManual = (data.radar_post_ref || '').startsWith('manual:');
      const texto = esManual ? 'Ver en Manual' : 'Ver en Radar';
      const span = btnRadar.querySelector('span');
      if (span) span.textContent = texto;
      btnRadar.setAttribute('aria-label', texto);
      btnRadar.title = esManual ? 'Abrir el post manual que originó esta postulación' : 'Abrir el post de LinkedIn que originó esta postulación';
    }
  }
  badge.className = `text-xs font-mono font-medium px-2 py-0.5 rounded ${
    data.status === 'READY'
      ? 'bg-emerald-950/40 text-emerald-400 border border-emerald-900/40'
      : data.status === 'PROCESSING'
      ? 'bg-amber-950/40 text-amber-300 border border-amber-900/40'
      : 'bg-[#1e2026] text-zinc-400 border border-[#262930]'
  }`;

  // Badge modo contingencia (fallback)
  const badgeFallback = document.getElementById('badgeModoFallback');
  if (badgeFallback) {
    if (data.is_fallback) {
      badgeFallback.classList.remove('hidden');
      if (data.fallback_reason) {
        badgeFallback.setAttribute('title', data.fallback_reason);
      }
    } else {
      badgeFallback.classList.add('hidden');
    }
  }

  // Habilitar botón descartar
  resetBotonDescartar();

  // Dolores de empresa
  const doloresList = document.getElementById('listaDolores');
  doloresList.innerHTML = '';
  const dolores = data.dolores_empresa || [];
  if (dolores.length === 0) {
    doloresList.innerHTML = '<li class="text-zinc-300 italic text-xs">No se detectaron dolores específicos.</li>';
  } else {
    dolores.forEach((d) => {
      const li = document.createElement('li');
      li.className = 'bg-[#1a1d24]/70 border border-[#262930] rounded-md px-2 py-1 text-xs text-zinc-300 leading-relaxed flex items-start gap-1.5';
      li.innerHTML = `<i class="fa-solid fa-bolt text-xs text-amber-400 mt-1 shrink-0"></i><span>${escapeHtml(d)}</span>`;
      doloresList.appendChild(li);
    });
  }

  // Match Score Dinámico
  actualizarScoreVisual(data.match_score, data.desglose_afinidad || data.analisis?.desglose_afinidad);

  // Renderizar Lista de Requisitos con Filtro
  try {
    renderizarListaTags();
  } catch (errTags) {
    console.error('Error renderizando lista de tags:', errTags);
  }

  // Renderizar Matriz de Oferta estructurada
  try {
    const matrizOferta = data.analisis?.matriz_oferta || null;
    renderMatrizOferta(matrizOferta, data.id);
  } catch (errMatriz) {
    console.error('Error renderizando matriz de oferta:', errMatriz);
  }

  // Previsualización de CV
  try {
    const cvContainer = document.getElementById('cvContent');
    const cvMarkdown = data.cv_markdown || '';
    if (cvMarkdown) {
      cvContainer.innerHTML = markdownSeguro(cvMarkdown);
    } else {
      cvContainer.innerHTML = '<p class="text-zinc-300 italic text-xs">El CV adaptado se mostrará aquí una vez procesada la oferta.</p>';
    }

    const chars = cvMarkdown.length;
    const charEl = document.getElementById('cvCharCount');
    if (charEl) {
      const estMin = Math.max(0.5, Math.round((chars / 1500) * 10) / 10);
      charEl.textContent = `${chars.toLocaleString()} caracteres · Formato A4 (~${estMin} min)`;
      charEl.className = 'font-mono text-zinc-300 font-semibold';
    }
  } catch (errCv) {
    console.error('Error renderizando previsualización de CV:', errCv);
  }

  // Auditoría: Dos Indicadores Diferenciados (Evaluación Profesional vs Diagnóstico ATS)
  const audit = (data.reporte_auditoria && typeof data.reporte_auditoria === 'object' && Object.keys(data.reporte_auditoria).length > 0)
    ? data.reporte_auditoria
    : null;

  const verdictEl = document.getElementById('auditVerdict');
  const afinidadEl = document.getElementById('auditAfinidadScore');
  const scoreAfinidad = data.match_score != null ? Math.round(data.match_score) : null;

  if (afinidadEl) {
    afinidadEl.textContent = scoreAfinidad != null ? `${scoreAfinidad}%` : '--%';
  }

  const atsEl = document.getElementById('atsScoreValue');
  const aiEl = document.getElementById('aiScoreValue');
  const kwPctEl = document.getElementById('keywordCoveragePct');
  const kwContainer = document.getElementById('keywordCoverageTags');
  const detectedEl = document.getElementById('auditDetectedAI');
  const dedContainer = document.getElementById('auditDeductionsContainer');
  const dedList = document.getElementById('auditDeductionsList');
  const dedTotal = document.getElementById('auditDeductionsTotal');

  if (audit) {
    const verdict = audit.verdict || (scoreAfinidad != null && scoreAfinidad >= 80 ? 'PASE A ENTREVISTA' : 'REQUIERE REMEDIACIÓN');
    if (verdictEl) {
      verdictEl.textContent = verdict;
      if (verdict === 'PASE A ENTREVISTA') {
        verdictEl.className = 'text-xs font-semibold text-emerald-400 px-2 py-0.5 rounded bg-emerald-950/40 border border-emerald-900/40';
      } else {
        verdictEl.className = 'text-xs font-semibold text-amber-300 px-2 py-0.5 rounded bg-amber-950/40 border border-amber-900/40';
      }
    }

    // Devolución Unificada de Auditoría y Botón Corregir CV
    const findings = audit.hallazgos_unificados || audit.diagnostico_documento?.hallazgos_unificados || null;
    renderAuditFindings(findings);

    // Diagnóstico del Documento (ATS & Keyword Coverage)
    const atsScore = Math.round(audit.ats_score ?? audit.ats?.ats_score ?? audit.diagnostico_documento?.ats_score ?? 0);
    if (atsEl) atsEl.textContent = `${atsScore}%`;

    const aiScore = Math.round(audit.anti_ai_score ?? audit.anti_ai?.anti_ai_score ?? 100);
    if (aiEl) aiEl.textContent = `${aiScore}%`;

    // Desglose Minimalista de Deducciones (< 100)
    const deductions = audit.deducciones || audit.diagnostico_documento?.deducciones || audit.ats?.deducciones || [];
    if (dedContainer && dedList) {
      if (deductions.length > 0 && (atsScore < 100 || aiScore < 100)) {
        dedContainer.classList.remove('hidden');
        dedList.innerHTML = deductions
          .slice(0, 4)
          .map(d => `<li class="flex items-start gap-1.5 text-zinc-300 text-xs"><span class="text-amber-400/80 flex-shrink-0">›</span><span>${escapeHtml(d)}</span></li>`)
          .join('');
        if (dedTotal) {
          dedTotal.textContent = `${deductions.length} observaciones`;
        }
      } else {
        dedContainer.classList.add('hidden');
        dedList.innerHTML = '';
      }
    }

    // Keyword Coverage Matrix
    const kwCoverage = audit.ats?.keyword_coverage ?? audit.diagnostico_documento?.keyword_coverage ?? 0;
    if (kwPctEl) {
      kwPctEl.textContent = `${Math.round(kwCoverage * 100)}%`;
    }

    if (kwContainer) {
      kwContainer.innerHTML = '';
      const foundKw = audit.ats?.found_keywords || audit.diagnostico_documento?.found_keywords || [];
      const missingKw = audit.ats?.missing_keywords || audit.diagnostico_documento?.missing_keywords || [];

      if (foundKw.length === 0 && missingKw.length === 0) {
        kwContainer.innerHTML = '<span class="text-zinc-300 italic text-xs">Sin keywords evaluadas aún.</span>';
      } else {
        foundKw.forEach(kw => {
          const span = document.createElement('span');
          span.className = 'px-1.5 py-0.2 rounded bg-emerald-950/40 text-emerald-400 border border-emerald-900/40 text-xs';
          span.textContent = `✓ ${kw}`;
          kwContainer.appendChild(span);
        });
        missingKw.forEach(kw => {
          const span = document.createElement('span');
          span.className = 'px-1.5 py-0.2 rounded bg-amber-950/40 text-amber-300 border border-amber-900/40 text-xs';
          span.textContent = `✗ ${kw}`;
          kwContainer.appendChild(span);
        });
      }
    }

    if (detectedEl) {
      const detected = audit.anti_ai?.detected_words || audit.evaluacion_profesional?.anti_ai_words || [];
      if (detected.length === 0) {
        detectedEl.innerHTML = '<span class="text-emerald-400 text-xs"><i class="fa-solid fa-check mr-1"></i>Ninguna palabra fetiche detectada. Redacción empírica limpia.</span>';
      } else {
        detectedEl.innerHTML = `<span class="text-rose-400 font-semibold text-xs"><i class="fa-solid fa-triangle-exclamation mr-1"></i>IA purgada:</span> <span class="text-zinc-300 text-xs">${escapeHtml(detected.join(', '))}</span>`;
      }
    }
  } else {
    // Estado neutral cuando la auditoría aún no se ha ejecutado o está pendiente
    if (verdictEl) {
      verdictEl.textContent = 'PENDIENTE DE EVALUACIÓN';
      verdictEl.className = 'text-xs font-semibold text-zinc-400 px-2 py-0.5 rounded bg-[#1e2026] border border-[#262930]';
    }
    renderAuditFindings(null);
    if (atsEl) atsEl.textContent = '--%';
    if (aiEl) aiEl.textContent = '--%';
    if (kwPctEl) kwPctEl.textContent = '--%';
    if (kwContainer) kwContainer.innerHTML = '<span class="text-zinc-400 italic text-xs">Auditoría pendiente de ejecución.</span>';
    if (detectedEl) detectedEl.innerHTML = '<span class="text-zinc-400 text-xs">Pendiente de evaluación</span>';
    if (dedContainer) {
      dedContainer.classList.add('hidden');
      if (dedList) dedList.innerHTML = '';
    }
  }

  // Lifecycle & Seguimiento
  const selEstado = document.getElementById('selectEstadoPostulacion');
  if (selEstado) {
    selEstado.value = data.estado_postulacion || 'POR_POSTULAR';
  }

  const badgeDias = document.getElementById('badgeDiasTranscurridos');
  if (badgeDias) {
    const dias = formatearDiasTranscurridos(data.fecha_postulacion);
    if (dias) {
      badgeDias.textContent = dias;
      badgeDias.classList.remove('hidden');
    } else {
      badgeDias.classList.add('hidden');
    }
  }

  const inputLink = document.getElementById('inputLinkPostulacion');
  if (inputLink) inputLink.value = data.link_postulacion || '';

  const btnIrLink = document.getElementById('btnIrLinkPostulacion');
  if (btnIrLink) {
    if (data.link_postulacion && data.link_postulacion.trim().startsWith('http')) {
      btnIrLink.href = data.link_postulacion.trim();
      btnIrLink.classList.remove('hidden');
      btnIrLink.classList.add('inline-flex');
    } else {
      btnIrLink.classList.add('hidden');
      btnIrLink.classList.remove('inline-flex');
    }
  }

  const inputContacto = document.getElementById('inputContactoRecruiter');
  if (inputContacto) inputContacto.value = data.contacto_recruiter || '';

  const inputNotas = document.getElementById('inputNotasSeguimiento');
  if (inputNotas) inputNotas.value = data.notas_seguimiento || '';

  const fechaDisplay = document.getElementById('fechaPostulacionDisplay');
  if (fechaDisplay) {
    fechaDisplay.textContent = data.fecha_postulacion ? `Postulada: ${formatearFechaOpaca(data.fecha_postulacion)}` : '';
  }
}

// Renderizar observaciones unificadas de auditoría y activar botón 'Corregir CV'
function renderAuditFindings(findings) {
  const box = document.getElementById('auditRemediationBox');
  const list = document.getElementById('auditFindingsList');
  if (!box || !list) return;

  if (!findings || !findings.lista_observaciones || findings.lista_observaciones.length === 0) {
    box.classList.add('hidden');
    list.innerHTML = '';
    return;
  }

  box.classList.remove('hidden');
  list.innerHTML = findings.lista_observaciones
    .map(obs => `<li>${escapeHtml(obs)}</li>`)
    .join('');
}

// Disparar remediación guiada por auditoría vía LLM (Regla de oro: 100% LLM-First)
async function remediarCVAuditoria() {
  if (!ofertaActual || !ofertaActual.id) {
    mostrarAlerta('No hay postulación seleccionada para corregir.', 'warning');
    return;
  }
  const btn = document.getElementById('btnCorregirCV');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-xs"></i> Corrigiendo...';
  }

  actualizarHUDProgreso({
    percent: 85,
    agent_title: 'Subagente 3: Auditor Ácido',
    step: 'Regenerando CV con directivas de auditoría vía LLM...',
    detail: 'Subsanando brechas, incorporando keywords aprobadas y puliendo el documento...',
    model: (telemetriaData && telemetriaData.configured_model) || 'gemini-3.8-flash-high',
    effort: 'high'
  });

  try {
    const res = await fetch(`/api/ofertas/${ofertaActual.id}/remediar`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Error al iniciar la corrección de auditoría');
    }
  } catch (err) {
    mostrarAlerta(`Error: ${err.message}`, 'error');
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-xs"></i> Corregir CV';
    }
  }
}

// Compatibilidad para sub-pestañas del Panel Central (en vista unificada viewCentralMatriz permanece visible)
function setPanelCentralTab(tab) {
  const viewMatriz = document.getElementById('viewCentralMatriz');
  if (viewMatriz) viewMatriz.classList.remove('hidden');
}

// Helpers para Match Score reactivo y aprendizajes
function actualizarScoreVisual(scoreNum, desglose = null) {
  const score = Math.round(scoreNum || 0);
  const scoreText = document.getElementById('scoreText');
  const scoreBar = document.getElementById('scoreBar');
  const verdictBadge = document.getElementById('scoreVerdictBadge');

  if (scoreText) scoreText.textContent = `${score}%`;
  if (scoreBar) scoreBar.style.width = `${score}%`;

  if (verdictBadge) {
    if (score >= 80) {
      verdictBadge.textContent = 'Pase a Entrevista';
      verdictBadge.className = 'font-semibold text-emerald-400 ml-1 text-xs';
    } else if (score >= 60) {
      verdictBadge.textContent = 'Transferibles Aceptables';
      verdictBadge.className = 'font-semibold text-amber-400 ml-1 text-xs';
    } else {
      verdictBadge.textContent = 'Brecha Significativa';
      verdictBadge.className = 'font-semibold text-rose-400 ml-1 text-xs';
    }
  }

  // Renderizar explicación accionable bajo el puntaje de afinidad
  let caminoContainer = document.getElementById('caminoAl100Container');
  if (!caminoContainer) {
    const barEl = document.getElementById('scoreBar');
    const barParent = barEl ? barEl.parentElement : null;
    if (barParent) {
      caminoContainer = document.createElement('div');
      caminoContainer.id = 'caminoAl100Container';
      barParent.insertAdjacentElement('afterend', caminoContainer);
    }
  }

  if (caminoContainer) {
    const desgloseObj = desglose || ofertaActual?.desglose_afinidad || ofertaActual?.analisis?.desglose_afinidad;
    if (desgloseObj && desgloseObj.camino_al_100) {
      if (score >= 100) {
        caminoContainer.innerHTML = `<p class="text-xs text-emerald-400 mt-1"><i class="fa-solid fa-circle-check text-emerald-400 mr-1"></i>${escapeHtml(desgloseObj.camino_al_100)}</p>`;
      } else {
        caminoContainer.innerHTML = `<p class="text-xs text-zinc-300 mt-1"><i class="fa-solid fa-circle-info text-amber-400 mr-1"></i>${escapeHtml(desgloseObj.camino_al_100)}</p>`;
      }
    } else if (score >= 100) {
      caminoContainer.innerHTML = `<p class="text-xs text-emerald-400 mt-1"><i class="fa-solid fa-circle-check text-emerald-400 mr-1"></i>Perfil 100% alineado con los requerimientos.</p>`;
    } else {
      caminoContainer.innerHTML = '';
    }
  }
}
window.actualizarScoreVisual = actualizarScoreVisual;

function actualizarCardOfertaSidebar(ofertaId, scoreNum, requisitos) {
  const card = document.getElementById(`card-oferta-${ofertaId}`);
  if (!card) return;
  const score = Math.round(scoreNum || 0);
  let scoreColor = 'bg-[#1e2026] text-zinc-400 border-[#262930]';
  if (score >= 80) {
    scoreColor = 'bg-emerald-950/40 text-emerald-400 border-emerald-900/40';
  } else if (score >= 60) {
    scoreColor = 'bg-amber-950/40 text-amber-300 border-amber-900/40';
  } else if (score > 0) {
    scoreColor = 'bg-rose-950/40 text-rose-300 border-rose-900/40';
  }
  const afinidad = resumenAfinidad(requisitos);
  const scoreBadge = card.querySelector('span.font-mono');
  if (scoreBadge) {
    scoreBadge.textContent = `${score}%`;
    scoreBadge.className = `text-xs font-mono px-1.5 py-0.2 rounded font-semibold border ${scoreColor}${afinidad && afinidad.mayormenteAprendido ? ' opacity-60' : ''}`;
  }
  const linea = card.querySelector('[data-afinidad]');
  if (linea && afinidad) linea.outerHTML = htmlAfinidad(afinidad, 'mb-1');
}
window.actualizarCardOfertaSidebar = actualizarCardOfertaSidebar;

// Desglose de la afinidad: cuántos requisitos cumplís, son transferibles, son brecha o están cubiertos por un aprendizaje
// (mismo criterio que la vista Radar). Sin requisitos devuelve null.
function resumenAfinidad(requisitos) {
  const d = { cumple: 0, transferible: 0, brecha: 0, aprendido: 0, total: 0 };
  (Array.isArray(requisitos) ? requisitos : []).forEach((r) => {
    if (!r || typeof r !== 'object') return;
    d.total += 1;
    const aprendido = r.aprendido || (typeof esTagAprendido === 'function' && esTagAprendido(r.nombre));
    if (r.estado === 'BRECHA_REAL') d.brecha += 1;
    else if (aprendido) d.aprendido += 1;
    else if (r.estado === 'MATCH_DIRECTO') d.cumple += 1;
    else if (r.estado === 'TRANSFERIBLE') d.transferible += 1;
  });
  if (!d.total) return null;
  const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
  const partes = [];
  if (d.cumple) partes.push(`${d.cumple} ✓`);
  if (d.transferible) partes.push(plural(d.transferible, 'transferible', 'transferibles'));
  if (d.aprendido) partes.push(plural(d.aprendido, 'aprendido', 'aprendidos'));
  partes.push(`${d.brecha} ${d.brecha > 1 ? 'brechas' : 'brecha'}`);
  let titulo = `Afinidad calculada sobre ${plural(d.total, 'requisito', 'requisitos')}: ${d.cumple} los cumplís por tu experiencia, ` +
    `${d.aprendido} porque los marcaste como aprendidos`;
  if (d.transferible) titulo += `, ${d.transferible} por experiencia parecida`;
  if (d.brecha) titulo += `, ${d.brecha} todavía sin cubrir`;
  return { texto: partes.join(' · '), titulo, mayormenteAprendido: d.aprendido * 2 > d.total };
}

// Línea chica bajo el título de la tarjeta: «6 ✓ · 27 aprendidos · 0 brecha» (con el title que explica la cuenta)
function htmlAfinidad(a, clase = '') {
  if (!a) return '';
  const leyenda = a.mayormenteAprendido ? ' · <span class="text-amber-300/80">mayormente por aprendizajes</span>' : '';
  return `<div data-afinidad class="text-xs text-zinc-400 ${clase}" title="${escapeHtml(a.titulo)}">${escapeHtml(a.texto)}${leyenda}</div>`;
}

function esTagAprendido(tagName) {
  if (!tagName || !window.aprendizajesCache) return false;
  const clean = tagName.trim().toLowerCase();
  return Boolean(window.aprendizajesCache[clean]);
}
window.esTagAprendido = esTagAprendido;

function actualizarBadgeAprendizajesUI() {
  const badge = document.getElementById('badgeAprendizajesHeader');
  const cant = document.getElementById('cantAprendizajesHeader');
  const total = (typeof window.totalAprendizajes === 'number')
    ? window.totalAprendizajes
    : (window.aprendizajesCache ? Object.keys(window.aprendizajesCache).length : 0);
  if (badge) {
    if (total > 0) {
      badge.classList.remove('hidden');
      badge.classList.add('inline-flex');
      if (cant) cant.textContent = total;
    } else {
      badge.classList.remove('inline-flex');
      badge.classList.add('hidden');
    }
  }
}
window.actualizarBadgeAprendizajesUI = actualizarBadgeAprendizajesUI;

// Renderizar Matriz de Oferta por categorías con estados de afinidad cromática y brechas
function renderMatrizOferta(matriz, postulacionId = null) {
  const cont = document.getElementById('contenedorMatrizOferta');
  if (!cont) return;

  const activeId = postulacionId || (typeof ofertaActual !== 'undefined' ? ofertaActual?.id : null) || null;

  // Fallback si la matriz no viene estructurada pero hay requisitos en ofertaActual
  if (!matriz || (!matriz.herramientas?.length && !matriz.metodologias?.length && !matriz.responsabilidades?.length && !matriz.otros?.length)) {
    if (typeof ofertaActual !== 'undefined' && ofertaActual?.requisitos?.length) {
      const reqs = ofertaActual.requisitos;
      const HERRAMIENTAS_KW = ["python", "sql", "bpmn", "visio", "jira", "confluence", "jenkins", "docker", "kubernetes", "git", "gitlab", "power bi", "powerbi", "tableau", "aws", "gcp", "servicenow", "excel", "rpa", "uipath", "postman", "linux", "oracle", "sap"];
      const METODOLOGIAS_KW = ["scrum", "kanban", "agile", "ágil", "lean", "six sigma", "kaizen", "itil", "as is", "as-is", "to be", "to-be", "kpi", "kpis", "mejora continua", "cascada", "uml"];
      matriz = { herramientas: [], metodologias: [], responsabilidades: [], otros: [] };
      reqs.forEach(r => {
        const n = (r.nombre || '').toLowerCase();
        if (HERRAMIENTAS_KW.some(k => n.includes(k))) matriz.herramientas.push(r);
        else if (METODOLOGIAS_KW.some(k => n.includes(k))) matriz.metodologias.push(r);
        else if (r.tipo === 'RESPONSABILIDAD' || ['relevamiento', 'diseño', 'coordinación', 'documentación', 'análisis', 'gestión'].some(k => n.includes(k))) matriz.responsabilidades.push(r);
        else matriz.otros.push(r);
      });
    }
  }

  if (!matriz || (!matriz.herramientas?.length && !matriz.metodologias?.length && !matriz.responsabilidades?.length && !matriz.otros?.length)) {
    cont.innerHTML = '<p class="text-xs text-zinc-300 italic">No se extrajo matriz estructurada de la oferta.</p>';
    return;
  }

  // Agrupar todos los items para métricas globales de afinidad y brechas
  const allItems = [
    ...(matriz.herramientas || []),
    ...(matriz.metodologias || []),
    ...(matriz.responsabilidades || []),
    ...(matriz.otros || [])
  ];

  const matchesDirectos = allItems.filter(it => (typeof it === 'object' && it.estado === 'MATCH_DIRECTO'));
  const transferibles = allItems.filter(it => (typeof it === 'object' && it.estado === 'TRANSFERIBLE'));
  const brechasReales = allItems.filter(it => (typeof it === 'object' && it.estado === 'BRECHA_REAL'));

  // Helper para renderizar cada chip con su cromática y tooltip según estado con soporte para ciclar en un clic
  // Orden universal: BRECHA_REAL -> TRANSFERIBLE -> MATCH_DIRECTO -> BRECHA_REAL
  const renderItemChip = (item) => {
    const nombre = typeof item === 'object' ? item.nombre : item;
    const estado = typeof item === 'object' ? (item.estado || 'MATCH_DIRECTO') : 'MATCH_DIRECTO';
    const evidencia = (typeof item === 'object' && item.evidencia) ? item.evidencia : '';
    const safeNombre = escapeHtml(nombre);
    const isLearned = typeof esTagAprendido === 'function' && esTagAprendido(nombre);

    const learnedBadge = isLearned
      ? `<span class="inline-flex items-center text-amber-400 text-xs font-medium ml-1" title="Aprendido de tus decisiones previas"><span class="font-bold">•</span> <span class="hidden sm:inline">Aprendido</span></span>`
      : '';

    if (estado === 'BRECHA_REAL') {
      return `
        <span data-ciclar-tag="${safeNombre}" class="cursor-pointer hover:opacity-80 active:scale-95 transition-all select-none inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-rose-950/40 text-rose-300 border border-rose-800/50 font-mono" title="Brecha Real (clic para alternar a Transferible): ${escapeHtml(evidencia || 'Sin experiencia directa identificada')}">
          <i class="fa-solid fa-triangle-exclamation text-xs text-rose-400"></i>
          <span>${safeNombre}</span>
          ${learnedBadge}
        </span>
      `;
    } else if (estado === 'TRANSFERIBLE') {
      return `
        <span data-ciclar-tag="${safeNombre}" class="cursor-pointer hover:opacity-80 active:scale-95 transition-all select-none inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-amber-950/40 text-amber-300 border border-amber-800/50 font-mono" title="Transferible (clic para alternar a Match Directo): ${escapeHtml(evidencia || 'Herramienta análoga comprobable')}">
          <i class="fa-solid fa-arrows-rotate text-xs text-amber-400"></i>
          <span>${safeNombre}</span>
          ${learnedBadge}
        </span>
      `;
    } else {
      return `
        <span data-ciclar-tag="${safeNombre}" class="cursor-pointer hover:opacity-80 active:scale-95 transition-all select-none inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-emerald-950/40 text-emerald-300 border border-emerald-800/50 font-mono" title="Match Directo (clic para alternar a Brecha Real): ${escapeHtml(evidencia || 'Comprobable en experiencia')}">
          <i class="fa-solid fa-check text-xs text-emerald-400"></i>
          <span>${safeNombre}</span>
          ${learnedBadge}
        </span>
      `;
    }
  };

  const renderSection = (titulo, icono, items) => {
    if (!items || items.length === 0) return '';
    return `
      <div class="bg-[#16181d] border border-[#262930] rounded-lg p-2.5 space-y-1.5">
        <div class="flex items-center justify-between text-xs font-semibold text-zinc-300">
          <span class="flex items-center gap-1.5">
            <i class="${icono} text-zinc-400 text-xs"></i>
            ${titulo}
          </span>
          <span class="font-mono text-xs text-zinc-400 px-1.5 py-0.2 rounded bg-[#1e2026]">${items.length}</span>
        </div>
        <div class="flex flex-wrap gap-1">
          ${items.map(renderItemChip).join('')}
        </div>
      </div>
    `;
  };

  // Resumen superior de Brechas Críticas interactivas
  let brechasSummaryHtml = '';
  if (brechasReales.length > 0) {
    brechasSummaryHtml = `
      <div class="bg-rose-950/20 border border-rose-900/40 rounded-lg p-2.5 space-y-1.5">
        <div class="flex items-center justify-between">
          <span class="text-xs font-semibold text-rose-300 flex items-center gap-1.5">
            <i class="fa-solid fa-triangle-exclamation text-rose-400"></i>
            Brechas Críticas Identificadas (${brechasReales.length})
          </span>
          <span class="text-xs text-zinc-400 italic">Clic en un tag para alternar</span>
        </div>
        <p class="text-xs text-zinc-300">Requerimientos sin match directo. Hacé clic para alternar de estado:</p>
        <div class="flex flex-wrap gap-1 pt-0.5">
          ${brechasReales.map(b => {
            const bNombre = typeof b === 'object' ? b.nombre : b;
            const safeBNombre = escapeHtml(bNombre);
            const isLearned = typeof esTagAprendido === 'function' && esTagAprendido(bNombre);
            const learnedBadge = isLearned ? `<span class="text-amber-400 text-xs font-medium ml-1" title="Aprendido">•</span>` : '';
            return `
              <span data-ciclar-tag="${safeBNombre}" class="cursor-pointer hover:opacity-80 active:scale-95 transition-all select-none px-1.5 py-0.5 rounded text-xs bg-rose-950/50 text-rose-300 border border-rose-800/60 font-mono inline-flex items-center gap-1" title="Brecha Real (clic para promover a Transferible)">
                <i class="fa-solid fa-triangle-exclamation text-xs text-rose-400"></i>
                <span>${safeBNombre}</span>
                ${learnedBadge}
              </span>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }

  const legendHtml = `
    <div class="flex flex-wrap items-center justify-between gap-1.5 text-xs text-zinc-400 px-1 py-1 border-b border-[#262930]">
      <div class="flex items-center gap-2">
        <span class="inline-flex items-center gap-1 text-emerald-400 font-medium"><i class="fa-solid fa-check text-xs"></i> ${matchesDirectos.length} Match</span>
        <span class="inline-flex items-center gap-1 text-amber-300 font-medium"><i class="fa-solid fa-arrows-rotate text-xs"></i> ${transferibles.length} Transferible</span>
        <span class="inline-flex items-center gap-1 text-rose-400 font-medium"><i class="fa-solid fa-triangle-exclamation text-xs"></i> ${brechasReales.length} Brecha</span>
      </div>
      <div class="flex items-center gap-2">
        ${(window.totalAprendizajes && window.totalAprendizajes > 0) ? `
          <span class="inline-flex items-center gap-1 text-xs text-amber-300/90 bg-amber-950/40 border border-amber-800/40 px-1.5 py-0.5 rounded-full font-medium" title="Competencias aprendidas de tus decisiones guardadas permanentemente">
            <i class="fa-solid fa-graduation-cap text-xs text-amber-400"></i> ${window.totalAprendizajes} aprendidas
          </span>
        ` : ''}
        <span class="text-zinc-400 font-mono">${allItems.length} requerimientos</span>
      </div>
    </div>
  `;

  cont.innerHTML = `
    ${legendHtml}
    ${htmlAfinidad(resumenAfinidad(allItems), 'px-1')}
    ${brechasSummaryHtml}
    ${renderSection('Herramientas & Tecnologías', 'fa-solid fa-screwdriver-wrench', matriz.herramientas)}
    ${renderSection('Metodologías & Procesos', 'fa-solid fa-diagram-project', matriz.metodologias)}
    ${renderSection('Responsabilidades Principales', 'fa-solid fa-briefcase', matriz.responsabilidades)}
    ${renderSection('Otros Requerimientos', 'fa-solid fa-list-check', matriz.otros)}
  `;
  // A1 (auditoría 17/11): el nombre del requisito sale de la oferta (terceros). Va en data-* (texto) y el clic se
  // engancha acá: dentro de un onclick="…" el navegador decodifica &#39; y el nombre se ejecutaría como código.
  cont.querySelectorAll('[data-ciclar-tag]').forEach((chip) => {
    chip.addEventListener('click', () => ciclarEstadoTag(activeId, chip.dataset.ciclarTag));
  });
}

// Limpiar el detalle de vacante cuando no hay ninguna oferta seleccionada
function limpiarDetalleOferta() {
  ofertaActual = null;
  const tit = document.getElementById('ofertaTitulo');
  if (tit) tit.textContent = 'Sin oferta seleccionada';
  const emp = document.getElementById('ofertaEmpresa');
  if (emp) emp.textContent = 'Empresa';
  const cont = document.getElementById('ofertaContenido');
  if (cont) cont.textContent = 'Seleccioná una postulación en la izquierda para ver su contenido...';
  const badge = document.getElementById('ofertaStatusBadge');
  if (badge) {
    badge.textContent = '--';
    badge.className = 'text-xs font-mono px-2 py-0.5 rounded bg-[#1e2026] text-zinc-400 border border-[#262930]';
  }
  const doloresList = document.getElementById('listaDolores');
  if (doloresList) doloresList.innerHTML = '<li class="text-zinc-300 italic text-xs">Esperando análisis de oferta...</li>';
  const scoreText = document.getElementById('scoreText');
  if (scoreText) scoreText.textContent = '0%';
  const scoreBar = document.getElementById('scoreBar');
  if (scoreBar) scoreBar.style.width = '0%';
  const caminoContainer = document.getElementById('caminoAl100Container');
  if (caminoContainer) caminoContainer.innerHTML = '';
  const scoreVerdict = document.getElementById('scoreVerdictBadge');
  if (scoreVerdict) {
    scoreVerdict.textContent = 'Pendiente';
    scoreVerdict.className = 'text-xs font-medium text-zinc-400 ml-1';
  }
  const tagsList = document.getElementById('contenedorTags');
  if (tagsList) tagsList.innerHTML = '<p class="text-xs text-zinc-300 italic">No hay requerimientos clasificados.</p>';
  const matrizCont = document.getElementById('contenedorMatrizOferta');
  if (matrizCont) matrizCont.innerHTML = '<p class="text-xs text-zinc-300 italic">Seleccioná una postulación para visualizar la matriz de requerimientos.</p>';
  const cvContainer = document.getElementById('cvContent');
  if (cvContainer) cvContainer.innerHTML = '<p class="text-zinc-300 italic text-xs">El CV adaptado se mostrará aquí una vez procesada la oferta.</p>';
  const cvCharCount = document.getElementById('cvCharCount');
  if (cvCharCount) cvCharCount.textContent = '0 caracteres · Formato A4';
  const auditVerdict = document.getElementById('auditVerdict');
  if (auditVerdict) {
    auditVerdict.textContent = 'PENDIENTE DE EVALUACIÓN';
    auditVerdict.className = 'text-xs font-semibold text-zinc-400 px-2 py-0.5 rounded bg-[#1e2026] border border-[#262930]';
  }
  const auditBox = document.getElementById('auditRemediationBox');
  if (auditBox) auditBox.classList.add('hidden');
  const atsScore = document.getElementById('atsScoreValue');
  if (atsScore) atsScore.textContent = '--%';
  const afinidadEl = document.getElementById('auditAfinidadScore');
  if (afinidadEl) afinidadEl.textContent = '--%';
  const kwPctEl = document.getElementById('keywordCoveragePct');
  if (kwPctEl) kwPctEl.textContent = '--%';
  const kwContainer = document.getElementById('keywordCoverageTags');
  if (kwContainer) kwContainer.innerHTML = '<span class="text-zinc-300 italic text-xs">Sin keywords evaluadas</span>';
  const aiScore = document.getElementById('aiScoreValue');
  if (aiScore) aiScore.textContent = '--%';
  const detectedEl = document.getElementById('auditDetectedAI');
  if (detectedEl) detectedEl.innerHTML = '<span class="text-zinc-400 text-xs">Sin evaluación</span>';
  
  // Limpiar controles de lifecycle
  const selEstado = document.getElementById('selectEstadoPostulacion');
  if (selEstado) selEstado.value = 'POR_POSTULAR';
  const badgeDias = document.getElementById('badgeDiasTranscurridos');
  if (badgeDias) badgeDias.classList.add('hidden');
  const inputLink = document.getElementById('inputLinkPostulacion');
  if (inputLink) inputLink.value = '';
  const btnIrLink = document.getElementById('btnIrLinkPostulacion');
  if (btnIrLink) {
    btnIrLink.classList.add('hidden');
    btnIrLink.classList.remove('inline-flex');
  }
  const inputContacto = document.getElementById('inputContactoRecruiter');
  if (inputContacto) inputContacto.value = '';
  const inputNotas = document.getElementById('inputNotasSeguimiento');
  if (inputNotas) inputNotas.value = '';
  const fechaDisplay = document.getElementById('fechaPostulacionDisplay');
  if (fechaDisplay) fechaDisplay.textContent = '';

  resetBotonDescartar();
}

// Botón Descartar In-Place (2 pasos de confirmación sin modal invasivo)
function resetBotonDescartar() {
  if (descartarTimeout) {
    clearTimeout(descartarTimeout);
    descartarTimeout = null;
  }
  descartarConfirmando = false;
  const btn = document.getElementById('btnDescartarOferta');
  if (!btn) return;
  btn.className = 'px-2.5 py-1 rounded text-xs font-medium bg-[#1e2026] hover:bg-rose-950/40 text-zinc-400 hover:text-rose-300 border border-[#262930] hover:border-rose-900/60 transition-colors flex items-center gap-1.5 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed';
  btn.innerHTML = '<i class="fa-solid fa-trash-can text-xs"></i><span id="lblDescartar">Descartar</span>';
  btn.disabled = !ofertaActual;
}

async function descartarOfertaClick(btn) {
  if (!ofertaActual) return;

  if (!descartarConfirmando) {
    // Paso 1: Pedir confirmación in-place en el mismo botón con timeout de 3.5s
    descartarConfirmando = true;
    btn.className = 'px-2.5 py-1 rounded text-xs font-semibold bg-rose-950/90 text-rose-200 border border-rose-700 hover:bg-rose-900 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm';
    btn.innerHTML = '<i class="fa-solid fa-triangle-exclamation text-xs text-rose-300"></i><span id="lblDescartar">¿Confirmar eliminación?</span>';

    descartarTimeout = setTimeout(() => {
      resetBotonDescartar();
    }, 3500);
    return;
  }

  // Paso 2: Segundo clic confirmado — Ejecutar DELETE definitivo sin rastros
  if (descartarTimeout) {
    clearTimeout(descartarTimeout);
    descartarTimeout = null;
  }
  descartarConfirmando = false;

  const idAEliminar = ofertaActual.id;
  btn.disabled = true;
  btn.className = 'px-2.5 py-1 rounded text-xs font-medium bg-[#1e2026] text-zinc-400 border border-[#262930] flex items-center gap-1.5 cursor-wait';
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-xs"></i><span>Eliminando...</span>';

  try {
    const res = await fetch(`/api/ofertas/${idAEliminar}`, {
      method: 'DELETE',
    });

    if (!res.ok) {
      alert('No se pudo eliminar la oferta de la base de datos.');
      resetBotonDescartar();
      return;
    }

    // Purgar de memoria local y sincronizar contador
    listaOfertasMemoria = listaOfertasMemoria.filter(o => o.id !== idAEliminar);
    const badgeCount = document.getElementById('contadorOfertasBadge');
    if (badgeCount) badgeCount.textContent = listaOfertasMemoria.length;

    if (listaOfertasMemoria.length > 0) {
      renderizarListaOfertas(listaOfertasMemoria, listaOfertasMemoria[0].id);
      await seleccionarOferta(listaOfertasMemoria[0].id, false);
    } else {
      renderizarListaOfertas([], null);
      limpiarDetalleOferta();
    }
  } catch (err) {
    console.error('Error al descartar oferta:', err);
    alert('Ocurrió un error al intentar eliminar la oferta.');
    resetBotonDescartar();
  }
}

// Renderizado compacto de tags con soporte de filtros y ciclo de 3 estados
function renderizarListaTags() {
  if (!ofertaActual) return;
  const tagsContainer = document.getElementById('contenedorTags');
  if (!tagsContainer) return;
  tagsContainer.innerHTML = '';

  const activeId = ofertaActual.id;
  const reqs = ofertaActual.requisitos || [];
  if (reqs.length === 0) {
    tagsContainer.innerHTML = '<p class="text-xs text-zinc-300 italic">No hay requerimientos clasificados.</p>';
    return;
  }

  const filtrados = reqs.map((r, idx) => ({ ...r, originalIdx: idx })).filter((r) => {
    if (currentTagFilter === 'TODOS') return true;
    if (['MATCH_DIRECTO', 'TRANSFERIBLE', 'BRECHA_REAL'].includes(currentTagFilter)) {
      return (r.estado || '').toUpperCase() === currentTagFilter;
    }
    if (['RESPONSABILIDAD', 'REQUISITO_EXCLUYENTE', 'REQUISITO_DESEABLE'].includes(currentTagFilter)) {
      return (r.tipo || '').toUpperCase() === currentTagFilter;
    }
    return true;
  });

  if (filtrados.length === 0) {
    tagsContainer.innerHTML = '<p class="text-xs text-zinc-300 italic">No hay requerimientos en esta categoría.</p>';
    return;
  }

  filtrados.forEach((r) => {
    const estado = (r.estado || '').toUpperCase();
    const safeNombre = escapeHtml(r.nombre);
    const isLearned = typeof esTagAprendido === 'function' && esTagAprendido(r.nombre);

    let pillColor = 'bg-[#16181d] text-zinc-300 border-[#262930] hover:border-zinc-500';
    let label = 'MATCH';
    let icon = 'fa-check text-emerald-400';
    let nextTip = 'Brecha Real';

    if (estado === 'MATCH_DIRECTO') {
      pillColor = 'bg-emerald-950/30 text-emerald-300 border-emerald-900/40 hover:border-emerald-700/60';
      label = 'MATCH';
      icon = 'fa-check text-emerald-400';
      nextTip = 'Brecha Real';
    } else if (estado === 'TRANSFERIBLE') {
      pillColor = 'bg-amber-950/30 text-amber-300 border-amber-900/40 hover:border-amber-700/60';
      label = 'TRANSFERIBLE';
      icon = 'fa-arrows-rotate text-amber-400';
      nextTip = 'Match Directo';
    } else if (estado === 'BRECHA_REAL') {
      pillColor = 'bg-rose-950/30 text-rose-300 border-rose-900/40 hover:border-rose-700/60';
      label = 'BRECHA';
      icon = 'fa-triangle-exclamation text-rose-400';
      nextTip = 'Transferible';
    }

    let tipoBadge = '';
    const tipo = (r.tipo || '').toUpperCase();
    if (tipo === 'RESPONSABILIDAD') {
      tipoBadge = '<span class="text-[11px] uppercase font-mono px-1.5 py-0.2 rounded bg-[#1e2026] text-zinc-400 border border-[#262930] flex-shrink-0">RESP.</span>';
    } else if (tipo === 'REQUISITO_EXCLUYENTE') {
      tipoBadge = '<span class="text-[11px] uppercase font-mono px-1.5 py-0.2 rounded bg-[#1e2026] text-zinc-300 border border-zinc-600 flex-shrink-0">EXCLUYENTE</span>';
    } else if (tipo === 'REQUISITO_DESEABLE') {
      tipoBadge = '<span class="text-[11px] uppercase font-mono px-1.5 py-0.2 rounded bg-[#1e2026] text-zinc-400 border border-[#262930] flex-shrink-0">DESEABLE</span>';
    }

    const item = document.createElement('div');
    item.className = `px-2.5 py-1.5 rounded-lg border text-xs flex items-center justify-between gap-2 transition-all cursor-pointer hover:opacity-90 active:scale-[0.99] select-none ${pillColor}`;
    item.title = `Hacé clic para alternar estado a ${nextTip}`;
    item.onclick = () => {
      ciclarEstadoTag(activeId, r.nombre);
    };

    item.innerHTML = `
      <div class="flex items-center gap-1.5 truncate">
        <i class="fa-solid ${icon} text-xs flex-shrink-0"></i>
        ${tipoBadge}
        <span class="font-medium text-zinc-200 truncate">${safeNombre}</span>
        ${isLearned ? `<span class="inline-flex items-center gap-0.5 text-amber-400 text-xs font-medium flex-shrink-0" title="Aprendido de tus decisiones previas"><span class="font-bold">•</span> Aprendido</span>` : ''}
        ${r.equivalencia ? `<span class="text-xs text-zinc-400 truncate hidden sm:inline">(${escapeHtml(r.equivalencia)})</span>` : ''}
      </div>
      <div class="flex items-center gap-1.5 flex-shrink-0">
        <span class="text-xs uppercase font-mono px-1.5 py-0.2 rounded bg-[#121418] font-semibold tracking-wider text-zinc-400 border border-[#262930]">
          ${label}
        </span>
      </div>
    `;
    tagsContainer.appendChild(item);
  });
}

function filtrarTags(tipo) {
  currentTagFilter = tipo;
  ['TODOS', 'MATCH_DIRECTO', 'TRANSFERIBLE', 'BRECHA_REAL', 'RESPONSABILIDAD', 'REQUISITO_EXCLUYENTE', 'REQUISITO_DESEABLE'].forEach((t) => {
    const btn = document.getElementById(`filter-${t}`);
    if (btn) {
      if (t === tipo) {
        btn.className = 'px-2 py-0.5 rounded bg-[#1e2026] text-zinc-100 border border-zinc-600 font-medium';
      } else {
        btn.className = 'px-2 py-0.5 rounded bg-[#16181d] text-zinc-400 hover:text-zinc-200 border border-[#262930]';
      }
    }
  });
  renderizarListaTags();
}

// Alternar Tag Transferible
async function toggleTagItem(tagIndex) {
  if (!ofertaActual) return;
  try {
    const res = await fetch(`/api/ofertas/${ofertaActual.id}/toggle-tag`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag_index: tagIndex }),
    });
    if (!res.ok) return;
    ofertaActual = await res.json();
    renderOferta(ofertaActual);

    // Actualizar badge en lista lateral
    const card = document.getElementById(`card-oferta-${ofertaActual.id}`);
    if (card) {
      const score = Math.round(ofertaActual.match_score || 0);
      let scoreColor = 'bg-[#1e2026] text-zinc-400 border-[#262930]';
      if (score >= 80) {
        scoreColor = 'bg-emerald-950/40 text-emerald-400 border-emerald-900/40';
      } else if (score >= 60) {
        scoreColor = 'bg-amber-950/40 text-amber-300 border-amber-900/40';
      } else if (score > 0) {
        scoreColor = 'bg-rose-950/40 text-rose-300 border-rose-900/40';
      }
      const scoreBadge = card.querySelector('span.font-mono');
      if (scoreBadge) {
        scoreBadge.textContent = `${score}%`;
        scoreBadge.className = `text-xs font-mono px-1.5 py-0.2 rounded font-semibold border ${scoreColor}`;
      }
    }
  } catch (err) {
    console.error('Error toggling tag:', err);
  }
}

// Acordeón para expandir/colapsar texto de oferta
function toggleTextoOferta() {
  const container = document.getElementById('ofertaContenido');
  const label = document.getElementById('labelToggleTexto');
  const icon = document.getElementById('iconToggleTexto');
  isTextoOfertaExpanded = !isTextoOfertaExpanded;

  if (isTextoOfertaExpanded) {
    container.classList.remove('max-h-56');
    container.classList.add('max-h-none');
    label.textContent = 'Colapsar';
    icon.className = 'fa-solid fa-chevron-up text-xs';
  } else {
    container.classList.remove('max-h-none');
    container.classList.add('max-h-56');
    label.textContent = 'Expandir';
    icon.className = 'fa-solid fa-chevron-down text-xs';
  }
}

// Botón de Regeneración Completa
async function regenerarPostulacionActual() {
  if (!ofertaActual) {
    alert('Por favor seleccioná una postulación primero.');
    return;
  }

  const btn = document.getElementById('btnRegenerarGlobal');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-arrows-rotate fa-spin text-xs"></i> <span class="hidden sm:inline">Regenerando...</span>';
  }

  mostrarAlertaEstado(`Volviendo a analizar la oferta y generando el CV para ${ofertaActual.titulo}...`);

  try {
    const res = await fetch(`/api/ofertas/${ofertaActual.id}/regenerate`, { method: 'POST' });
    if (!res.ok) throw new Error('Error al solicitar regeneración');
  } catch (err) {
    console.error('Error regenerando postulación:', err);
    restaurarBotonRegenerar();
  }
}

function restaurarBotonRegenerar() {
  const btn = document.getElementById('btnRegenerarGlobal');
  if (btn) {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-arrows-rotate text-xs"></i> <span class="hidden sm:inline">Regenerar</span>';
  }
}

// Recalcular CV exclusivamente con tags seleccionados
async function recalcularCVConTags() {
  if (!ofertaActual) return;
  mostrarAlertaEstado('Regenerando el CV con las competencias seleccionadas...');
  try {
    const res = await fetch(`/api/ofertas/${ofertaActual.id}/recalculate-cv`, { method: 'POST' });
    if (!res.ok) throw new Error('Error al recalcular CV');
  } catch (err) {
    console.error('Error recalculando CV:', err);
  }
}

// Formato del CV: «completo» (por defecto, sin límite de páginas) o «una_pagina» (agrupado por empresa)
function formatoCvActual(data) {
  return (data && data.formato_cv === 'una_pagina') ? 'una_pagina' : 'completo';
}

function actualizarBotonFormatoCv(data) {
  const texto = document.getElementById('btnFormatoCvTexto');
  if (!texto) return;
  texto.textContent = formatoCvActual(data) === 'una_pagina' ? 'Volver al CV completo' : 'Versión de 1 página';
}

async function alternarFormatoCv() {
  if (!ofertaActual) return;
  const nuevo = formatoCvActual(ofertaActual) === 'una_pagina' ? 'completo' : 'una_pagina';
  mostrarAlertaEstado(nuevo === 'una_pagina'
    ? 'Generando la versión de 1 página del CV...'
    : 'Generando el CV completo...');
  try {
    const res = await fetch(`/api/ofertas/${ofertaActual.id}/recalculate-cv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ formato: nuevo }),
    });
    if (!res.ok) throw new Error('Error al cambiar el formato del CV');
    ofertaActual.formato_cv = nuevo;
    actualizarBotonFormatoCv(ofertaActual);
  } catch (err) {
    console.error('Error cambiando el formato del CV:', err);
  }
}

// Exportar DOCX A4
function descargarDocx() {
  if (!ofertaActual) return;
  window.open(`/api/ofertas/${ofertaActual.id}/export/docx`, '_blank');
}

// Exportar PDF A4
function descargarPdf() {
  if (!ofertaActual) return;
  window.open(`/api/ofertas/${ofertaActual.id}/export/pdf`, '_blank');
}

// Manejo de Tabs Derecha en Desktop
function setRightTab(tab) {
  currentRightTab = tab;
  ['cv', 'audit'].forEach((t) => {
    const subtab = document.getElementById(`subtab-${t}`);
    if (subtab) subtab.classList.toggle('hidden', t !== tab);
    const btn = document.getElementById(`rightTab-${t}`);
    if (btn) {
      if (t === tab) {
        btn.className = 'text-xs font-medium px-2 py-1 rounded bg-[#1e2026] text-zinc-100 border border-zinc-600';
      } else {
        btn.className = 'text-xs font-medium px-2 py-1 rounded text-zinc-400 hover:text-zinc-200';
      }
    }
  });
}

// Manejo de Tabs en Móvil
function setMobileTab(tab) {
  currentMobileTab = tab;
  ['ofertas', 'vacante', 'tags', 'cv'].forEach((t) => {
    const btn = document.getElementById(`tabBtn-${t}`);
    if (btn) {
      if (t === tab) {
        btn.className = 'flex-1 py-2.5 px-1 font-medium text-center border-b-2 border-zinc-200 text-zinc-100';
      } else {
        btn.className = 'flex-1 py-2.5 px-1 font-medium text-center border-b-2 border-transparent text-zinc-400';
      }
    }
  });

  const panelOfertas = document.getElementById('panel-ofertas');
  const panelMedio = document.getElementById('panel-medio');
  const subpanelVacante = document.getElementById('subpanel-vacante');
  const subpanelTags = document.getElementById('subpanel-tags');
  const panelCV = document.getElementById('panel-cv');

  if (!panelOfertas || !panelMedio || !panelCV) return;

  if (tab === 'ofertas') {
    panelOfertas.classList.remove('hidden');
    panelMedio.classList.add('hidden');
    panelCV.classList.add('hidden');
  } else if (tab === 'vacante') {
    panelOfertas.classList.add('hidden');
    panelMedio.classList.remove('hidden');
    if (subpanelVacante) subpanelVacante.classList.remove('hidden');
    if (subpanelTags) subpanelTags.classList.add('hidden');
    panelCV.classList.add('hidden');
  } else if (tab === 'tags') {
    panelOfertas.classList.add('hidden');
    panelMedio.classList.remove('hidden');
    if (subpanelVacante) subpanelVacante.classList.add('hidden');
    if (subpanelTags) subpanelTags.classList.remove('hidden');
    panelCV.classList.add('hidden');
  } else if (tab === 'cv') {
    panelOfertas.classList.add('hidden');
    panelMedio.classList.add('hidden');
    panelCV.classList.remove('hidden');
  }
}

// Modal Nueva Oferta
function abrirModalNueva() {
  document.getElementById('modalNueva').classList.remove('hidden');
  setModalTab('manual');
}

function cerrarModalNueva() {
  document.getElementById('modalNueva').classList.add('hidden');
}

function setModalTab(tab) {
  currentModalTab = tab;
  ['manual', 'url', 'batch'].forEach((t) => {
    const btn = document.getElementById(`modalTab-${t}`);
    const form = document.getElementById(`form-${t}`);
    if (btn) {
      if (t === tab) {
        btn.className = 'px-2.5 py-1 rounded bg-[#1e2026] text-zinc-100 border border-zinc-600 font-medium';
      } else {
        btn.className = 'px-2.5 py-1 rounded text-zinc-400 hover:text-zinc-200 font-medium';
      }
    }
    if (form) {
      form.classList.toggle('hidden', t !== tab);
    }
  });
}

async function enviarNuevaOferta() {
  if (currentModalTab === 'manual') {
    const titulo = document.getElementById('nuevoTitulo').value.trim();
    const empresa = document.getElementById('nuevaEmpresa').value.trim();
    const contenido = document.getElementById('nuevoContenido').value.trim();

    if (!contenido) {
      alert('Por favor pegá el texto de la vacante.');
      return;
    }

    cerrarModalNueva();
    cambiarVista('manual');

    try {
      const res = await fetch('/api/ofertas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ titulo, empresa, contenido }),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        if (data && data.id != null && window.Radar && typeof window.Radar.abrirPost === 'function') {
          window.Radar.abrirPost(data.id);
        }
      }
    } catch (err) {
      console.error('Error enviando oferta:', err);
    }
  } else if (currentModalTab === 'url') {
    const url = document.getElementById('inputSoloUrl').value.trim();
    if (!url || !url.startsWith('http')) {
      alert('Por favor ingresá una URL válida que comience con http:// o https://');
      return;
    }

    cerrarModalNueva();
    cambiarVista('manual');

    try {
      const res = await fetch('/api/ofertas/url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        if (data && data.id != null && window.Radar && typeof window.Radar.abrirPost === 'function') {
          window.Radar.abrirPost(data.id);
        }
      }
    } catch (err) {
      console.error('Error enviando URL:', err);
    }
  } else if (currentModalTab === 'batch') {
    const urlsText = document.getElementById('inputBatchUrls').value.trim();
    const urls = urlsText.split('\n').map(u => u.trim()).filter(u => u.startsWith('http'));

    if (urls.length === 0) {
      alert('Por favor ingresá al menos una URL válida.');
      return;
    }

    cerrarModalNueva();
    cambiarVista('manual');

    try {
      const res = await fetch('/api/ofertas/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls }),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const targetId = data && (data.id != null ? data.id : (Array.isArray(data.ids) ? data.ids[0] : null));
        if (targetId != null && window.Radar && typeof window.Radar.abrirPost === 'function') {
          window.Radar.abrirPost(targetId);
        }
      }
    } catch (err) {
      console.error('Error enviando lote:', err);
    }
  }
}

const cerrarModalNuevaOferta = cerrarModalNueva;
const guardarNuevaOfertaModal = enviarNuevaOferta;
if (typeof window !== 'undefined') {
  window.cerrarModalNuevaOferta = cerrarModalNuevaOferta;
  window.guardarNuevaOfertaModal = guardarNuevaOfertaModal;
}

async function cargarAprendizajes() {
  try {
    const res = await fetch('/api/candidato/aprendizajes');
    if (!res.ok) return;
    const data = await res.json();
    const items = data.aprendizajes || {};
    window.aprendizajesCache = items;
    window.totalAprendizajes = Object.keys(items).length;
    actualizarBadgeAprendizajesUI();
  } catch (err) {
    console.error('Error al cargar aprendizajes:', err);
  }
}
window.cargarAprendizajes = cargarAprendizajes;

window.ciclarEstadoTag = async function(ofertaId, tagName) {
  if (!ofertaId || !tagName) return;

  // Feedback táctil inmediato en el chip clickeado si existe
  try {
    const safeTagLower = tagName.trim().toLowerCase();
    const allChips = document.querySelectorAll(`[onclick*="${safeTagLower}"]`);
    allChips.forEach(c => {
      c.classList.add('scale-95', 'opacity-70');
      setTimeout(() => c.classList.remove('scale-95', 'opacity-70'), 180);
    });
  } catch (_) {}

  try {
    const res = await fetch(`/api/ofertas/${ofertaId}/tags/${encodeURIComponent(tagName)}/cycle-state`, {
      method: 'POST'
    });
    if (!res.ok) throw new Error('Error al ciclar estado de la habilidad');
    const data = await res.json();

    if (typeof data.aprendizajes_totales === 'number') {
      window.totalAprendizajes = data.aprendizajes_totales;
    }
    if (data.aprendizajes) {
      window.aprendizajesCache = data.aprendizajes;
    } else if (data.nuevo_estado) {
      if (!window.aprendizajesCache) window.aprendizajesCache = {};
      if (data.nuevo_estado === 'BRECHA_REAL') {
        delete window.aprendizajesCache[tagName.trim().toLowerCase()];
      } else {
        window.aprendizajesCache[tagName.trim().toLowerCase()] = { estado: data.nuevo_estado };
      }
    }
    actualizarBadgeAprendizajesUI();

    if (ofertaActual && ofertaActual.id === ofertaId) {
      if (data.analisis) {
        ofertaActual.analisis = data.analisis;
      }
      if (data.requisitos) {
        ofertaActual.requisitos = data.requisitos;
      }
      if (typeof data.match_score === 'number') {
        ofertaActual.match_score = data.match_score;
      }
      if (data.desglose_afinidad) {
        ofertaActual.desglose_afinidad = data.desglose_afinidad;
      }

      // 1. Recalcular y actualizar reactivamente el Match Score visual en la barra superior
      actualizarScoreVisual(ofertaActual.match_score, data.desglose_afinidad || ofertaActual.desglose_afinidad);

      // 2. Re-renderizar matriz y lista de tags reactivamente
      const matrizOferta = ofertaActual.analisis?.matriz_oferta || null;
      renderMatrizOferta(matrizOferta, ofertaId);
      renderizarListaTags();

      // 3. Actualizar badge de la oferta en la barra lateral izquierda
      actualizarCardOfertaSidebar(ofertaId, ofertaActual.match_score, ofertaActual.requisitos);
    }
  } catch (err) {
    console.error('Error al alternar estado del tag:', err);
  }
};

// Telemetría y Selector Unificado de Modelo AGY CLI
async function actualizarTelemetriaModelo() {
  try {
    const res = await fetch('/api/model-info');
    if (!res.ok) return;
    const data = await res.json();
    telemetriaData = data;

    const labelModelo = document.getElementById('labelModeloActivo');
    const badgeTokens = document.getElementById('labelThinkingTokensBadge');
    const indicador = document.getElementById('indicadorPulsoModelo');

    if (labelModelo) {
      labelModelo.textContent = data.display_name || data.configured_model || 'Gemini 3.8 Flash (High)';
    }

    if (indicador) {
      indicador.className = `w-1.5 h-1.5 rounded-full ${data.cli_available ? 'bg-emerald-400' : 'bg-amber-400'}`;
    }

    if (badgeTokens) {
      if (data.last_telemetry && data.last_telemetry.thinking_tokens) {
        const tokens = data.last_telemetry.thinking_tokens;
        const formatted = tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'k' : tokens;
        badgeTokens.textContent = `${formatted} tokens`;
      } else if (data.effort) {
        badgeTokens.textContent = data.effort;
      }
    }
  } catch (err) {
    console.error('Error actualizando telemetría del modelo:', err);
  }
}

async function cambiarModelo(nuevoModelo, nuevoEffort = null) {
  if (!nuevoModelo) return;
  try {
    const body = { model: nuevoModelo };
    if (nuevoEffort) {
      body.effort = nuevoEffort;
    }
    const res = await fetch('/api/model-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      const err = await res.json();
      alert('No se pudo cambiar de modelo: ' + (err.detail || 'Error desconocido'));
      actualizarTelemetriaModelo();
      return;
    }
    await actualizarTelemetriaModelo();
    // Refrescar datos en el desplegable si está visible
    const dropdown = document.getElementById('dropdownTelemetria');
    if (dropdown && !dropdown.classList.contains('hidden')) {
      poblarDatosTelemetria();
    }
  } catch (err) {
    console.error('Error enviando cambio de modelo:', err);
  }
}

// Menú «Ajustes» (engranaje de la barra superior): el panel conserva el id #dropdownTelemetria
function mostrarDropdownTelemetria() {
  const dropdown = document.getElementById('dropdownTelemetria');
  if (!dropdown) return;
  dropdown.classList.remove('hidden');
  const boton = document.getElementById('btnVerTelemetria');
  if (boton) boton.setAttribute('aria-expanded', 'true');
  poblarDatosTelemetria();
}

function ocultarDropdownTelemetria() {
  const dropdown = document.getElementById('dropdownTelemetria');
  if (!dropdown) return;
  dropdown.classList.add('hidden');
  const boton = document.getElementById('btnVerTelemetria');
  if (boton) boton.setAttribute('aria-expanded', 'false');
}

function toggleAjustesMenu() {
  const dropdown = document.getElementById('dropdownTelemetria');
  if (!dropdown) return;
  if (dropdown.classList.contains('hidden')) mostrarDropdownTelemetria();
  else ocultarDropdownTelemetria();
}

/** Escape cierra el menú «Ajustes» y devuelve el foco al engranaje. */
function manejarTeclaAjustes(e) {
  if (!e || e.key !== 'Escape') return;
  const dropdown = document.getElementById('dropdownTelemetria');
  if (!dropdown || dropdown.classList.contains('hidden')) return;
  ocultarDropdownTelemetria();
  const boton = document.getElementById('btnVerTelemetria');
  if (boton && typeof boton.focus === 'function') boton.focus();
}

/** Escape cierra el modal «Nueva oferta», como el botón Cerrar. */
function cerrarModalesConEscape(e) {
  if (!e || e.key !== 'Escape') return;
  const nueva = document.getElementById('modalNueva');
  if (nueva && !nueva.classList.contains('hidden')) cerrarModalNueva();
}

// Compatibilidad hacia atrás para llamadas y tests existentes
function abrirModalTelemetria() {
  mostrarDropdownTelemetria();
}

function cerrarModalTelemetria() {
  ocultarDropdownTelemetria();
}

function poblarDatosTelemetria() {
  if (!telemetriaData) return;

  const modeloNom = document.getElementById('telemetriaModeloNombre');
  if (modeloNom) modeloNom.textContent = telemetriaData.configured_model || 'gemini-3.8-flash-high';

  const effortEl = document.getElementById('telemetriaEffort');
  if (effortEl) effortEl.textContent = telemetriaData.effort || 'high';

  const cliEstado = document.getElementById('telemetriaCliEstado');
  if (cliEstado) {
    cliEstado.textContent = telemetriaData.cli_available ? 'Conectado' : 'No detectado (modo alternativo)';
    cliEstado.className = `font-mono font-medium ${telemetriaData.cli_available ? 'text-emerald-400' : 'text-amber-400'}`;
  }

  const cliRuta = document.getElementById('telemetriaCliRuta');
  if (cliRuta) cliRuta.textContent = telemetriaData.cli_path || 'No disponible';

  const last = telemetriaData.last_telemetry || (ofertaActual && ofertaActual.telemetry ? ofertaActual.telemetry : null);
  if (last) {
    const tt = document.getElementById('telemetriaThinkingTokens');
    if (tt) tt.textContent = (last.thinking_tokens || 0).toLocaleString();
    const tot = document.getElementById('telemetriaTotalTokens');
    if (tot) tot.textContent = (last.total_tokens || 0).toLocaleString();
    const dur = document.getElementById('telemetriaDuracion');
    if (dur) dur.textContent = `${(last.duration_seconds || 0).toFixed(2)} s`;
    const cid = document.getElementById('telemetriaConvoId');
    if (cid) cid.textContent = last.conversation_id || 'Sesión en esta PC';
  }

  // Renderizar selector de modelos reales disponibles desde AGY CLI
  const modelosContainer = document.getElementById('telemetriaModelosLista');
  if (modelosContainer) {
    modelosContainer.textContent = '';
    const list = telemetriaData.models_details || (telemetriaData.available_models || []).map(m => ({ id: m, display_name: m, effort: 'high' }));
    list.forEach((m) => {
      const isCurrent = m.id === telemetriaData.configured_model;
      const card = document.createElement('button');
      card.type = 'button';
      card.setAttribute('aria-pressed', isCurrent ? 'true' : 'false');
      card.className = `w-full text-left p-1.5 rounded-lg border text-xs flex items-center justify-between cursor-pointer transition-colors ${
        isCurrent
          ? 'bg-[#1e2026] border-zinc-500 text-zinc-100 shadow-sm'
          : 'bg-[#121418] border-[#262930] hover:border-zinc-600 hover:bg-[#1a1d23] text-zinc-300'
      }`;
      card.onclick = (e) => {
        e.stopPropagation();
        cambiarModelo(m.id, m.effort);
      };
      const izquierda = document.createElement('div');
      izquierda.className = 'flex items-center gap-2 min-w-0';
      const punto = document.createElement('span');
      punto.className = `w-1.5 h-1.5 rounded-full ${isCurrent ? 'bg-emerald-400' : 'bg-zinc-600'} flex-shrink-0`;
      const textos = document.createElement('div');
      textos.className = 'truncate';
      const nombre = document.createElement('div');
      nombre.className = `font-medium truncate ${isCurrent ? 'text-zinc-100 font-semibold' : 'text-zinc-300'}`;
      nombre.textContent = m.display_name || m.id;
      const idModelo = document.createElement('div');
      idModelo.className = 'text-xs font-mono text-zinc-400 truncate';
      idModelo.textContent = m.id;
      textos.appendChild(nombre);
      textos.appendChild(idModelo);
      izquierda.appendChild(punto);
      izquierda.appendChild(textos);
      const derecha = document.createElement('div');
      derecha.className = 'flex items-center gap-1.5 flex-shrink-0';
      const nivel = document.createElement('span');
      nivel.className = 'text-xs font-mono px-1.5 py-0.5 rounded bg-[#16181d] text-zinc-400 border border-[#262930]';
      nivel.textContent = m.effort || 'high';
      derecha.appendChild(nivel);
      if (isCurrent) {
        const activo = document.createElement('span');
        activo.className = 'text-xs font-medium text-emerald-400 ml-1';
        activo.textContent = '✓ Activo';
        derecha.appendChild(activo);
      }
      card.appendChild(izquierda);
      card.appendChild(derecha);
      modelosContainer.appendChild(card);
    });
  }
}

// Control y Operaciones de Ciclo de Vida y Pipeline de Seguimiento (Career Ops)
function toggleDetalleSeguimiento() {
  const panel = document.getElementById('seccionSeguimientoDetalle');
  const icon = document.getElementById('iconToggleSeguimiento');
  if (!panel) return;
  isDetalleSeguimientoOpen = !isDetalleSeguimientoOpen;
  if (isDetalleSeguimientoOpen) {
    panel.classList.remove('hidden');
    if (icon) icon.className = 'fa-solid fa-chevron-up text-xs text-zinc-400';
  } else {
    panel.classList.add('hidden');
    if (icon) icon.className = 'fa-solid fa-chevron-down text-xs text-zinc-400';
  }
}

async function cambiarEstadoLifecycle(nuevoEstado) {
  if (!ofertaActual) return;
  try {
    const payload = {
      estado_postulacion: nuevoEstado,
      fecha_postulacion: ofertaActual.fecha_postulacion || (nuevoEstado === 'POSTULADA' ? new Date().toISOString().replace('T', ' ').slice(0, 19) : null),
      link_postulacion: ofertaActual.link_postulacion || '',
      notas_seguimiento: ofertaActual.notas_seguimiento || '',
      contacto_recruiter: ofertaActual.contacto_recruiter || ''
    };
    const res = await fetch(`/api/ofertas/${ofertaActual.id}/lifecycle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      const updated = await res.json();
      ofertaActual = updated;
      const idx = listaOfertasMemoria.findIndex(o => o.id === updated.id);
      if (idx !== -1) listaOfertasMemoria[idx] = updated;
      renderOferta(updated);
      renderizarListaOfertas(null, updated.id);
    }
  } catch (err) {
    console.error('Error al actualizar estado de ciclo de vida:', err);
  }
}

async function guardarLifecycleActual() {
  if (!ofertaActual) return;
  const selEstado = document.getElementById('selectEstadoPostulacion');
  const inputLink = document.getElementById('inputLinkPostulacion');
  const inputContacto = document.getElementById('inputContactoRecruiter');
  const inputNotas = document.getElementById('inputNotasSeguimiento');
  const lblGuardado = document.getElementById('labelGuardadoLifecycle');

  const estado = selEstado ? selEstado.value : (ofertaActual.estado_postulacion || 'POR_POSTULAR');
  const link = inputLink ? inputLink.value.trim() : '';
  const contacto = inputContacto ? inputContacto.value.trim() : '';
  const notas = inputNotas ? inputNotas.value.trim() : '';

  try {
    const payload = {
      estado_postulacion: estado,
      fecha_postulacion: ofertaActual.fecha_postulacion || (estado === 'POSTULADA' ? new Date().toISOString().replace('T', ' ').slice(0, 19) : null),
      link_postulacion: link,
      notas_seguimiento: notas,
      contacto_recruiter: contacto
    };
    const res = await fetch(`/api/ofertas/${ofertaActual.id}/lifecycle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      const updated = await res.json();
      ofertaActual = updated;
      const idx = listaOfertasMemoria.findIndex(o => o.id === updated.id);
      if (idx !== -1) listaOfertasMemoria[idx] = updated;
      renderOferta(updated);
      renderizarListaOfertas(null, updated.id);
      if (lblGuardado) {
        lblGuardado.classList.remove('hidden');
        setTimeout(() => lblGuardado.classList.add('hidden'), 2500);
      }
    }
  } catch (err) {
    console.error('Error al guardar datos de seguimiento:', err);
  }
}

// Vinculación explícita a window para compatibilidad global
if (typeof window !== 'undefined') {
  window.toggleAjustesMenu = toggleAjustesMenu;
}
