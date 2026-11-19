/* Vista Radar LinkedIn (spec §5): ofertas en dos paneles (Feed general / Mi feed).
 *
 * Consume /api/radar/* y recibe `radar_status` / `radar_post` por SSE (los reenvía app.js a window.Radar).
 * Invariante de seguridad: este módulo nunca envía nada por su cuenta; solo "Aprobar y enviar" /
 * "Aprobar todos" (clic explícito del usuario) llaman a approve / approve-all.
 *
 * Todo texto que viene de posts o del LLM (autor, cargo, texto, motivo, asunto, cuerpo, emails…)
 * se escapa con escapeHtml (definida en app.js) o se asigna con textContent / value; nunca va crudo a innerHTML.
 */
(function () {
  'use strict';

  const API = '/api/radar';
  const MS_DEBOUNCE_EDICION = 600;
  const MS_DEBOUNCE_REFRESCO = 250;
  const MS_FALLBACK_DETENER = 30000;
  /** Tope solo para descartados e ignoradas (cada uno): todo lo demás (enviados, listos, analizados,
   *  pendientes, errores) se carga completo, sin límite, para que ningún filtro pierda ofertas viejas. */
  const LIMITE_DESCARTADOS = 500;
  const FILTROS = ['RELEVANTES', 'PENDIENTES', 'ANALIZADA', 'LISTO', 'GENERANDO', 'ENVIADO', 'DESCARTADO', 'ERROR', 'TODOS', 'IGNORADA'];
  // Estados en los que el post ya tiene (o está generando) su CV
  const CON_CV = ['GENERANDO', 'LISTO', 'ENVIANDO', 'ENVIADO'];
  const ALCANCES_FILTRO = ['TODAS', 'ARGENTINA', 'EXTERIOR_COMPATIBLE'];
  const CANALES_FILTRO = ['TODOS', 'EMAIL', 'ENLACE'];
  const ETIQUETA_ALCANCE = {
    ARGENTINA: 'Argentina',
    EXTERIOR_COMPATIBLE: 'Exterior compatible',
    EXTERIOR: 'Exterior',
    DESCONOCIDO: 'Sin ubicación',
  };

  // Modelo de estados del usuario: texto del badge y, aparte, el detalle («sub») de los pendientes.
  const ETIQUETA_ESTADO = {
    NUEVO: { texto: 'Pendiente', sub: 'sin clasificar' },
    OFERTA: { texto: 'Pendiente', sub: 'por analizar' },
    ANALIZANDO: { texto: 'Pendiente', sub: 'analizando' },
    ANALIZADA: { texto: 'Analizada', sub: '' },
    GENERANDO: { texto: 'Generando', sub: '' },
    LISTO: { texto: 'Lista para enviar', sub: '' },
    ENVIANDO: { texto: 'Enviando', sub: '' },
    ENVIADO: { texto: 'Enviada', sub: '' },
    DESCARTADO: { texto: 'Descartada', sub: '' },
    ERROR: { texto: 'Error', sub: '' },
    REVISAR: { texto: 'A revisar', sub: '' },
    IGNORADA: { texto: 'Ignorada', sub: 'publicada hace 1 mes o más' },
  };

  // Tono de cada estado: chips de componentes.css (rediseño E2); siempre van con texto, nunca solo color
  const ESTILO_ESTADO = {
    NUEVO: 'chip-info',
    OFERTA: 'chip-info',
    ANALIZANDO: 'chip-info',
    ANALIZADA: 'chip-ok',
    DESCARTADO: '',
    REVISAR: 'chip-warn',
    IGNORADA: '',
    GENERANDO: 'chip-info',
    LISTO: 'chip-ok',
    ENVIANDO: 'chip-info',
    ENVIADO: 'chip-ok',
    ERROR: 'chip-danger',
  };

  /** Ícono como texto HTML (solo nombres fijos del código, nunca datos). Es una máscara CSS (static/css/iconos.css),
   *  no un <svg><use>: en listas de miles de tarjetas el subárbol SVG por ícono sumaba ~900 ms al dibujo. */
  function svgIcono(nombre, clase) {
    return `<span class="ico icono-${nombre}${clase ? ' ' + clase : ''}" aria-hidden="true"></span>`;
  }

  // Tono del estado del Radar: clase de chip de componentes.css (rediseño E2)
  const ESTILO_PILL = {
    CORRIENDO: 'chip-ok',
    PAUSA: 'chip-info',
    ESPERANDO_LOGIN: 'chip-warn',
    ERROR: 'chip-danger',
    DETENIDO: '',
    DETENIENDO: 'chip-warn',
  };

  /** Ícono del sprite local (static/iconos.js); sin él (tests en node) no dibuja nada. */
  function icono(nombre, clase) {
    if (typeof window !== 'undefined' && typeof window.crearIcono === 'function') return window.crearIcono(nombre, clase);
    return null;
  }

  /** Ícono + texto en un botón (el texto siempre como texto). */
  function iconoYTexto(el, nombre, texto) {
    const ico = icono(nombre);
    if (ico && typeof el.replaceChildren === 'function') el.replaceChildren(ico, document.createTextNode(texto));
    else el.textContent = texto;
  }

  // ------------------------------------------------------------------ utilidades puras

  /** Solo https://www.linkedin.com/…: el href sale de datos de un post (no confiables), así que se
   *  rechaza cualquier otro esquema (`javascript:`, `data:`, `http:`) o dominio. */
  /** Link a la publicación: https en www.linkedin.com o el corto lnkd.in. Otro → "". */
  function urlPublicacion(u) {
    try {
      const url = new URL(String(u || '').trim());
      return url.protocol === 'https:' && (url.hostname === 'www.linkedin.com' || url.hostname === 'lnkd.in') ? url.href : '';
    } catch (_) {
      return '';
    }
  }

  function urlSegura(u) {
    if (typeof u !== 'string') return '';
    const t = u.trim();
    try {
      const url = new URL(t);
      return url.protocol === 'https:' && url.hostname === 'www.linkedin.com' ? t : '';
    } catch (_) {
      return '';
    }
  }

  // Portales (14/11): ofertas de Jooble e Indeed. Tienen su vista; no se ven en la de LinkedIn ni en la manual
  const PORTALES = { jooble: 'Jooble', indeed: 'Indeed', computrabajo: 'Computrabajo', bumeran: 'Bumeran' };
  const PORTALES_FILTRO = ['TODOS', ...Object.keys(PORTALES)];
  const esDePortal = (p) => !!p && Object.prototype.hasOwnProperty.call(PORTALES, p.fuente);

  /** La vista a la que pertenece un post: manual, portales o radar (LinkedIn). */
  function modoDePost(p) {
    if (p.fuente === 'manual') return 'manual';
    return esDePortal(p) ? 'portales' : 'radar';
  }

  /** Filtro de portal (vista Portales): TODOS o el de la oferta. */
  function coincidePortal(post, portal) {
    return portal === 'TODOS' || !portal || post.fuente === portal;
  }

  /** «Postular en el sitio»: una oferta de portal sin mail se postula en su página (el link directo primero).
   *  Sale de datos del portal (no confiables): solo `https://`. Sin link seguro, «». */
  function linkPostularSitio(post) {
    if (!esDePortal(post)) return '';
    const conMail = String(post.email_to || '').includes('@') || (Array.isArray(post.emails) && post.emails.length > 0);
    if (conMail) return '';
    const candidatos = [...(Array.isArray(post.links) ? post.links : []), post.post_url];
    for (const c of candidatos) {
      if (typeof c !== 'string') continue;
      try {
        const url = new URL(c.trim());
        if (url.protocol === 'https:') return url.href;
      } catch (_) { /* link inválido: se prueba el siguiente */ }
    }
    return '';
  }

  /** Logo (sello con el nombre) del portal de la oferta, o null. */
  function logoPortal(post) {
    if (!esDePortal(post) || typeof document.createElement !== 'function') return null;
    const el = document.createElement('span');
    el.className = `portal-logo portal-logo-${post.fuente}`;
    el.dataset.portal = post.fuente;
    el.title = `Oferta de ${PORTALES[post.fuente]}`;
    el.textContent = PORTALES[post.fuente];
    return el;
  }

  /** Enlace «Postular en el sitio» (se abre en otra pestaña; nunca se envía nada), o null. */
  function botonPostularSitio(post) {
    const href = linkPostularSitio(post);
    if (!href || typeof document.createElement !== 'function') return null;
    const a = document.createElement('a');
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.dataset.postularSitio = String(post.id);
    a.className = 'btn btn-sm';
    a.textContent = 'Postular en el sitio';
    return a;
  }

  /** Chip de filtro → ¿se muestra este post? (ENVIANDO se agrupa con Enviados). RELEVANTES (default)
   *  son las ofertas: todo menos lo sin clasificar (NUEVO) y lo descartado; PENDIENTES es lo que
   *  todavía no se analizó (NUEVO, OFERTA, ANALIZANDO). */
  function coincideFiltro(post, filtro) {
    const e = post.estado;
    if (e === 'IGNORADA') return filtro === 'IGNORADA' || (filtro === 'NO_APTO' && motivosNoApto(post).length > 0);
    switch (filtro) {
      case 'NO_APTO': return motivosNoApto(post).length > 0;
      case 'TODOS': return true;
      case 'RELEVANTES': return e !== 'NUEVO' && e !== 'DESCARTADO';
      case 'PENDIENTES': return e === 'NUEVO' || e === 'OFERTA' || e === 'ANALIZANDO';
      case 'ENVIADO': return e === 'ENVIADO' || e === 'ENVIANDO';
      default: return e === filtro;
    }
  }

  /** Filtro de ubicación: TODAS muestra todo (también los aún sin triage). */
  function coincideAlcance(post, alcance) {
    return alcance === 'TODAS' || (post.alcance || '') === alcance;
  }

  /** Filtro de canal: EMAIL = sale por Gmail (email con algún destinatario); ENLACE = todo lo demás. */
  function coincideCanal(post, canal) {
    if (canal === 'TODOS' || !canal) return true;
    const conEmail = esEmail(post)
      && (String(post.email_to || '').trim() !== '' || (Array.isArray(post.emails) && post.emails.length > 0));
    return canal === 'EMAIL' ? conEmail : !conEmail;
  }

  function etiquetaAlcance(alcance) {
    return ETIQUETA_ALCANCE[alcance] || '';
  }

  const ICONO_CANAL = { email: '✉️', link: '🔗', whatsapp: '💬', dm: '💼', otro: '❔' };
  const NOMBRE_CANAL = { email: 'Mail', link: 'Link', whatsapp: 'WhatsApp', dm: 'Mensaje directo', otro: 'Otro' };

  /** Estado del post -> { texto, sub }. Un ENVIADO con `message_id` «manual:...» es una postulación que
   *  el usuario hizo por su cuenta («Postulada»). Estado desconocido: se muestra tal cual. */
  function etiquetaEstado(post) {
    const estado = post && post.estado;
    if (estado === 'ENVIADO' && String(post.message_id || '').startsWith('manual:')) {
      return { texto: 'Postulada', sub: '' };
    }
    // Un CV solo está «Generando» mientras el worker lo procesa (tiene progreso); antes, espera en la cola.
    const conProgreso = (S.progreso && S.progreso.has(post.id)) || !!(post.progreso && typeof post.progreso === 'object' && Object.keys(post.progreso).length);
    if (estado === 'GENERANDO' && !conProgreso) {
      return { texto: 'En cola', sub: 'Espera su turno: se genera un CV por vez' };
    }
    // Colas dinámicas (08/11): la posición se recalcula en cada consulta; una oferta mejor pasa adelante.
    if (estado === 'ANALIZADA' && post.cola && post.cola.posicion) {
      const prio = post.cola.nivel === 'prioritaria';
      return {
        texto: (prio ? 'Prioritaria, ' : 'De fondo, ') + post.cola.posicion + '.º',
        sub: prio ? 'Hasta 14 días' : 'Después de la prioritaria',
      };
    }
    if (estado === 'IGNORADA' && motivosNoApto(post).length) return { texto: 'Ignorada', sub: 'No apto' };
    // Por qué está descartada (15/11): una frase corta en la tarjeta
    if (estado === 'DESCARTADO' && post.descarte && post.descarte.corto) return { texto: 'Descartada', sub: post.descarte.corto };
    return propio(ETIQUETA_ESTADO, estado, { texto: estado, sub: '' });
  }

  /** Busca `clave` solo entre las claves propias de la tabla: "constructor" / "toString" (que vienen
   *  de un post no confiable) no deben resolver a propiedades heredadas de Object. */
  function propio(tabla, clave, defecto) {
    return typeof clave === 'string' && Object.prototype.hasOwnProperty.call(tabla, clave) ? tabla[clave] : defecto;
  }

  /** Ícono del canal de postulación; sin canal (posts viejos) es email. */
  function iconoCanal(canal) {
    if (!canal) return ICONO_CANAL.email;
    return propio(ICONO_CANAL, canal, ICONO_CANAL.otro);
  }

  /** Nombre legible del canal; sin canal es email, un canal desconocido es "Otro". */
  function nombreCanal(canal) {
    if (!canal) return NOMBRE_CANAL.email;
    return propio(NOMBRE_CANAL, canal, NOMBRE_CANAL.otro);
  }

  /** Posts viejos (canal vacío) son de email. Solo el canal email se envía por Gmail. */
  function esEmail(post) {
    return !post || !post.canal || post.canal === 'email';
  }

  /** Dónde se abre la postulación de un canal no-email. Sale de datos de un post (no confiables):
   *  link → solo `https://` (cualquier dominio); whatsapp → wa.me con solo los dígitos del número y el
   *  mensaje codificado; dm → el perfil de LinkedIn (urlSegura). Cualquier otro caso → "". */
  function urlDestino(post, body) {
    if (!post) return '';
    const destino = String(post.destino_canal || '').trim();
    switch (post.canal) {
      case 'link':
        try {
          return /^https:\/\//i.test(destino) && new URL(destino).protocol === 'https:' ? destino : '';
        } catch (_) {
          return '';
        }
      case 'whatsapp': {
        const digitos = destino.replace(/\D/g, '');
        if (!digitos) return '';
        try {
          return `https://wa.me/${digitos}?text=${encodeURIComponent(String(body || ''))}`;
        } catch (_) {
          // Surrogate suelto en el mensaje (URIError): sin URL segura, no se abre nada
          return '';
        }
      }
      case 'dm':
        return urlSegura(post.url);
      default:
        return '';
    }
  }

  /** "YYYY-MM-DD HH:MM:SS" (hora local del servidor) → Date, o null. */
  function parseFecha(str) {
    if (!str) return null;
    const d = new Date(String(str).trim().replace(' ', 'T'));
    return isNaN(d.getTime()) ? null : d;
  }

  const dos = (n) => String(n).padStart(2, '0');

  function formatearCuenta(ms) {
    const total = Math.max(0, Math.round(ms / 1000));
    return `${Math.floor(total / 60)}:${dos(total % 60)}`;
  }

  function formatearFecha(str) {
    const d = parseFecha(str);
    if (!d) return str ? String(str).slice(0, 16) : '';
    return `${dos(d.getDate())}/${dos(d.getMonth() + 1)} ${dos(d.getHours())}:${dos(d.getMinutes())}`;
  }

  /** Texto y estilo del pill de estado. `deteniendo`: stop pedido, falta el SSE que confirma DETENIDO. */
  function describirPill(st, deteniendo) {
    if (deteniendo) return { texto: 'Deteniendo…', clase: ESTILO_PILL.DETENIENDO };
    const estado = (st && st.estado) || 'DETENIDO';
    const clase = ESTILO_PILL[estado] || ESTILO_PILL.DETENIDO;
    switch (estado) {
      case 'CORRIENDO':
        return { texto: 'En ejecución', clase };
      case 'PAUSA': {
        const proximo = parseFecha(st.next_cycle_at);
        if (!proximo) return { texto: 'En pausa', clase };
        const restante = proximo.getTime() - Date.now();
        let texto = `En pausa · próxima vuelta ${dos(proximo.getHours())}:${dos(proximo.getMinutes())}`;
        if (restante > 0) texto += ` · ${formatearCuenta(restante)}`;
        return { texto, clase };
      }
      case 'ESPERANDO_LOGIN':
        return { texto: 'Esperando que inicies sesión', clase };
      case 'ERROR': {
        const detalle = (st && st.estado_detalle) ? String(st.estado_detalle) : '';
        const esRed = ['ERR_INTERNET_DISCONNECTED', 'ERR_NAME_NOT_RESOLVED', 'ERR_CONNECTION', 'net::'].some((k) => detalle.includes(k));
        const proximo = parseFecha(st && st.next_cycle_at);
        const hora = proximo ? `${dos(proximo.getHours())}:${dos(proximo.getMinutes())}` : '';
        const reintento = hora ? `Reintenta a las ${hora}` : 'Reintenta en el próximo ciclo';
        const texto = esRed ? `Sin conexión a internet. ${reintento}` : `Error del Radar. ${reintento}`;
        return { texto, clase, title: detalle };
      }
      default:
        return { texto: 'Detenido', clase };
    }
  }

  /** Qué acciones ofrece cada estado del post. */
  function accionesPara(post) {
    const e = post.estado;
    const aprobado = !!post.aprobado_at;
    const listo = e === 'LISTO';
    const cvEnCurso = post.postulacion && ['PENDING', 'PROCESSING'].includes(post.postulacion.status);
    // Las ignoradas por antigüedad no tienen acciones; las No apto permiten volver.
    if (e === 'IGNORADA') {
      return { aprobar: false, descartar: false, regenerar: false, analizar: false, generarCv: false,
        reintentar: false, editor: false, editable: false,
        ...(puedePostularIgual(post) ? { postularIgual: true, descartar: true } : {}) };
    }
    return {
      ...(puedePostularIgual(post) ? { postularIgual: true } : {}),
      aprobar: listo && !aprobado && esEmail(post),
      descartar: e !== 'ENVIANDO' && e !== 'ENVIADO',
      regenerar: listo || (e === 'ERROR' && !!post.postulacion_id),
      // Analizar (o darle prioridad) y Generar CV: mientras no tenga CV; no durante un análisis en curso
      analizar: !CON_CV.includes(e) && e !== 'ANALIZANDO',
      generarCv: !CON_CV.includes(e) && e !== 'ANALIZANDO' && !puedePostularIgual(post),
      reintentar: e === 'ERROR' && !!post.postulacion_id,
      // Mientras se (re)genera, la postulación vinculada está PENDING: no hay nada para editar todavía
      editor: !!post.postulacion_id && e !== 'GENERANDO' && e !== 'NUEVO' && !cvEnCurso,
      editable: listo && !aprobado,
    };
  }

  /** Acciones propias de los canales sin email (nunca se envía nada solo: se copia, se abre y se marca).
   *  «Ya me postulé» (`marcarPostulado`) vale para todo post que no sale por Gmail: otro canal o email sin destinatario. */
  function accionesCanal(post) {
    const otro = !esEmail(post);
    const sinDestinatario = !otro && !String(post.email_to || '').trim();
    return {
      panel: otro,
      copiar: otro,
      marcarPostulado: (otro || sinDestinatario) && ['ANALIZADA', 'LISTO', 'ERROR'].includes(post.estado),
    };
  }

  /** ¿El CV (PDF) de la postulación vinculada ya se puede descargar? */
  function cvDisponible(post) {
    const pid = Number(post.postulacion_id);
    const cvListo = post.postulacion && (post.postulacion.status === 'READY' || post.postulacion.cv_markdown);
    return !!(pid && post.estado !== 'GENERANDO' && cvListo);
  }

  /** ¿El error del post es un envío que pudo haber salido (interrumpido o incierto)? Reintentar
   *  puede duplicar el email: la UI pide confirmación antes. */
  function envioDudoso(error) {
    return typeof error === 'string' && /env[ií]o (interrumpido|incierto)/i.test(error);
  }

  /** `detail` de un error de FastAPI (string, objeto, lista de validación) → texto legible. */
  function mensajeDeDetalle(detail) {
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map((d) => (d && d.msg) || String(d)).join('; ');
    }
    if (detail && typeof detail === 'object') {
      if (detail.duplicate) return `Ya hay un envío reciente a ${detail.email || 'ese destinatario'}`;
      if (detail.message) return String(detail.message);
    }
    return 'No se pudo completar la acción';
  }

  // ------------------------------------------------------------------ estado

  const S = {
    modo: 'radar',
    genModo: 0,
    iniciado: false,
    estado: { estado: 'DETENIDO', desired_running: false, umbral_match: 60, contadores_dia: {}, gmail_configurado: null },
    posts: new Map(),
    filtro: 'RELEVANTES',
    alcance: 'TODAS',
    canal: 'TODOS',
    portal: 'TODOS',
    aprobar: null,   // revisión previa de «Aprobar todos» abierta: { filas, resolver, foco }
    orden: 'match',
    // Progreso de la generación del CV por post: { percent, step, t0 }
    progreso: new Map(),
    selId: null,
    detalle: null,
    revisionVista: null,
    deteniendo: false,
    tab: 'lista',
    listaTab: 'lista',  // la lista abierta (Feed general o Mi feed) aunque el paso sea Detalle o Email
    edit: null,
    tokenDetalle: 0,
    detalleFallido: null,
    colaDesde: Date.now(),
  };
  let colaGuardado = Promise.resolve();
  let timerDetener = null;
  let timerCola = null;
  const timersRefresco = new Map();

  const $ = (id) => document.getElementById(id);

  function perteneceAlModo(p) {
    if (!p) return false;
    return modoDePost(p) === S.modo;
  }

  function aplicarModo() {
    const vistaRadar = $('vista-radar');
    if (vistaRadar) {
      if (!vistaRadar.dataset) vistaRadar.dataset = {};
      vistaRadar.dataset.modo = S.modo;
      if (typeof vistaRadar.setAttribute === 'function') {
        vistaRadar.setAttribute('data-modo', S.modo);
      }
    }
    const esManual = S.modo === 'manual';
    const esPortales = S.modo === 'portales';
    // data-solo-radar: lo del Radar (LinkedIn y Portales, que corre en su hilo); data-solo-linkedin: solo LinkedIn
    const soloRadar = (document.querySelectorAll && document.querySelectorAll('[data-solo-radar]')) || [];
    soloRadar.forEach((el) => {
      el.hidden = esManual;
    });
    const soloLinkedin = (document.querySelectorAll && document.querySelectorAll('[data-solo-linkedin]')) || [];
    soloLinkedin.forEach((el) => {
      el.hidden = S.modo !== 'radar';
    });
    const soloPortales = (document.querySelectorAll && document.querySelectorAll('[data-solo-portales]')) || [];
    soloPortales.forEach((el) => {
      el.hidden = !esPortales;
    });
    const soloManual = (document.querySelectorAll && document.querySelectorAll('[data-solo-manual]')) || [];
    soloManual.forEach((el) => {
      el.hidden = !esManual;
    });
    const reintentar = $('radarReintentarAhoraBtn');
    if (reintentar) reintentar.hidden = esManual || S.estado.estado !== 'ERROR';
    const titulo = $('radarPanelBusquedaTitulo');
    if (titulo) {
      titulo.textContent = esManual ? 'Ofertas manuales' : (esPortales ? 'Ofertas de portales' : 'Búsquedas');
    }
    const tabLista = $('radarTabListaTexto');
    if (tabLista) tabLista.textContent = esManual || esPortales ? 'Ofertas' : 'Búsquedas';
    const tituloDetalle = $('radarDetalleTitulo');
    if (tituloDetalle) tituloDetalle.textContent = S.modo === 'radar' ? 'Post de LinkedIn' : 'Oferta';
  }

  function textoEstadoCv(status) {
    const textos = { READY: 'CV listo', PROCESSING: 'Generando', ERROR: 'Error', PENDING: 'Pendiente' };
    if (!status) return textos.PENDING;
    return Object.prototype.hasOwnProperty.call(textos, status) ? textos[status] : String(status);
  }

  function filaHistorial(o) {
    const el = document.createElement('div');
    el.className = 'proc-tarjeta';

    const arriba = document.createElement('div');
    arriba.className = 'proc-linea';

    const tit = document.createElement('span');
    tit.className = 'proc-titulo-fila linea-corta';
    tit.textContent = o.titulo || 'Sin título';
    arriba.appendChild(tit);

    const st = document.createElement('span');
    st.className = 'proc-pct';
    st.textContent = textoEstadoCv(o.status);
    arriba.appendChild(st);

    el.appendChild(arriba);

    const medio = document.createElement('div');
    medio.className = 'proc-linea proc-meta';

    const emp = document.createElement('span');
    emp.className = 'linea-corta';
    emp.textContent = o.empresa || '';
    medio.appendChild(emp);

    const fec = document.createElement('span');
    fec.className = 'proc-meta';
    fec.textContent = formatearFecha(o.created_at);
    medio.appendChild(fec);

    el.appendChild(medio);

    const abajo = document.createElement('div');
    abajo.className = 'proc-enlaces';

    const linkPdf = document.createElement('a');
    linkPdf.href = `/api/ofertas/${o.id}/export/pdf`;
    linkPdf.target = '_blank';
    linkPdf.rel = 'noopener noreferrer';
    linkPdf.className = 'enlace';
    linkPdf.textContent = 'PDF';
    linkPdf.onclick = (e) => descargarPdf(o.id, e);
    abajo.appendChild(linkPdf);

    const linkDocx = document.createElement('a');
    linkDocx.href = `/api/ofertas/${o.id}/export/docx`;
    linkDocx.target = '_blank';
    linkDocx.rel = 'noopener noreferrer';
    linkDocx.className = 'enlace';
    linkDocx.textContent = 'DOCX';
    abajo.appendChild(linkDocx);

    el.appendChild(abajo);
    return el;
  }

  // «Historial anterior» arranca cerrado; si el usuario lo abre (o lo cierra), se recuerda solo durante la sesión
  const CLAVE_HISTORIAL = 'linkedout.historialAnteriorAbierto';
  function historialAbiertoEnLaSesion() {
    try { return window.sessionStorage.getItem(CLAVE_HISTORIAL) === '1'; } catch (e) { return S.historialAbierto === true; }
  }
  function recordarHistorial(abierto) {
    S.historialAbierto = abierto;
    try { window.sessionStorage.setItem(CLAVE_HISTORIAL, abierto ? '1' : '0'); } catch (e) { /* sin almacenamiento: solo en memoria */ }
  }

  /** Historial bajo la lista. Manual: las postulaciones manuales («Historial anterior»). Radar: las postulaciones
   *  del Radar cuyo post ya no existe (sin esto no se ven en ninguna vista); oculto si no hay ninguna. */
  async function cargarHistorial() {
    const modo = S.modo;
    const manual = modo === 'manual';
    const detalles = $('manualHistorial');
    if (detalles && !manual) detalles.hidden = true;
    if (modo === 'portales') return;
    try {
      const res = await fetch(manual ? '/api/ofertas' : '/api/ofertas?origen=radar');
      if (!res.ok) return;
      const ofertas = await res.json();
      if (!Array.isArray(ofertas) || S.modo !== modo) return;
      const lista = $('manualHistorialLista');
      const titulo = manual ? 'Historial anterior' : 'Postulaciones sin post';
      if (detalles) {
        detalles.hidden = !manual && !ofertas.length;
        const summary = detalles.querySelector ? detalles.querySelector('summary') : null;
        if (summary) {
          const summarySpan = detalles.querySelector('#manualHistorialSummary') || (summary.querySelector && summary.querySelector('span'));
          if (summarySpan) {
            summarySpan.textContent = `${titulo} (${ofertas.length})`;
          } else {
            summary.textContent = `${titulo} (${ofertas.length})`;
          }
        }
      }
      if (lista) {
        lista.textContent = '';
        ofertas.forEach((o) => {
          lista.appendChild(filaHistorial(o));
        });
      }
    } catch (err) {
      console.warn('Error cargando historial de ofertas:', err);
    }
  }

  // ------------------------------------------------------------------ HTTP

  /** Toda llamada que modifica algo va con `Content-Type: application/json` y cuerpo JSON (`{}` como
   *  mínimo): así un sitio ajeno no puede imitarla con un POST "simple" sin la consulta previa de CORS (CSRF).
   *  El servidor además rechaza (403) lo que no venga de la propia PC o de un celular emparejado. */
  async function api(metodo, ruta, cuerpo) {
    // El servidor solo acepta descartar ofertas con esta cabecera (un script o un agente no la manda)
    const opts = { method: metodo, headers: { 'X-LinkedOut-Origen': 'portal' } };
    if (cuerpo === undefined && metodo !== 'GET' && metodo !== 'HEAD') cuerpo = {};
    if (cuerpo !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(cuerpo);
    }
    try {
      const res = await fetch(API + ruta, opts);
      let data = null;
      try { data = await res.json(); } catch (_) { /* respuesta sin cuerpo JSON */ }
      return { ok: res.ok, status: res.status, data, detail: data ? data.detail : undefined };
    } catch (err) {
      return { ok: false, status: 0, data: null, detail: 'No se pudo conectar con el servidor' };
    }
  }

  // ------------------------------------------------------------------ mensajes

  /** Mensaje cerrable bajo la barra de control. Todo va con textContent. */
  const MS_MENSAJE_OK = 6000;
  const MS_MENSAJE_DESHACER = 10000;
  let timerMensaje = null;
  let idMensaje = 0;

  /** `accion` opcional: { texto, alClick } agrega un botón (un solo uso) y alarga el mensaje a 10 s.
   *  `opciones` opcional: { filas: [elementos con acciones], fijo: true } deja el mensaje abierto hasta
   *  que se cierre con su botón. Devuelve el id del mensaje mostrado (para saber si sigue en pantalla). */
  function mostrarMensaje(texto, tipo, lineas, accion, opciones) {
    const caja = $('radarMensaje');
    const cont = $('radarMensajeTexto');
    if (!caja || !cont) return 0;
    const extra = opciones || {};
    clearTimeout(timerMensaje);
    idMensaje += 1;
    const miId = idMensaje;
    if (tipo === 'ok' && !extra.fijo) timerMensaje = setTimeout(ocultarMensaje, accion ? MS_MENSAJE_DESHACER : MS_MENSAJE_OK);
    cont.textContent = '';
    const principal = document.createElement('div');
    principal.className = 'mensaje-principal';
    principal.textContent = texto;
    if (accion) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'enlace-accion';
      btn.textContent = accion.texto;
      btn.addEventListener('click', () => {
        btn.disabled = true;
        accion.alClick();
      });
      principal.appendChild(btn);
    }
    cont.appendChild(principal);
    (lineas || []).forEach((l) => {
      const li = document.createElement('div');
      li.className = 'mensaje-linea';
      li.textContent = l;
      cont.appendChild(li);
    });
    (extra.filas || []).forEach((f) => cont.appendChild(f));
    caja.className = 'mensaje';
    if (caja.dataset) caja.dataset.tipo = tipo === 'error' || tipo === 'ok' ? tipo : 'info';
    caja.hidden = false;
    return miId;
  }

  /** Cuando ya no quedan acciones pendientes, el mensaje fijo vuelve a irse solo (si sigue siendo ese). */
  function soltarMensajeFijo(id) {
    if (id !== idMensaje) return;
    clearTimeout(timerMensaje);
    timerMensaje = setTimeout(ocultarMensaje, MS_MENSAJE_OK);
  }

  function ocultarMensaje() {
    const caja = $('radarMensaje');
    if (caja) caja.hidden = true;
  }

  // Oferta cargada a mano con el análisis o la generación en pausa: la pausa se respeta (decisión del 19/11) y se
  // avisa, con un botón a Procesos, donde se reanuda. La oferta queda en «Por analizar».
  function textoAvisoPausa(analisis, generacion) {
    if (analisis && generacion) return 'El análisis y la generación de CV y mail están en pausa: reanudalos en Procesos';
    if (analisis) return 'El análisis está en pausa: reanudalo en Procesos';
    if (generacion) return 'La generación de CV y mail está en pausa: reanudala en Procesos';
    return '';
  }

  async function avisarPausaAlCargar() {
    await cargarEstado();
    const texto = textoAvisoPausa(!!S.estado.analisis_pausado, !!S.estado.generacion_pausada);
    if (!texto) return false;
    mostrarMensaje(texto, 'info', null, { texto: 'Ir a Procesos', alClick: abrirProcesos }, { fijo: true });
    return true;
  }

  function mostrarAvisoGmail(texto) {
    S.estado.gmail_configurado = false;
    const msg = $('radarGmailAvisoMsg');
    if (msg && texto) msg.textContent = texto;
    renderAvisos();
  }

  // ------------------------------------------------------------------ barra de control

  /** Posts LISTO sin aprobar, tal como los ve el usuario (lo que "Aprobar todos" le pide confirmar). */
  function listosSinAprobar() {
    const out = [];
    // Solo canal email: para los demás, tocá «Ya me postulé» uno a uno.
    S.posts.forEach((p) => { if (p.estado === 'LISTO' && !p.aprobado_at && esEmail(p)) out.push(p); });
    return out;
  }

  function contarListos() {
    return listosSinAprobar().length;
  }

  function renderControl() {
    const st = S.estado;
    const btn = $('radarToggleBtn');
    if (btn) {
      const corriendo = !!st.desired_running;
      btn.textContent = S.deteniendo ? 'Deteniendo…' : (corriendo ? 'Detener' : 'Iniciar');
      btn.disabled = S.deteniendo;
      btn.className = 'btn ' + (corriendo ? 'btn-detener' : 'btn-ok');
    }
    renderPill();
    const btnReintentar = $('radarReintentarAhoraBtn');
    if (btnReintentar) btnReintentar.hidden = S.modo !== 'radar' || st.estado !== 'ERROR';
    const nav = $('radarMostrarNavegadorBtn');
    if (nav) nav.hidden = S.modo !== 'radar' || st.estado !== 'ESPERANDO_LOGIN';
    const umbral = $('radarUmbralInput');
    if (umbral && document.activeElement !== umbral && st.umbral_match !== undefined && st.umbral_match !== null) {
      umbral.value = st.umbral_match;
    }
    const serie = $('radarModoSerie');
    if (serie) serie.checked = !!st.modo_serie;
    renderAvisos();
    renderCola();
    renderAprobarTodos();
    renderRevisionBtn();
  }

  // ------------------------------------------------------------------ cola de trabajo

  /** «Generando 13 · ahora: Analista (CV) · termina ~15:40 · 99 por analizar»; '' si no hay nada en cola.
   *  `desdeMs`: momento en que el servidor calculó la estimación (la hora de fin se cuenta desde ahí). */
  function textoCola(cola, etapaVista, desdeMs) {
    if (!cola || typeof cola !== 'object') return '';
    const generando = Number(cola.generando) || 0;
    const analizando = Number(cola.analizando) || 0;
    const partes = [];
    if (generando > 0) {
      const act = cola.actual;
      if (act) {
        partes.push(`Generando ${generando}`);
        partes.push('ahora: ' + (act.puesto || 'una oferta') + ' (' + (etapaVista || act.etapa || 'CV') + ')');
        if (Number.isFinite(Number(cola.eta_s)) && cola.eta_s !== null) {
          const fin = new Date(desdeMs + Number(cola.eta_s) * 1000);
          const dos = (n) => String(n).padStart(2, '0');
          partes.push(`termina ~${dos(fin.getHours())}:${dos(fin.getMinutes())}`);
        }
      } else {
        partes.push(`Generando ${generando} en cola`);
      }
    }
    if (analizando > 0) partes.push(`${analizando} por analizar`);
    return partes.join(' · ');
  }

  /** Un post cambió de estado: la cola del servidor cambió (el aviso espera un poco para juntar varios cambios). */
  function refrescarCola() {
    clearTimeout(timerCola);
    timerCola = setTimeout(() => { cargarEstado(); }, MS_DEBOUNCE_REFRESCO);
  }

  // Interruptores del Radar (12/11): [id, ruta de la API, campo del estado, nombre]
  const INTERRUPTORES = [
    ['radarFeedSwitch', '/feed', 'feed_activo', 'el feed'],
    ['radarBusquedasSwitch', '/busquedas', 'busquedas_activas', 'las búsquedas'],
  ];

  /** Radar detenido: los dos apagados, grisados y deshabilitados. En marcha: el estado guardado. */
  function renderInterruptores() {
    const enMarcha = !!S.estado.desired_running && S.estado.estado !== 'DETENIDO';
    const motivo = 'El Radar está detenido: encendelo para cambiar el feed y las búsquedas';
    for (const [id, , campo, nombre] of INTERRUPTORES) {
      const sw = $(id);
      if (!sw) continue;
      const on = enMarcha && !!S.estado[campo];
      if (sw.setAttribute) sw.setAttribute('aria-checked', on ? 'true' : 'false');
      sw.disabled = !enMarcha;
      sw.title = !enMarcha ? motivo : `${nombre[0].toUpperCase()}${nombre.slice(1)}: ${on ? 'encendido, tocá para apagar' : 'apagado, tocá para encender'}`;
    }
    const caja = $('radarInterruptores');
    if (caja) caja.title = enMarcha ? '' : motivo;
    const est = $('radarInterruptoresEstado');
    if (est) {
      const feed = !!S.estado.feed_activo, busq = !!S.estado.busquedas_activas;
      est.textContent = !enMarcha ? 'detenido'
        : feed && busq ? 'feed y búsquedas' : feed ? 'solo feed' : busq ? 'solo búsquedas' : 'sin lecturas';
      if (est.dataset) est.dataset.estado = !enMarcha ? '' : (feed || busq) ? 'activo' : 'pausa';
    }
    const ayuda = $('radarInterruptoresAyuda');
    if (ayuda) ayuda.textContent = enMarcha ? 'Qué lee en LinkedIn en cada ciclo' : 'Encendé el Radar para cambiarlos';
  }

  async function alternarInterruptor(id) {
    const def = INTERRUPTORES.find(i => i[0] === id);
    const sw = $(id);
    if (!def || !sw) return;
    const [, ruta, campo] = def;
    const activo = !S.estado[campo];
    // La cuenta primero: encender las búsquedas en LinkedIn pide confirmación
    if (campo === 'busquedas_activas' && activo &&
        !window.confirm('¿Encender las búsquedas en LinkedIn? Empiezan en la próxima lectura, dentro del presupuesto.')) return;
    sw.disabled = true;
    try {
      const r = await api('POST', ruta, { activo });
      if (r.ok && r.data) onStatus(r.data);
    } finally {
      renderInterruptores();
    }
  }

  function renderCola() {
    renderInterruptores();
    const pausada = !!S.estado.generacion_pausada;
    const analisisPausado = !!S.estado.analisis_pausado;
    // Panel de procesos (08/11): cada fila dice qué controla y en qué estado está; un solo botón por fila
    const btn = $('radarGeneracionBtn');
    if (btn) {
      btn.textContent = pausada ? 'Reanudar' : 'Pausar';
      if (btn.dataset) btn.dataset.accion = pausada ? 'reanudar' : 'pausar';
    }
    const estGen = $('radarGeneracionEstado');
    if (estGen) {
      estGen.textContent = pausada ? 'en pausa' : 'generando CV y mail';
      if (estGen.dataset) estGen.dataset.estado = pausada ? 'pausa' : 'activo';
    }
    const btnAna = $('radarAnalisisBtn');
    if (btnAna) {
      btnAna.textContent = analisisPausado ? 'Reanudar' : 'Pausar';
      if (btnAna.dataset) btnAna.dataset.accion = analisisPausado ? 'reanudar' : 'pausar';
    }
    const estAna = $('radarAnalisisEstado');
    if (estAna) {
      estAna.textContent = analisisPausado ? 'en pausa' : 'activo';
      if (estAna.dataset) estAna.dataset.estado = analisisPausado ? 'pausa' : 'activo';
    }
    const franja = $('radarCola');
    if (!franja) return;
    const cola = S.estado.cola;
    const prog = cola && cola.actual ? S.progreso.get(cola.actual.post_id) : null;
    const texto = textoCola(cola, prog && prog.step, S.colaDesde);
    franja.hidden = false;
    let contenido = pausada ? 'Generación de CV en pausa' : (texto || 'Nada en curso · Ver procesos');
    if (analisisPausado) contenido += ' · análisis en pausa';
    franja.textContent = contenido;
  }

  // ------------------------------------------------------------------ historial de procesos

  function crearControlSerie() {
    if ($('radarModoSerie')) return;
    const lista = $('radarProcesosLista');
    if (!lista || !lista.parentNode) return;
    const label = document.createElement('label');
    label.setAttribute('for', 'radarModoSerie');
    label.className = 'proc-serie';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.id = 'radarModoSerie';
    label.appendChild(input);
    const texto = document.createElement('span');
    texto.textContent = 'Modo serie: una llamada a la IA por vez (más lento; solo si la IA está saturada. Se activa solo ante un error 503)';
    label.appendChild(texto);
    lista.parentNode.insertBefore(label, lista.nextSibling || null);
  }

  async function guardarModoSerie() {
    const input = $('radarModoSerie');
    if (!input) return;
    input.disabled = true;
    try {
      const r = await api('PATCH', '/config', { modo_serie: input.checked });
      if (r.ok && r.data) {
        onStatus(r.data);
      } else {
        mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
        await cargarEstado();
        renderControl();
      }
      await cargarProcesos();
    } finally {
      input.disabled = false;
    }
  }

  function renderHistorialAGY(lista, agy) {
    if (!agy || typeof agy !== 'object') return 0;
    S.estado.modo_serie = !!agy.modo_serie;
    const input = $('radarModoSerie');
    if (input) input.checked = S.estado.modo_serie;
    let cantidad = 0;
    if (agy.serie_hasta !== null && agy.serie_hasta !== undefined) {
      const aviso = document.createElement('div');
      aviso.className = 'banner banner-warn';
      aviso.textContent = 'IA saturada: una llamada por vez hasta las ' +
        new Date(Number(agy.serie_hasta) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      lista.appendChild(aviso);
      cantidad++;
    }
    return cantidad;
  }

  const PASOS_CV = [
    ['preparar', 'Preparando la postulación'],
    ['analizar', 'Analizando la oferta'],
    ['redactar', 'Redactando el CV'],
    ['auditar', 'Revisando y corrigiendo el CV'],
    ['completar', 'Completando puesto, CV, presentación y canales'],
    ['mensaje', 'Redactando el mensaje'],
    ['completar_mensaje', 'Revisando el mensaje'],
  ];

  const ETIQUETAS_CV = new Map(PASOS_CV);

  function etiquetaPaso(progreso) {
    // La etapa corregida también se traduce en historiales guardados con la etiqueta vieja.
    if (progreso.paso === 'completar') return ETIQUETAS_CV.get('completar');
    return progreso.etiqueta || progreso.step || ETIQUETAS_CV.get(progreso.paso) || '';
  }

  function lineaPasos(progreso) {
    if (!progreso || typeof progreso !== 'object') return [];
    const resultado = [];
    const pasosEjecutados = Array.isArray(progreso.pasos) ? progreso.pasos : [];
    const pasoActual = progreso.paso;
    const terminado = Boolean(progreso.fin);

    for (const p of pasosEjecutados) {
      if (!p || typeof p !== 'object') continue;
      const paso = p.paso;
      const etiqueta = etiquetaPaso(p) || paso;
      const esActual = !terminado && (paso === pasoActual || p.fin === null);
      resultado.push({
        paso,
        etiqueta,
        estado: esActual ? 'actual' : 'hecho',
        duracion_s: p.duracion_s !== undefined && p.duracion_s !== null ? Number(p.duracion_s) : null,
      });
    }

    if (!terminado && pasoActual) {
      const idxActual = PASOS_CV.findIndex(([k]) => k === pasoActual);
      if (idxActual !== -1) {
        for (let i = idxActual + 1; i < PASOS_CV.length; i++) {
          const [paso, etiqueta] = PASOS_CV[i];
          resultado.push({
            paso,
            etiqueta,
            estado: 'pendiente',
            duracion_s: null,
          });
        }
      }
    }
    return resultado;
  }

  function textoTopes(topes) {
    if (!topes || typeof topes !== 'object') return '';
    const leidos = Number(topes.leidos_busquedas) || 0;
    // Los topes son del perfil: sin reglas válidas llegan en null y se muestra «—», nunca un número inventado
    const tope = (v) => (Number.isInteger(v) && v > 0 ? String(v) : '—');
    const leidosFeed = Number(topes.leidos_feed) || 0;
    const enviados = Number(topes.enviados) || 0;
    const topeEnvios = tope(topes.tope_envios);
    const postulados = Number(topes.postulados) || 0;
    const ofertas = Number(topes.ofertas) || 0;
    // Espacio duro dentro de cada dato: la etiqueta nunca queda separada de su número al cortar renglón
    return [`Feed ${leidosFeed}`, `Búsquedas ${leidos}`, `Enviados ${enviados}/${topeEnvios}`,
      `Postulados hoy ${postulados}`, `Ofertas ${ofertas}`].map((t) => t.replace(/ /g, ' ')).join(' · ');
  }

  function textoCuotaProceso(progreso) {
    if (!progreso || !progreso.esperando_cuota) return '';
    const renuevaVal = progreso.cuota_renueva || progreso.renueva_a || progreso.renueva;
    if (renuevaVal) {
      let hora = '';
      if (typeof renuevaVal === 'string' && /^\d{1,2}:\d{2}$/.test(renuevaVal.trim())) {
        hora = renuevaVal.trim();
      } else {
        try {
          const d = new Date(renuevaVal);
          if (!isNaN(d.getTime())) {
            hora = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
          }
        } catch (_) {}
      }
      if (hora) {
        return `Esperando cuota de AGY · se renueva a las ${hora}`;
      }
    }
    return 'Esperando cuota de AGY';
  }

  function filaProceso(item) {
    const row = document.createElement('div');
    row.className = 'proc-fila';
    if (item && item.post_id) {
      row.setAttribute('data-post-id', String(item.post_id));
    }

    const linea1 = document.createElement('div');
    linea1.className = 'proc-linea1';

    const puestoWrap = document.createElement('div');
    puestoWrap.className = 'proc-titulo-fila linea-corta';
    puestoWrap.textContent = (item && (item.puesto || item.cargo_autor)) || (item && item.post_id ? 'Post #' + item.post_id : '(Sin puesto)');
    linea1.appendChild(puestoWrap);

    const badgeWrap = document.createElement('div');
    badgeWrap.className = 'proc-chips';

    if (item && item.posicion !== undefined && item.posicion !== null) {
      const posSpan = document.createElement('span');
      posSpan.className = 'chip num';
      posSpan.textContent = '#' + item.posicion;
      badgeWrap.appendChild(posSpan);
    }

    if (item && item.tipo) {
      const tipoSpan = document.createElement('span');
      tipoSpan.className = 'chip ' +
        (item.tipo === 'generacion' ? 'chip-info' :
         item.tipo === 'analisis' ? '' :
         'chip-warn');
      const tiposTexto = { generacion: 'CV', analisis: 'Análisis', clasificacion: 'Clasificación' };
      tipoSpan.textContent = tiposTexto[item.tipo] || item.tipo;
      badgeWrap.appendChild(tipoSpan);
    }

    if (item && item.estado) {
      const estadoSpan = document.createElement('span');
      let textoEst = etiquetaEstado(item).texto || item.estado;
      if (item.estado === 'GENERANDO' && (!item.progreso || (typeof item.progreso === 'object' && !item.progreso.etiqueta && !item.progreso.pct && !item.progreso.inicio && !item.progreso.esperando_cuota))) {
        textoEst = 'En cola';
      }
      const estilo = (textoEst === 'En cola') ? '' : (ESTILO_ESTADO[item.estado] || '');
      estadoSpan.className = ('chip ' + estilo).trim();
      estadoSpan.textContent = textoEst;
      badgeWrap.appendChild(estadoSpan);
    }

    if (item && item.resultado && (!item.estado || item.resultado !== item.estado)) {
      const resSpan = document.createElement('span');
      resSpan.className = 'chip ' +
        (item.resultado === 'LISTO' ? 'chip-ok' : '');
      resSpan.textContent = item.resultado;
      badgeWrap.appendChild(resSpan);
    }

    linea1.appendChild(badgeWrap);
    row.appendChild(linea1);

    const linea2 = document.createElement('div');
    linea2.className = 'proc-linea proc-meta';

    if (item && item.empresa) {
      const empSpan = document.createElement('span');
      empSpan.className = 'linea-corta';
      empSpan.textContent = item.empresa;
      linea2.appendChild(empSpan);
    } else if (item && item.fuente) {
      const fuenteSpan = document.createElement('span');
      fuenteSpan.className = 'chip';
      fuenteSpan.textContent = item.fuente === 'feed' ? 'Feed' : (item.fuente === 'busqueda' ? 'Búsqueda' : item.fuente);
      linea2.appendChild(fuenteSpan);
    }

    if (item && item.duracion_s !== undefined && item.duracion_s !== null) {
      const durSpan = document.createElement('span');
      durSpan.className = 'proc-pct';
      durSpan.textContent = item.duracion_s + ' s';
      linea2.appendChild(durSpan);
    }
    if (linea2.children.length > 0) {
      row.appendChild(linea2);
    }

    if (item && item.progreso && typeof item.progreso === 'object') {
      if (item.progreso.esperando_cuota) {
        const cuotaDiv = document.createElement('div');
        cuotaDiv.setAttribute('data-proceso-cuota', '1');
        cuotaDiv.className = 'proc-cuota';
        cuotaDiv.textContent = textoCuotaProceso(item.progreso);
        row.appendChild(cuotaDiv);
      } else if (item.progreso.etiqueta) {
        const progDiv = document.createElement('div');
        progDiv.setAttribute('data-proceso-progreso', '1');
        progDiv.className = 'proc-progreso';
        const etiqWrap = document.createElement('div');
        etiqWrap.className = 'proc-etiqueta';
        const etiqSpan = document.createElement('span');
        etiqSpan.className = 'linea-corta';
        etiqSpan.textContent = etiquetaPaso(item.progreso);
        etiqWrap.appendChild(etiqSpan);
        if (item.progreso.sub) {
          const subSpan = document.createElement('span');
          subSpan.setAttribute('data-proceso-sub', '1');
          subSpan.className = 'proc-sub';
          subSpan.textContent = item.progreso.sub;
          etiqWrap.appendChild(subSpan);
        }
        progDiv.appendChild(etiqWrap);
        if (item.progreso.pct !== undefined && item.progreso.pct !== null) {
          const pctSpan = document.createElement('span');
          pctSpan.className = 'proc-pct';
          pctSpan.textContent = item.progreso.pct + '%';
          progDiv.appendChild(pctSpan);
        }
        row.appendChild(progDiv);
      }

      const pasos = lineaPasos(item.progreso);
      if (pasos.length > 0) {
        const pasosWrap = document.createElement('div');
        pasosWrap.setAttribute('data-proceso-pasos', '1');
        pasosWrap.className = 'proc-pasos';
        for (const ps of pasos) {
          const pBadge = document.createElement('span');
          pBadge.className = 'chip ' +
            (ps.estado === 'hecho' ? 'chip-ok' :
             ps.estado === 'actual' ? 'chip-info' :
             '');
          pBadge.textContent = ps.etiqueta + (ps.duracion_s ? ' (' + ps.duracion_s + 's)' : '');
          pasosWrap.appendChild(pBadge);
        }
        row.appendChild(pasosWrap);
      }
    }

    return row;
  }

  function sembrarProgreso(postId, progreso) {
    if (!postId || !progreso || typeof progreso !== 'object') return;
    const id = Number(postId);
    const inicioParsed = progreso.inicio ? Date.parse(progreso.inicio) : NaN;
    const t0 = isNaN(inicioParsed) ? Date.now() : inicioParsed;
    const percent = Number(progreso.pct !== undefined ? progreso.pct : progreso.percent) || 0;
    const step = String(etiquetaPaso(progreso));
    const sub = progreso.sub || null;
    S.progreso.set(id, { t0, percent, step, sub });
  }

  let timerCargarProcesos = null;
  function debouncedCargarProcesos() {
    clearTimeout(timerCargarProcesos);
    timerCargarProcesos = setTimeout(() => {
      const panel = $('radarProcesosPanel');
      if (panel && !panel.hidden) {
        cargarProcesos();
      }
    }, 250);
  }

  function abrirProcesos() {
    const panel = $('radarProcesosPanel');
    if (!panel) return;
    procesosAbiertoEn = Date.now();
    panel.hidden = false;
    const btn = $('radarCola');
    if (btn) btn.setAttribute('aria-expanded', 'true');
    cargarProcesos();
  }

  function cerrarProcesos() {
    const panel = $('radarProcesosPanel');
    if (!panel) return;
    panel.hidden = true;
    const btn = $('radarCola');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  function alternarProcesos() {
    const panel = $('radarProcesosPanel');
    if (!panel) return;
    if (panel.hidden) abrirProcesos();
    else cerrarProcesos();
  }

  let fondoProcesosPulsado = false;
  let procesosAbiertoEn = 0;

  function pulsarFondoProcesos(e) {
    fondoProcesosPulsado = e.target === $('radarProcesosPanel');
  }

  function clicFondoProcesos(e) {
    const empezoAca = fondoProcesosPulsado;
    fondoProcesosPulsado = false;
    if (e.target !== $('radarProcesosPanel') || !empezoAca) return;
    if (Date.now() - procesosAbiertoEn < MS_SIN_CERRAR_AL_ABRIR) return;
    cerrarProcesos();
  }

  async function alternarGeneracion() {
    const btn = $('radarGeneracionBtn');
    if (!btn) return;
    btn.disabled = true;
    try {
      const r = await api('POST', '/generacion', { pausada: !S.estado.generacion_pausada });
      if (r.ok && r.data) onStatus(r.data);
      await cargarProcesos();
    } finally {
      btn.disabled = false;
    }
  }

  async function alternarAnalisis() {
    const btn = $('radarAnalisisBtn');
    if (!btn) return;
    btn.disabled = true;
    try {
      const r = await api('POST', '/analisis', { pausada: !S.estado.analisis_pausado });
      if (r.ok && r.data) onStatus(r.data);
      await cargarProcesos();
    } finally {
      btn.disabled = false;
    }
  }

  async function alternarBacklog() {
    const btn = $('radarBacklogBtn');
    if (!btn) return;
    const pausar = btn.textContent.trim() === 'Pausar';
    btn.disabled = true;
    try {
      const r = await api('POST', '/backlog', { activo: !pausar });
      if (r.ok && r.data) onStatus(r.data);
      await cargarProcesos();
    } finally {
      btn.disabled = false;
    }
  }

  /** «Errores pendientes por tipo»: posts que hoy siguen en error (lo resuelto no se muestra). */
  function renderErrores(lista, errores) {
    const etapas = { clasificacion: 'Clasificación', analisis: 'Análisis', generacion: 'Generación', envio: 'Envío', otra: 'Otra' };
    lista.appendChild(seccionTituloProcesos('Errores pendientes por tipo', errores.length));
    for (const e of errores) {
      const fila = document.createElement('div');
      fila.className = 'proc-fila proc-error';
      const cab = document.createElement('div');
      cab.className = 'proc-linea1';
      const tipo = document.createElement('span');
      tipo.className = 'proc-titulo-fila titulo-entero';
      tipo.textContent = e.tipo || 'Sin detalle';
      const veces = document.createElement('span');
      veces.className = 'chip chip-danger num';
      veces.textContent = '×' + (e.veces || 0);
      cab.appendChild(tipo);
      cab.appendChild(veces);
      const det = document.createElement('div');
      det.className = 'proc-meta';
      det.textContent = `${propio(etapas, e.etapa, e.etapa || '')} · ${e.posts || 0} post${e.posts === 1 ? '' : 's'}`
        + (Array.isArray(e.post_ids) && e.post_ids.length ? ' · #' + e.post_ids.slice(0, 6).join(', #') : '');
      fila.appendChild(cab);
      fila.appendChild(det);
      lista.appendChild(fila);
    }
    return errores.length;
  }

  /** Una candidata de las colas: canal, grupo de rol, antigüedad y afinidad. */
  function detalleCola(item) {
    const partes = [item.canal === 'email' ? 'Mail' : item.canal === 'link' ? 'Link' : 'Otro canal'];
    if (item.forzada) partes.push('Pedida a mano');
    else if (item.grupo) partes.push('Grupo ' + String(item.grupo).toUpperCase());
    if (typeof item.edad_dias === 'number') {
      // Días y horas: «Hace 5 d» redondeado hacía creer que entraba en la ventana de 5 días (creador, 08/11)
      const dias = Math.floor(item.edad_dias);
      const horas = Math.round((item.edad_dias - dias) * 24);
      partes.push(dias < 1 ? `Hace ${horas} h` : `Hace ${dias} d` + (horas ? ` ${horas} h` : ''));
    }
    if (typeof item.score === 'number' && item.score > 0) partes.push(`Afinidad ${Math.round(item.score)}`);
    return partes.join(' · ');
  }

  /** Panel «Colas»: la prioritaria (primero lo pedido a mano o ya tomado, después las 3 próximas analizadas) y
   *  la de fondo (3 próximas), con totales. */
  function renderColas(lista, c, tomadas) {
    let n = 0;
    tomadas = Array.isArray(tomadas) ? tomadas : [];
    const niveles = [
      ['prioritaria', `Cola prioritaria (hasta ${c.dias_prioritaria} días)`],
      ['fondo', 'Cola de fondo' + (c.fondo && !c.fondo.activo ? ' · en pausa' : '')],
    ];
    for (const [clave, titulo] of niveles) {
      const nivel = c[clave];
      if (!nivel) continue;
      const extra = clave === 'prioritaria' ? tomadas.length : 0;
      lista.appendChild(seccionTituloProcesos(titulo, nivel.total + extra));
      if (extra) {
        for (const item of tomadas) {
          lista.appendChild(filaProceso(item));
          n++;
        }
      }
      if (!nivel.total && extra) continue;
      if (!nivel.total) {
        const vacio = document.createElement('div');
        vacio.className = 'proc-meta';
        vacio.textContent = clave === 'prioritaria' ? 'Sin ofertas prioritarias esperando CV' : 'Sin ofertas de fondo';
        lista.appendChild(vacio);
        continue;
      }
      for (const item of nivel.proximas || []) {
        const fila = filaProceso({ post_id: item.post_id, puesto: item.puesto, empresa: item.empresa,
                                   posicion: item.posicion + extra, tipo: 'generacion' });
        const det = document.createElement('div');
        det.className = 'proc-meta';
        det.textContent = detalleCola(item);
        fila.appendChild(det);
        lista.appendChild(fila);
        n++;
      }
    }
    return n;
  }

  function seccionTituloProcesos(texto, conteo) {
    const h = document.createElement('div');
    h.className = 'proc-titulo';
    const t = document.createElement('span');
    t.textContent = texto;
    h.appendChild(t);
    if (conteo !== undefined && conteo !== null) {
      const c = document.createElement('span');
      c.className = 'proc-conteo';
      c.textContent = String(conteo);
      h.appendChild(c);
    }
    return h;
  }

  function renderProcesos(data) {
    if (!data || typeof data !== 'object') return;

    const topesEl = $('radarProcesosTopes');
    if (topesEl && data.topes) {
      topesEl.textContent = textoTopes(data.topes);
    }

    const cuotaEl = $('radarProcesosCuota');
    if (cuotaEl) {
      if (data.cuota && data.cuota.agotada) {
        cuotaEl.hidden = false;
        let msg = 'Esperando cuota de AGY';
        if (data.cuota.renueva) {
          try {
            const d = new Date(data.cuota.renueva);
            if (!isNaN(d.getTime())) {
              const hh = String(d.getHours()).padStart(2, '0');
              const mm = String(d.getMinutes()).padStart(2, '0');
              msg += ` · se renueva a las ${hh}:${mm}`;
            }
          } catch (_) {}
        }
        cuotaEl.textContent = msg;
      } else {
        cuotaEl.hidden = true;
        cuotaEl.textContent = '';
      }
    }

    const quedanEl = $('radarBacklogQuedan');
    if (quedanEl && data.colas && data.colas.fondo) {
      quedanEl.textContent = (data.colas.fondo.activo ? 'activa' : 'en pausa') + ` · ${data.colas.fondo.total} esperando`;
      if (quedanEl.dataset) quedanEl.dataset.estado = data.colas.fondo.activo ? 'activo' : 'pausa';
    }
    const backlogBtn = $('radarBacklogBtn');
    if (backlogBtn && data.backlog) {
      backlogBtn.textContent = data.backlog.activo ? 'Pausar' : 'Reanudar';
      if (backlogBtn.dataset) backlogBtn.dataset.accion = data.backlog.activo ? 'pausar' : 'reanudar';
    }

    const lista = $('radarProcesosLista');
    if (!lista) return;
    lista.textContent = '';

    let totalItems = renderHistorialAGY(lista, data.agy);

    if (Array.isArray(data.en_curso) && data.en_curso.length > 0) {
      lista.appendChild(seccionTituloProcesos('En curso', data.en_curso.length));
      for (const item of data.en_curso) {
        lista.appendChild(filaProceso(item));
        totalItems++;
      }
    }

    if (data.colas) {
      totalItems += renderColas(lista, data.colas, Array.isArray(data.cola_generacion) ? data.cola_generacion : []);
    }

    if (Array.isArray(data.cola_analisis) && data.cola_analisis.length > 0) {
      lista.appendChild(seccionTituloProcesos('Por clasificar o analizar', data.cola_analisis.length));
      for (const item of data.cola_analisis) {
        lista.appendChild(filaProceso(item));
        totalItems++;
      }
    }

    if (Array.isArray(data.errores) && data.errores.length > 0) {
      totalItems += renderErrores(lista, data.errores);
    }

    if (Array.isArray(data.terminados) && data.terminados.length > 0) {
      lista.appendChild(seccionTituloProcesos('Últimos terminados', data.terminados.length));
      for (const item of data.terminados) {
        lista.appendChild(filaProceso(item));
        totalItems++;
      }
    }

    if (totalItems === 0) {
      const vacio = document.createElement('div');
      vacio.className = 'vacio lista-vacia';
      vacio.textContent = 'Sin procesos registrados';
      lista.appendChild(vacio);
    }
  }

  function actualizarFilaEnCursoProcesos(id, nuevo, progObj) {
    const lista = $('radarProcesosLista');
    if (!lista) return;
    const row = lista.querySelector(`[data-post-id="${Number(id)}"]`);
    if (!row) return;

    // Si la fila decía "En cola", al recibir progreso pasa a "Generando"
    const badgeSpans = typeof row.querySelectorAll === 'function' ? row.querySelectorAll('span') : [];
    for (const b of badgeSpans) {
      if (b && b.textContent === 'En cola') {
        b.textContent = 'Generando';
        b.className = 'chip ' + ESTILO_ESTADO.GENERANDO;
        break;
      }
    }

    if (progObj && typeof progObj === 'object') {
      let cuotaEl = typeof row.querySelector === 'function' ? row.querySelector('[data-proceso-cuota]') : null;
      if (progObj.esperando_cuota) {
        if (!cuotaEl) {
          cuotaEl = document.createElement('div');
          cuotaEl.setAttribute('data-proceso-cuota', '1');
          cuotaEl.className = 'proc-cuota';
          const pasosEl = typeof row.querySelector === 'function' ? row.querySelector('[data-proceso-pasos]') : null;
          if (pasosEl && typeof row.insertBefore === 'function') {
            row.insertBefore(cuotaEl, pasosEl);
          } else {
            row.appendChild(cuotaEl);
          }
        }
        cuotaEl.textContent = textoCuotaProceso(progObj);
        const progEl = typeof row.querySelector === 'function' ? row.querySelector('[data-proceso-progreso]') : null;
        if (progEl && typeof progEl.remove === 'function') progEl.remove();
      } else {
        if (cuotaEl && typeof cuotaEl.remove === 'function') cuotaEl.remove();

        let progEl = typeof row.querySelector === 'function' ? row.querySelector('[data-proceso-progreso]') : null;
        if (!progEl) {
          progEl = document.createElement('div');
          progEl.setAttribute('data-proceso-progreso', '1');
          progEl.className = 'proc-progreso';
          const pasosEl = typeof row.querySelector === 'function' ? row.querySelector('[data-proceso-pasos]') : null;
          if (pasosEl && typeof row.insertBefore === 'function') {
            row.insertBefore(progEl, pasosEl);
          } else {
            row.appendChild(progEl);
          }
        }
        progEl.textContent = '';
        const etiqWrap = document.createElement('div');
        etiqWrap.className = 'proc-etiqueta';
        const etiqSpan = document.createElement('span');
        etiqSpan.className = 'linea-corta';
        etiqSpan.textContent = etiquetaPaso(progObj) || (nuevo && nuevo.step) || 'Generando CV';
        etiqWrap.appendChild(etiqSpan);
        const subTexto = ('sub' in progObj) ? (progObj.sub || null) : (nuevo && nuevo.sub ? nuevo.sub : null);
        if (subTexto) {
          const subSpan = document.createElement('span');
          subSpan.setAttribute('data-proceso-sub', '1');
          subSpan.className = 'proc-sub';
          subSpan.textContent = subTexto;
          etiqWrap.appendChild(subSpan);
        }
        progEl.appendChild(etiqWrap);
        const pct = progObj.pct !== undefined && progObj.pct !== null ? progObj.pct : (nuevo ? nuevo.percent : null);
        if (pct !== undefined && pct !== null) {
          const pctSpan = document.createElement('span');
          pctSpan.className = 'proc-pct';
          pctSpan.textContent = `${Math.round(pct)}%`;
          progEl.appendChild(pctSpan);
        }
      }

      const pasos = lineaPasos(progObj);
      if (pasos.length > 0) {
        let pasosWrap = typeof row.querySelector === 'function' ? row.querySelector('[data-proceso-pasos]') : null;
        if (!pasosWrap) {
          pasosWrap = document.createElement('div');
          pasosWrap.setAttribute('data-proceso-pasos', '1');
          pasosWrap.className = 'proc-pasos';
          row.appendChild(pasosWrap);
        }
        pasosWrap.textContent = '';
        for (const ps of pasos) {
          const pBadge = document.createElement('span');
          pBadge.className = 'chip ' +
            (ps.estado === 'hecho' ? 'chip-ok' :
             ps.estado === 'actual' ? 'chip-info' :
             '');
          pBadge.textContent = ps.etiqueta + (ps.duracion_s ? ' (' + ps.duracion_s + 's)' : '');
          pasosWrap.appendChild(pBadge);
        }
      }
    } else {
      let progEl = typeof row.querySelector === 'function' ? row.querySelector('[data-proceso-progreso]') : null;
      if (!progEl) {
        progEl = document.createElement('div');
        progEl.setAttribute('data-proceso-progreso', '1');
        progEl.className = 'proc-progreso';
        row.appendChild(progEl);
      }
      progEl.textContent = '';
      const etiqWrap = document.createElement('div');
      etiqWrap.className = 'proc-etiqueta';
      const etiqSpan = document.createElement('span');
      etiqSpan.className = 'linea-corta';
      etiqSpan.textContent = (nuevo && nuevo.step) || 'Generando CV';
      etiqWrap.appendChild(etiqSpan);
      if (nuevo && nuevo.sub) {
        const subSpan = document.createElement('span');
        subSpan.setAttribute('data-proceso-sub', '1');
        subSpan.className = 'proc-sub';
        subSpan.textContent = nuevo.sub;
        etiqWrap.appendChild(subSpan);
      }
      progEl.appendChild(etiqWrap);
      if (nuevo && nuevo.percent !== undefined && nuevo.percent !== null) {
        const pctSpan = document.createElement('span');
        pctSpan.className = 'proc-pct';
        pctSpan.textContent = `${Math.round(nuevo.percent)}%`;
        progEl.appendChild(pctSpan);
      }
    }
  }

  async function cargarProcesos() {
    const r = await api('GET', '/procesos');
    if (!r.ok || !r.data) return;
    renderProcesos(r.data);
    await cargarRadaresPortales();
  }

  // ------------------------------------------------------------------ un radar por portal (15/11)

  async function cargarRadaresPortales() {
    const cont = $('radarPortalesInterruptores');
    if (!cont) return;
    try {
      const r = await fetch('/api/portales/estado', { headers: { 'X-LinkedOut-Origen': 'portal' } });
      if (!r.ok) return;
      renderRadaresPortales(cont, await r.json());
    } catch (_) { /* el panel sigue con lo demás */ }
  }

  /** Estado de un radar de portal en pocas palabras: pausado (y por qué), apagado en las reglas o corriendo. */
  function estadoRadarPortal(p) {
    if (p.pausado) return p.motivo_pausa && p.motivo_pausa !== 'Pausado desde el panel' ? `en pausa: ${p.motivo_pausa}` : 'en pausa';
    if (!p.corre) return p.motivo || 'apagado';
    const hoy = p.consultas_por_dia ? `${p.consultas_hoy} de ${p.consultas_por_dia} consultas hoy` : '';
    return ['corriendo', hoy].filter(Boolean).join(' · ');
  }

  function renderRadaresPortales(cont, data) {
    cont.textContent = '';
    for (const p of (data && Array.isArray(data.portales) ? data.portales : [])) {
      const fila = document.createElement('div');
      fila.className = 'proc-control';
      fila.dataset.portal = p.portal;
      const texto = document.createElement('div');
      texto.className = 'proc-control-texto';
      const titulo = document.createElement('span');
      titulo.className = 'proc-control-nombre';
      titulo.textContent = `Radar ${p.nombre || p.portal} · `;
      const estado = document.createElement('span');
      estado.className = 'proc-control-estado';
      estado.textContent = estadoRadarPortal(p);
      titulo.appendChild(estado);
      texto.appendChild(titulo);
      const sw = document.createElement('button');
      sw.type = 'button';
      sw.className = 'radar-switch';
      sw.setAttribute('role', 'switch');
      sw.setAttribute('aria-checked', String(!p.pausado));
      sw.disabled = !p.corre && !p.pausado;  // apagado en las reglas: se prende en Mi perfil
      sw.innerHTML = '<span class="radar-switch-pista" aria-hidden="true"><span class="radar-switch-perilla"></span></span>';
      sw.appendChild(document.createTextNode(p.pausado ? 'Reanudar' : 'Pausar'));
      sw.addEventListener('click', async () => {
        sw.disabled = true;
        const r = await fetch(`/api/portales/${encodeURIComponent(p.portal)}/pausa`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LinkedOut-Origen': 'portal' },
          body: JSON.stringify({ pausado: !p.pausado }),
        });
        if (r.ok) renderRadaresPortales(cont, await r.json());
        else { sw.disabled = false; mostrarMensaje('No se pudo cambiar el radar del portal.', 'error'); }
      });
      fila.append(texto, sw);
      cont.appendChild(fila);
    }
  }

  // ------------------------------------------------------------------ revisión pendiente

  const ETIQUETA_REVISION = {
    dato_inventado: 'Dato completado',
    contradiccion: 'Contradicción corregida',
    placeholder: 'Campo sin completar',
    fallback: 'CV de respaldo',
    senal_ia: 'Revisar redacción',
  };

  function renderRevisionBtn() {
    const btn = $('radarRevisionBtn');
    if (!btn) return;
    const n = Number(S.estado.revision_pendientes) || 0;
    btn.hidden = n === 0;
    iconoYTexto(btn, 'flag', `Por revisar · ${n}`);
    if (n === 0) cerrarRevision();
  }

  function cerrarRevision() {
    const panel = $('radarRevisionPanel');
    if (panel) panel.hidden = true;
    const btn = $('radarRevisionBtn');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  async function alternarRevision() {
    const panel = $('radarRevisionPanel');
    if (!panel) return;
    if (!panel.hidden) return cerrarRevision();
    panel.hidden = false;
    $('radarRevisionBtn').setAttribute('aria-expanded', 'true');
    await cargarRevision();
  }

  async function cargarRevision() {
    const lista = $('radarRevisionLista');
    if (!lista) return;
    const r = await api('GET', '/revision');
    if (!r.ok || !r.data) {
      lista.textContent = mensajeDeDetalle(r.detail);
      return;
    }
    S.estado.revision_pendientes = r.data.pendientes;
    lista.replaceChildren(...r.data.items.map(itemRevision));
    if (!r.data.items.length) lista.textContent = 'Nada para revisar.';
  }

  /** «Puesto · Empresa» de la oferta; si no se sabe, el número del post (o de la postulación). */
  function tituloRevision(it) {
    const nombre = [it.puesto, it.empresa].filter(Boolean).join(' · ');
    if (nombre) return nombre;
    return it.post_id ? `Post #${it.post_id}` : (it.postulacion_id ? `Postulación #${it.postulacion_id}` : 'Sin oferta asociada');
  }

  /** Todo con textContent: el valor y el detalle pueden traer texto de la oferta. */
  function itemRevision(it) {
    const fila = document.createElement('div');
    fila.className = 'proc-tarjeta';
    const cab = document.createElement('div');
    cab.className = 'proc-linea';
    const titulo = document.createElement('span');
    titulo.className = 'proc-titulo-fila linea-corta';
    titulo.textContent = tituloRevision(it);
    cab.append(titulo);
    if (it.post_id) {
      const abrir = document.createElement('button');
      abrir.type = 'button';
      abrir.className = 'btn btn-sm empuja';
      abrir.textContent = 'Abrir';
      abrir.addEventListener('click', () => { cerrarRevision(); seleccionar(it.post_id); });
      cab.append(abrir);
    }
    const visto = document.createElement('button');
    visto.type = 'button';
    visto.className = (it.post_id ? '' : 'empuja ') + 'btn btn-sm';
    visto.innerHTML = svgIcono('check') + 'Visto';
    visto.addEventListener('click', () => ejecutar(visto, async () => {
      const r = await api('POST', `/revision/${it.id}/revisado`);
      if (r.ok && r.data) {
        S.estado.revision_pendientes = r.data.pendientes;
        fila.remove();
        renderRevisionBtn();
      } else {
        mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
      }
    }));
    cab.append(visto);
    fila.append(cab);
    // Qué revisar y por qué, en una frase (creador, 15/11): sin el texto completo ni el detalle técnico
    const frase = document.createElement('p');
    frase.className = 'proc-frase';
    frase.textContent = it.frase || `${ETIQUETA_REVISION[it.tipo] || it.tipo}.`;
    fila.append(frase);
    return fila;
  }

  function renderPill() {
    const pill = $('radarEstadoPill');
    if (!pill) return;
    const d = describirPill(S.estado, S.deteniendo);
    pill.textContent = d.texto;
    pill.className = ('chip estado-radar ' + d.clase).trim();
    pill.title = d.title || '';
  }

  function textoPausaBusquedas(p) {
    if (!p) return '';
    const d = parseFecha(p.hasta);
    const hhmm = d ? `${dos(d.getHours())}:${dos(d.getMinutes())}` : (p.hasta ? String(p.hasta).slice(11, 16) : '');
    const pausaLarga = Number(p.horas) > 6 ? ` (pausa de ${p.horas} h)` : '';
    return `LinkedIn pausó las búsquedas («tráfico de búsqueda inusual»). Se retoman a las ${hhmm}${pausaLarga}. El feed sigue.`;
  }

  function renderAvisos() {
    const gmail = $('radarGmailAviso');
    if (gmail) gmail.hidden = S.estado.gmail_configurado !== false;
    const pausa = $('radarEnviosPausados');
    if (pausa) pausa.hidden = !S.estado.envios_pausados;
    renderAvisoCuota();
    renderCuentas();

    const p = S.estado.busquedas_pausa;
    const box = $('radarBusquedasPausadas');
    if (box) {
      box.hidden = !p;
      if (p) {
        const txt = $('radarBusquedasPausadasTexto');
        if (txt) txt.textContent = textoPausaBusquedas(p);
        const link = $('radarBusquedasPausadasCaptura');
        if (link) {
          link.hidden = !p.captura;
          if (p.captura) link.href = p.captura;
        }
      }
    }
  }

  /** Cuentas de LinkedIn del Radar: cuál busca ahora y la pausa de cada una. */
  function textoCuentas(cuentas) {
    const lista = Array.isArray(cuentas) ? cuentas : [];
    const hora = (iso) => new Date(iso).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
    const nombre = (c) => (c.cuenta === 'secundaria' ? 'Cuenta 2' : 'Cuenta principal');
    const dos = lista.filter((c) => c.con_sesion).length > 1;
    if (!dos) return 'LinkedIn: una cuenta.';
    return 'LinkedIn: ' + lista.filter((c) => c.con_sesion).map((c) => {
      const estado = c.pausada_hasta ? `búsquedas pausadas hasta las ${hora(c.pausada_hasta)}` : (c.en_uso ? 'en uso' : 'disponible');
      return `${nombre(c)}, ${estado}`;
    }).join(' · ') + '.';
  }

  function renderCuentas() {
    const txt = $('radarCuentasTexto');
    if (!txt) return;
    const cuentas = S.estado.cuentas;
    txt.textContent = textoCuentas(cuentas);
  }

  /** Sin cuota de AGY nada falla: las llamadas esperan y una reintenta cada tanto. */
  function renderAvisoCuota() {
    const aviso = $('radarCuotaAviso');
    if (!aviso) return;
    const c = S.estado.agy_cuota || {};
    aviso.hidden = !c.agotada;
    if (!c.agotada) return;
    const hora = c.reintento ? new Date(c.reintento).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' }) : '';
    const renueva = c.renueva ? new Date(c.renueva).toLocaleString('es-AR', { weekday: 'long', hour: '2-digit', minute: '2-digit' }) : '';
    const msg = $('radarCuotaAvisoMsg');
    if (msg) {
      msg.textContent = 'Sin cuota de AGY: lo que está en curso y en cola espera, no se cancela.'
        + (hora ? ` Próximo intento ${hora}.` : '')
        + (renueva ? ` AGY la renueva el ${renueva}.` : '');
    }
  }

  async function reintentarCuota() {
    const btn = $('radarCuotaReintentarBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Probando…'; }
    try {
      const r = await api('POST', '/agy/reintentar');
      if (r.ok && r.data) S.estado.agy_cuota = r.data;
      renderAvisoCuota();
      mostrarMensaje(S.estado.agy_cuota && S.estado.agy_cuota.agotada
        ? 'AGY sigue sin cuota en esta cuenta.' : 'Volvió la cuota de AGY: la cola sigue.', S.estado.agy_cuota && S.estado.agy_cuota.agotada ? 'error' : 'ok');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Reintentar ahora'; }
    }
  }

  function renderAprobarTodos() {
    const btn = $('radarAprobarTodosBtn');
    if (!btn) return;
    const n = contarListos();
    iconoYTexto(btn, 'send', `Aprobar listos · ${n}`);
    btn.disabled = n === 0;
  }

  // ------------------------------------------------------------------ lista

  function normalizar(p) {
    const out = { ...p };
    const t = p && p.triage && typeof p.triage === 'object' ? p.triage : null;
    if (t) {
      if (t.puesto) out.puesto = t.puesto;
      if (t.empresa) out.empresa = t.empresa;
      if (t.motivo) out.motivo = t.motivo;
      if (t.alcance) out.alcance = t.alcance;
      if (t.pais) out.pais = t.pais;
    }
    // El análisis (Estratega) manda sobre la clasificación
    const a = p && p.analisis && typeof p.analisis === 'object' ? p.analisis : null;
    if (a) {
      out.no_apto = a.no_apto || null;
      if (a.puesto) out.puesto = a.puesto;
      if (a.empresa) out.empresa = a.empresa;
      if (a.match_score !== undefined && a.match_score !== null) out.match_score = a.match_score;
      out.oferta_cerrada = !!a.oferta_cerrada;
    }
    return out;
  }

  function guardarPost(p) {
    const previo = S.posts.get(p.id) || {};
    if (!perteneceAlModo({ ...previo, ...p })) return null;
    const nuevo = { ...previo, ...normalizar(p) };
    S.posts.set(p.id, nuevo);
    return nuevo;
  }

  function claseCard(seleccionada) {
    return 'oferta' + (seleccionada ? ' elegida' : '');
  }

  /** Badge del estado (recibe el post): el detalle de los pendientes va en el `title`. */
  function badgeEstado(post) {
    const estado = post && post.estado;
    const { texto, sub } = etiquetaEstado(post);
    const clase = propio(ESTILO_ESTADO, estado, ESTILO_ESTADO.NUEVO);
    const enCola = texto === 'En cola';
    const pulso = enCola ? svgIcono('clock')
      : (estado === 'GENERANDO' || estado === 'ENVIANDO' || estado === 'ANALIZANDO' ? svgIcono('loader-circle', 'girando') : '');
    const titulo = sub ? ` title="${escapeHtml(sub)}"` : '';
    return `<span${titulo} class="chip ${clase}">${pulso}${escapeHtml(texto)}</span>`;
  }

  /** Detalle chico al lado del badge (solo los pendientes lo tienen). */
  function subEstado(post) {
    const { sub } = etiquetaEstado(post);
    return sub ? `<span class="oferta-subestado">${escapeHtml(sub)}</span>` : '';
  }

  function badgeMatch(valor, atenuado) {
    if (valor === undefined || valor === null || valor === '') return '';
    const n = Math.round(Number(valor));
    if (isNaN(n)) return '';
    const umbral = Number(S.estado.umbral_match);
    const ok = isNaN(umbral) ? true : n >= umbral;
    const clase = ok ? 'chip-ok' : 'chip-warn';
    return `<span class="chip num ${clase}${atenuado ? ' atenuado' : ''}" title="${atenuado ? 'Afinidad mayormente por aprendizajes que marcaste vos' : 'Match del análisis de la oferta'}">${n}%</span>`;
  }

  function visible(p, filtro) {
    // Ignoradas aparece sin ubicación ni canal; con motivos también pertenece a No apto.
    const ignorada = !!p && p.estado === 'IGNORADA';
    if (ignorada && filtro === 'NO_APTO' && motivosNoApto(p).length > 0) return true;
    if (filtro === 'IGNORADA' || ignorada) return filtro === 'IGNORADA' && ignorada;
    return coincideFiltro(p, filtro) && coincideAlcance(p, S.alcance) && coincideCanal(p, S.canal)
      && (S.modo !== 'portales' || coincidePortal(p, S.portal));
  }

  const CLASE_ALCANCE = {
    ARGENTINA: 'chip-info',
    EXTERIOR_COMPATIBLE: 'chip-ok',
    EXTERIOR: '',
    DESCONOCIDO: '',
  };

  function badgeAlcance(p) {
    const etiqueta = etiquetaAlcance(p.alcance);
    if (!etiqueta) return '';
    const titulo = p.pais ? ` title="${escapeHtml(p.pais)}"` : '';
    const clase = propio(CLASE_ALCANCE, p.alcance, CLASE_ALCANCE.DESCONOCIDO);
    return `<span class="chip ${clase}"${titulo}>${svgIcono('pin')}${escapeHtml(etiqueta)}</span>`;
  }

  /** Línea de destino de la tarjeta: el email, o el destino del canal (URL / número) / nombre del canal. */
  function textoDestinoCard(p) {
    if (esEmail(p)) return p.email_to || (p.emails && p.emails[0]) || 'sin email';
    return p.destino_canal || propio(NOMBRE_CANAL, p.canal, '') || 'sin destino';
  }

  function contenedorLista(p) {
    return $(p && p.fuente === 'feed' ? 'radarListaFeed' : 'radarLista');
  }

  /** Cuenta los requisitos por estado: lo que cumplís por tu experiencia, lo transferible, las brechas y lo
   *  que quedó cubierto porque lo marcaste como aprendido. Sin requisitos (o ausentes) devuelve todo en cero. */
  function desgloseAfinidad(requisitos) {
    const d = { cumple: 0, transferible: 0, brecha: 0, aprendido: 0, total: 0 };
    (Array.isArray(requisitos) ? requisitos : []).forEach((r) => {
      if (!r || typeof r !== 'object') return;
      if (r.logistico) return;
      d.total += 1;
      if (r.estado === 'BRECHA_REAL') d.brecha += 1;
      else if (r.aprendido) d.aprendido += 1;
      else if (r.estado === 'MATCH_DIRECTO') d.cumple += 1;
      else if (r.estado === 'TRANSFERIBLE') d.transferible += 1;
    });
    return d;
  }

  /** Texto chico y explicación (title) del desglose; null si no hay requisitos. `mayormenteAprendido`: más de la mitad. */
  function resumenAfinidad(requisitos) {
    const d = desgloseAfinidad(requisitos);
    if (!d.total) return null;
    const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
    const partes = [];
    if (d.cumple) partes.push(`${d.cumple} ✓`);
    if (d.transferible) partes.push(plural(d.transferible, 'transferible', 'transferibles'));
    if (d.aprendido) partes.push(plural(d.aprendido, 'aprendido', 'aprendidos'));
    partes.push(`${d.brecha} ${d.brecha === 1 ? 'falta' : 'faltan'}`);
    let titulo = `Afinidad calculada sobre ${plural(d.total, 'requisito', 'requisitos')}: ${d.cumple} los cumplís por tu experiencia, ` +
      `${d.aprendido} porque los marcaste como aprendidos`;
    if (d.transferible) titulo += `, ${d.transferible} por experiencia parecida`;
    if (d.brecha) titulo += `, ${d.brecha} todavía sin cubrir`;
    return { texto: partes.join(' · '), titulo, mayormenteAprendido: d.aprendido * 2 > d.total };
  }

  /** Línea chica bajo el match: «6 ✓ · 27 aprendidos · 0 brecha» (solo números y textos fijos). */
  function htmlAfinidad(a) {
    const leyenda = a.mayormenteAprendido
      ? ' · <span class="oferta-aviso">mayormente por aprendizajes</span>' : '';
    return `<div data-afinidad class="oferta-afinidad" title="${escapeHtml(a.titulo)}">${escapeHtml(a.texto)}${leyenda}</div>`;
  }

  /** Requisitos de un post: los del análisis (si ya se abrió el detalle, es lo más fresco) o los del resumen de la lista. */
  function requisitosPost(p) {
    if (!p) return [];
    const a = p.analisis && typeof p.analisis === 'object' ? p.analisis : null;
    if (a && Array.isArray(a.requisitos)) return a.requisitos;
    return p.requisitos_estado;
  }

  function motivosNoApto(p) {
    // El resumen fresco puede traer null mientras aún conservamos un análisis anterior en caché.
    const sello = p && (Object.prototype.hasOwnProperty.call(p, 'no_apto')
      ? p.no_apto : p.analisis && p.analisis.no_apto);
    return sello && Array.isArray(sello.motivos) ? sello.motivos.filter(m => m && typeof m === 'object') : [];
  }

  function puedePostularIgual(p) {
    return !!p && ['ANALIZADA', 'LISTO', 'IGNORADA'].includes(p.estado) && !p.aprobado_at && motivosNoApto(p).length > 0;
  }

  function selloNoApto(p, todos) {
    const motivos = motivosNoApto(p);
    if (!motivos.length) return null;
    const caja = elemento('div', 'no-apto');
    caja.setAttribute('data-no-apto', '');
    if (todos) {
      motivos.forEach(m => caja.appendChild(elemento('p', '', 'No apto: ' + String(m.texto || ''))));
    } else caja.textContent = 'No apto: ' + String(motivos[0].texto || '');
    return caja;
  }

  /** Puesto del post: el del análisis manda sobre el de la clasificación (igual que `normalizar`). */
  function puestoDePost(p) {
    if (!p) return '';
    const a = p.analisis && typeof p.analisis === 'object' ? p.analisis : null;
    const t = p.triage && typeof p.triage === 'object' ? p.triage : null;
    return String((a && a.puesto) || (t && t.puesto) || p.puesto || '').trim();
  }

  function crearCard(p) {
    const el = document.createElement('div');
    el.dataset.postId = String(p.id);
    el.setAttribute('role', 'button');
    el.tabIndex = 0;
    el.className = claseCard(p.id === S.selId);
    // El puesto manda arriba (es lo que se está eligiendo); sin puesto queda el autor, como antes
    const puesto = puestoDePost(p);
    const titulo = puesto || p.autor || 'Autor desconocido';
    const linea2 = puesto
      ? [p.empresa, p.autor].filter(Boolean).join(' · ')
      : (p.empresa || p.extracto || '');
    const aprobado = p.estado === 'LISTO' && p.aprobado_at
      ? '<span class="chip chip-ok">en cola</span>' : '';
    const acc = accionesPara(p);
    const botonCard = (accion, icono, texto, clase) =>
      `<button type="button" data-accion="${accion}" class="btn btn-sm ${clase}">${svgIcono(icono)}${texto}</button>`;
    const acciones = [
      acc.analizar ? botonCard('analizar', 'search', p.prioridad ? 'Priorizado' : 'Analizar ahora', '') : '',
      acc.generarCv ? botonCard('generar-cv', 'file-text', 'CV y mail', 'btn-primary') : '',
      acc.postularIgual ? botonCard('postular-igual', 'file-text', 'Postular igual', 'btn-borde-warn') : '',
      puedePostularIgual(p) ? botonCard('descartar', 'x', 'Descartar', 'btn-danger') : '',
    ].join('');
    const afinidad = resumenAfinidad(requisitosPost(p));
    // Con sello «No apto» el título va entero (cada palabra cuenta); si no, en una línea
    const claseTitulo = motivosNoApto(p).length ? 'titulo-entero' : 'titulo-linea';
    el.innerHTML = `
      <div class="oferta-top">
        <div class="oferta-titulo ${claseTitulo}">${escapeHtml(titulo)}</div>
        <div class="oferta-estados">${badgeEstado(p)}${subEstado(p)}</div>
      </div>
      <div class="oferta-sub">${escapeHtml(linea2)}</div>
      <div data-score class="oferta-meta">
        ${badgeMatch(p.match_score, Boolean(afinidad && afinidad.mayormenteAprendido))}
        ${p.oferta_cerrada ? '<span class="chip chip-danger" title="La página de la oferta dice que ya no acepta postulaciones">cerrada</span>' : ''}
        ${badgeAlcance(p)}
        <span class="oferta-canal" title="${escapeHtml(nombreCanal(p.canal))}">${escapeHtml(iconoCanal(p.canal))}</span>
        <span class="oferta-destino">${escapeHtml(textoDestinoCard(p))}</span>
        ${aprobado}
        <span class="oferta-fecha">${escapeHtml(p.fecha_rel || '')}</span>
      </div>
      ${afinidad ? htmlAfinidad(afinidad) : ''}
      ${acciones ? `<div class="oferta-acciones">${acciones}</div>` : ''}
      ${htmlProgreso(p)}`;
    const sello = selloNoApto(p, false);
    if (sello) (el.querySelector('[data-score]') || el).appendChild(sello);
    const logo = logoPortal(p);
    const fila = el.querySelector ? el.querySelector('[data-score]') : null;
    if (logo && fila) fila.insertBefore(logo, fila.firstChild);
    const postular = botonPostularSitio(p);
    if (postular) {
      const caja = document.createElement('div');
      caja.className = 'oferta-acciones';
      caja.appendChild(postular);
      el.appendChild(caja);
    }
    return el;
  }

  // ------------------------------------------------------------------ progreso de Generar CV

  /** Posición en la cola de generación (1 = el próximo); los CV se generan de a uno, el de menor id primero. */
  function posicionEnCola(id) {
    const enCola = Array.from(S.posts.values())
      .filter((q) => q.estado === 'GENERANDO' && !S.progreso.has(q.id))
      .map((q) => q.id)
      .sort((a, b) => a - b);
    return enCola.indexOf(id) + 1;
  }

  function formatearDuracion(seg) {
    const s = Math.max(0, Math.round(seg));
    return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} min` : `${s} s`;
  }

  /** Barra de progreso de un post en GENERANDO (vacío en otro estado). `detalle` agrega la aclaración larga. */
  function htmlProgreso(p, detalle) {
    if (!p || p.estado !== 'GENERANDO') return '';
    const pr = S.progreso.get(p.id);
    const nota = detalle
      ? '<p class="progreso-nota">Sigue en segundo plano: podés ver otras ofertas o pedir más CV (se generan de a uno, en orden).</p>'
      : '';
    if (!pr) {
      const pos = posicionEnCola(p.id);
      return `<div data-progreso class="progreso-cv en-cola">${svgIcono('clock')}En cola${pos > 1 ? ` (${pos}.º)` : ''}: empieza cuando termine el CV en curso${nota}</div>`;
    }
    const pct = Math.max(2, Math.min(99, Number(pr.percent) || 0));
    const transcurrido = formatearDuracion((Date.now() - pr.t0) / 1000);
    return `<div data-progreso class="progreso-cv" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}" aria-label="Generación del CV">
        <div class="progreso-fila">
          <span class="progreso-paso">${svgIcono('loader-circle', 'girando')}${escapeHtml(pr.step || 'Generando el CV')}</span>
          <span class="num">${Math.round(pct)}% · ${escapeHtml(transcurrido)}</span>
        </div>
        <div class="barra-progreso"><div style="width:${pct}%"></div></div>
        ${nota}
      </div>`;
  }

  /** Repinta solo la barra (tarjeta y detalle) sin reconstruir nada más. */
  function pintarProgreso(id) {
    const p = S.posts.get(id);
    if (typeof document.querySelectorAll === 'function') {
      document.querySelectorAll(`[data-post-id="${Number(id)}"] [data-progreso]`).forEach((el) => {
        el.outerHTML = htmlProgreso(p);
      });
    }
    const det = $('radarProgresoDetalle');
    if (det && S.selId === id) det.innerHTML = htmlProgreso(p, true);
  }

  /** Evento del pipeline (status / status_heartbeat con `radar_post_id`) o `radar_progreso` del radar. */
  function onProgreso(data) {
    if (!data || typeof data !== 'object') return;
    const id = Number(data.radar_post_id !== undefined ? data.radar_post_id : data.id);
    if (!Number.isFinite(id) || id <= 0) return;

    const p = S.posts.get(id);
    if (p && p.estado !== 'GENERANDO') return;

    const previo = S.progreso.get(id);
    const nuevo = { t0: previo ? previo.t0 : Date.now(), percent: previo ? previo.percent : 0, step: previo ? previo.step : '', sub: previo ? previo.sub : null };
    if (data.progreso && data.progreso.inicio) {
      const parsed = Date.parse(data.progreso.inicio);
      if (Number.isFinite(parsed)) nuevo.t0 = parsed;
    }
    if (Number.isFinite(Number(data.percent))) nuevo.percent = Math.max(nuevo.percent, Number(data.percent));
    if (data.step) nuevo.step = String(data.step);
    if (data.progreso && data.progreso.etiqueta) nuevo.step = String(etiquetaPaso(data.progreso));
    if (data.progreso && Number.isFinite(Number(data.progreso.pct))) nuevo.percent = Number(data.progreso.pct);
    if (data.progreso && ('sub' in data.progreso)) {
      nuevo.sub = data.progreso.sub || null;
    } else if ('sub' in data) {
      nuevo.sub = data.sub || null;
    }

    S.progreso.set(id, nuevo);

    actualizarFilaEnCursoProcesos(id, nuevo, data.progreso);
    renderCola();

    if (p && p.estado === 'GENERANDO') {
      const primero = !previo;
      if (primero) {
        // Los que esperan corren un lugar en la cola
        Array.from(S.posts.values()).filter((q) => q.estado === 'GENERANDO').forEach((q) => pintarProgreso(q.id));
      } else {
        pintarProgreso(id);
      }
    }
  }


  /** Orden de las tarjetas: mayor match primero (sin analizar al final), o más recientes primero. */
  function comparar(a, b) {
    if (S.orden === 'match') {
      const ma = Number.isFinite(Number(a.match_score)) && a.match_score !== null ? Number(a.match_score) : -1;
      const mb = Number.isFinite(Number(b.match_score)) && b.match_score !== null ? Number(b.match_score) : -1;
      if (ma !== mb) return mb - ma;
    }
    return b.id - a.id;
  }

  function renderLista() {
    const busqueda = $('radarLista');
    const feed = $('radarListaFeed');
    if (!busqueda) return;
    const visibles = Array.from(S.posts.values())
      .filter((p) => visible(p, S.filtro))
      .sort(comparar);
    [[busqueda, false], [feed, true]].forEach(([cont, esFeed]) => {
      if (!cont) return;
      cont.textContent = '';
      const frag = (document.createDocumentFragment && document.createDocumentFragment()) || document.createElement('div');
      visibles.filter((p) => (p.fuente === 'feed') === esFeed).forEach((p) => frag.appendChild(crearCard(p)));
      cont.appendChild(frag);
    });
    renderChips();
    renderVacio();
    renderAprobarTodos();
  }

  /** Inserta/actualiza/quita una sola tarjeta (en su lugar según `comparar`) en el panel de su fuente
   *  (búsqueda global o Mi feed), sin reconstruir la lista. */
  function upsertCard(p) {
    if (!p) return;
    const cont = contenedorLista(p);
    if (!cont) return;
    const otro = cont.id === 'radarLista' ? $('radarListaFeed') : $('radarLista');
    const sel = `[data-post-id="${Number(p.id)}"]`;
    // Si la fuente cambió (el evento SSE no la trae), se quita del otro panel
    const ajena = otro ? otro.querySelector(sel) : null;
    if (ajena) ajena.remove();
    const existente = cont.querySelector(sel);
    if (!visible(p, S.filtro)) {
      if (existente) existente.remove();
    } else {
      const nueva = crearCard(p);
      if (existente) existente.remove();
      // El match puede cambiar (al analizarse): la tarjeta se reubica
      const siguiente = Array.from(cont.children).find((c) => {
        const otroPost = S.posts.get(Number(c.dataset.postId));
        return otroPost && comparar(p, otroPost) < 0;
      });
      cont.insertBefore(nueva, siguiente || null);
    }
    renderChips();
    renderVacio();
    renderAprobarTodos();
  }

  /** Texto del panel sin tarjetas. Con el chip Relevantes y solo pendientes de análisis en ese panel,
   *  avisa cuántos hay y dónde verlos (el chip Pendientes). */
  function textoVacio(esFeed) {
    if (S.modo === 'manual') {
      const manuales = Array.from(S.posts.values()).filter((p) => p.fuente === 'manual');
      if (!manuales.length) {
        return 'Todavía no cargaste ofertas. Tocá «Nueva oferta» para pegar un texto o un link.\nTus postulaciones anteriores están en «Historial anterior».';
      }
      let texto = 'No hay ofertas en este filtro.';
      const pendientes = manuales.filter((p) => p.estado === 'NUEVO').length;
      if (S.filtro === 'RELEVANTES' && pendientes > 0) {
        texto += ` (hay ${pendientes} sin clasificar: chip Por analizar)`;
      }
      return texto;
    }
    if (S.modo === 'portales' && !Array.from(S.posts.values()).some(esDePortal)) {
      return 'Todavía no hay ofertas de portales. Marcá Jooble o Indeed en tus búsquedas (Mi perfil) e iniciá el radar.';
    }
    const delPanel = Array.from(S.posts.values()).filter((p) => (p.fuente === 'feed') === esFeed);
    if (!delPanel.length) {
      return esFeed ? 'Todavía no hay ofertas de tu feed. Iniciá el radar: lee tu feed de LinkedIn en cada ciclo.'
        : 'Todavía no hay ofertas. Iniciá el radar para empezar a leer LinkedIn.';
    }
    let texto = 'No hay ofertas en este filtro.';
    const pendientes = delPanel.filter((p) => p.estado === 'NUEVO').length;
    if (S.filtro === 'RELEVANTES' && pendientes > 0) {
      texto += ` (hay ${pendientes} sin clasificar: chip Por analizar)`;
    }
    return texto;
  }

  function renderVacio() {
    const manuales = Array.from(S.posts.values()).filter((p) => p.fuente === 'manual');
    [['radarLista', 'radarListaVacia', 'radarPanelBusquedaCuenta', false], ['radarListaFeed', 'radarListaFeedVacia', 'radarPanelFeedCuenta', true]]
      .forEach(([idLista, idVacia, idCuenta, esFeed]) => {
        const cont = $(idLista);
        const vacio = $(idVacia);
        if (!cont) return;
        const cuenta = $(idCuenta);
        if (cuenta) cuenta.textContent = String(cont.children.length);
        if (!vacio) return;
        vacio.hidden = cont.children.length > 0;
        if (!vacio.hidden) {
          if (S.modo === 'manual' && manuales.length === 0) {
            vacio.textContent = '';
            const p1 = document.createElement('p');
            p1.textContent = 'Todavía no cargaste ofertas. Tocá «Nueva oferta» para pegar un texto o un link.';
            const p2 = document.createElement('p');
            p2.className = 'proc-meta';
            p2.textContent = 'Tus postulaciones anteriores están en «Historial anterior».';
            vacio.appendChild(p1);
            vacio.appendChild(p2);
          } else {
            vacio.textContent = textoVacio(esFeed);
          }
        }
      });
  }

  // Filtros fuera de la fila de estados que están activos (rediseño E2): se ven como chips que se pueden quitar
  const TEXTO_ALCANCE = { ARGENTINA: 'Argentina', EXTERIOR_COMPATIBLE: 'Exterior compatible' };
  const TEXTO_CANAL = { EMAIL: 'Con mail', ENLACE: 'Solo enlace' };

  function filtrosActivos(st) {
    const out = [];
    if (TEXTO_ALCANCE[st.alcance]) out.push({ clave: 'alcance', texto: TEXTO_ALCANCE[st.alcance] });
    if (TEXTO_CANAL[st.canal]) out.push({ clave: 'canal', texto: TEXTO_CANAL[st.canal] });
    if (st.orden === 'recientes') out.push({ clave: 'orden', texto: 'Más recientes' });
    if (st.filtro === 'NO_APTO') out.push({ clave: 'no_apto', texto: 'También las no aptas' });
    return out;
  }

  function quitarFiltro(clave) {
    if (clave === 'alcance') S.alcance = 'TODAS';
    else if (clave === 'canal') S.canal = 'TODOS';
    else if (clave === 'orden') {
      S.orden = 'match';
      const sel = $('radarOrden');
      if (sel) sel.value = 'match';
    } else if (clave === 'no_apto') S.filtro = 'RELEVANTES';
    renderLista();
    marcarSeleccion();
  }

  function renderFiltrosActivos() {
    const activos = filtrosActivos(S);
    const cuenta = $('radarFiltrosCuenta');
    if (cuenta) {
      cuenta.hidden = activos.length === 0;
      cuenta.textContent = String(activos.length);
    }
    const cont = $('radarFiltrosActivos');
    if (!cont || typeof cont.replaceChildren !== 'function') return;
    cont.hidden = activos.length === 0;
    cont.replaceChildren(...activos.map((f) => {
      const chip = document.createElement('span');
      chip.className = 'tag';
      chip.append(document.createTextNode(f.texto));
      const quitar = document.createElement('button');
      quitar.type = 'button';
      quitar.dataset.quitarFiltro = f.clave;
      quitar.setAttribute('aria-label', 'Quitar filtro ' + f.texto);
      const ico = icono('x');
      if (ico) quitar.append(ico); else quitar.textContent = '×';
      chip.append(quitar);
      return chip;
    }));
  }

  function marcar(b, activo) {
    b.setAttribute('aria-pressed', String(activo));
  }

  function renderChips() {
    const cont = $('radarFiltros');
    if (!cont) return;
    const noApto = $('radarFiltroNoApto');
    if (noApto) {
      marcar(noApto, S.filtro === 'NO_APTO');
      const c = noApto.querySelector('[data-count]');
      if (c) c.textContent = String(Array.from(S.posts.values())
        .filter(p => perteneceAlModo(p) && visible(p, 'NO_APTO')).length);
    }
    const cuentas = {};
    FILTROS.forEach((f) => { cuentas[f] = 0; });
    S.posts.forEach((p) => FILTROS.forEach((f) => { if (visible(p, f)) cuentas[f] += 1; }));
    cont.querySelectorAll('button[data-estado]').forEach((b) => {
      const f = b.dataset.estado;
      marcar(b, f === S.filtro);
      const c = b.querySelector('[data-count]');
      if (c) c.textContent = String(cuentas[f] || 0);
    });
    renderSelectorEstado(cont, noApto);
    const contAlcance = $('radarFiltroAlcance');
    if (contAlcance) contAlcance.querySelectorAll('button[data-alcance]').forEach((b) => marcar(b, b.dataset.alcance === S.alcance));
    const contCanal = $('radarFiltroCanal');
    if (contCanal) contCanal.querySelectorAll('button[data-canal]').forEach((b) => marcar(b, b.dataset.canal === S.canal));
    const contPortal = $('radarFiltroPortal');
    if (contPortal) {
      contPortal.querySelectorAll('button[data-portal]').forEach((b) => {
        const portal = b.dataset.portal;
        marcar(b, portal === S.portal);
        const c = b.querySelector('[data-count]');
        if (c) c.textContent = String(Array.from(S.posts.values()).filter((p) => esDePortal(p) && coincidePortal(p, portal)).length);
      });
    }
    renderFiltrosActivos();
  }

  // Selector de estado (creador, 19/11, opción A): muestra el estado elegido y su número, «Relevantes · 30»
  function renderSelectorEstado(cont, noApto) {
    const texto = $('radarEstadoTexto');
    const cuenta = $('radarEstadoCuenta');
    if (!texto || !cuenta) return;
    const activo = S.filtro === 'NO_APTO' ? noApto : cont.querySelector(`button[data-estado="${S.filtro}"]`);
    if (!activo) return;
    const nodo = Array.from(activo.childNodes || []).find((n) => n.nodeType === 3 && n.textContent.trim());
    texto.textContent = S.filtro === 'NO_APTO' ? 'No aptas' : (nodo ? nodo.textContent.trim() : S.filtro);
    const c = activo.querySelector('[data-count]');
    cuenta.textContent = c ? c.textContent : '0';
  }

  function itemsMenuEstados() {
    const menu = $('radarFiltros');
    return menu ? Array.from(menu.querySelectorAll('button[data-estado]')) : [];
  }

  function abrirMenuEstados() {
    const menu = $('radarFiltros');
    const btn = $('radarEstadoBtn');
    if (!menu || !btn) return;
    cerrarPanelFiltros();
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    const items = itemsMenuEstados();
    const activo = items.find((b) => b.getAttribute('aria-pressed') === 'true') || items[0];
    if (activo && typeof activo.focus === 'function') activo.focus();
  }

  function cerrarMenuEstados(devolverFoco) {
    const menu = $('radarFiltros');
    const btn = $('radarEstadoBtn');
    if (!menu || !btn) return;
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    if (devolverFoco && typeof btn.focus === 'function') btn.focus();
  }

  function cerrarPanelFiltros() {
    const panel = $('radarFiltrosPanel');
    const btn = $('radarFiltrosBtn');
    if (panel) panel.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  // Flechas (con vuelta), Inicio y Fin recorren la lista; Esc la cierra y el foco vuelve al selector
  function teclaMenuEstados(e) {
    const items = itemsMenuEstados();
    const i = items.indexOf(document.activeElement);
    let destino = null;
    if (e.key === 'ArrowDown') destino = items[(i + 1) % items.length];
    else if (e.key === 'ArrowUp') destino = items[(i - 1 + items.length) % items.length];
    else if (e.key === 'Home') destino = items[0];
    else if (e.key === 'End') destino = items[items.length - 1];
    else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      cerrarMenuEstados(true);
      return;
    }
    if (destino) {
      e.preventDefault();
      destino.focus();
    }
  }

  function marcarSeleccion() {
    ['radarLista', 'radarListaFeed'].forEach((id) => {
      const cont = $(id);
      if (!cont) return;
      Array.from(cont.children).forEach((c) => {
        const pId = (c && c.dataset && c.dataset.postId) || (c && c.getAttribute && c.getAttribute('data-post-id'));
        c.className = claseCard(Number(pId) === S.selId);
      });
    });
  }

  // ------------------------------------------------------------------ detalle (columna central)

  /** Texto del post para el detalle: nunca un bloque vacío. Sin el detalle completo se muestra el
   *  extracto de la lista y se avisa (con «Reintentar» si falló la carga). */
  function htmlTextoPost(p) {
    // Contenido de la oferta literal: escapado y en pre-wrap, sin transformar nada (invariante del rediseño)
    const pre = (t) => `<pre class="texto-oferta">${escapeHtml(t)}</pre>`;
    if (typeof p.texto === 'string' && p.texto.trim()) return pre(p.texto);
    if (typeof p.texto === 'string') {
      return '<p class="vacio">Sin texto capturado.</p>';
    }
    const extracto = String(p.extracto || '').trim();
    const aviso = S.detalleFallido === p.id
      ? '<p class="det-alerta">No se pudo cargar el texto completo. '
        + '<button type="button" id="radarPostReintentar" class="enlace-accion">Reintentar</button></p>'
      : '<p class="det-meta">Cargando el texto completo…</p>';
    return (extracto ? pre(extracto + '…') : '') + aviso;
  }

  /** Bloque del detalle; `tono`: 'info', 'ok', 'warn' o 'danger' (borde y fondo suaves de tokens). */
  function bloque(titulo, contenidoHtml, tono) {
    return `<section class="bloque-det${tono ? ' tono-' + tono : ''}">
      <h3 class="bloque-titulo">${titulo}</h3>
      <div class="bloque-cuerpo">${contenidoHtml}</div></section>`;
  }

  const ESTILO_REQUISITO = {
    MATCH_DIRECTO: ['Coincide', 'req-ok'],
    TRANSFERIBLE: ['Transferible', 'req-warn'],
    BRECHA_REAL: ['Falta', 'req-danger'],
  };

  /** Análisis del Estratega (el mismo de Postulaciones): score, dolores y requisitos con su estado. */
  function htmlAnalisis(a) {
    if (!a || typeof a !== 'object') return '';
    const partes = [];
    if (a.oferta_cerrada) {
      partes.push('<div class="det-alerta">La página de la oferta dice que ya no acepta postulaciones.</div>');
    }
    const dolores = Array.isArray(a.dolores_empresa) ? a.dolores_empresa.filter(Boolean) : [];
    if (dolores.length) {
      partes.push('<h4 class="sub-det">Qué necesitan</h4>' +
        `<ul class="lista-det">${dolores.map((d) => `<li>${escapeHtml(d)}</li>`).join('')}</ul>`);
    }
    const reqs = Array.isArray(a.requisitos) ? a.requisitos.filter((r) => r && typeof r === 'object') : [];
    if (reqs.length) {
      const afinidad = resumenAfinidad(reqs);
      partes.push('<div class="det-req-cab"><h4 class="sub-det">Requisitos</h4>' +
        '<span class="det-meta">tocá el estado para cambiarlo</span></div>' +
        (afinidad ? htmlAfinidad(afinidad) : '') +
        '<ul data-requisitos class="reqs-det"></ul>');
    }
    const links = Array.isArray(a.links_leidos) ? a.links_leidos.filter(Boolean) : [];
    if (links.length) {
      partes.push(`<div class="det-meta det-links-leidos">Se leyó también: ${links.map((u) => escapeHtml(u)).join(' · ')}</div>`);
    }
    return partes.join('');
  }

  function renderRequisitos(cont, p) {
    const reqs = requisitosPost(p);
    if (!Array.isArray(reqs) || !reqs.length) return;
    const destino = cont.querySelector('[data-requisitos]') || cont;
    const motivos = motivosNoApto(p);
    const cerrado = ['ENVIADO', 'DESCARTADO'].includes(p.estado) || (p.estado === 'IGNORADA' && !motivos.length);
    reqs.filter((r) => r && typeof r === 'object').forEach((r) => {
      const fila = elemento('li', 'req');
      if (r.justificacion) {
        fila.setAttribute('title', r.justificacion);
        fila.title = r.justificacion;
      }
      // Contrato: createElement/setAttribute/textContent (sin innerHTML con datos).
      // El estado es un botón de ancho fijo (96 px) que cicla Match → Transferible → Brecha (radar.css: .req-estado)
      if (r.logistico) {
        const chip = elemento('span', 'req-estado', 'Condición');
        fila.appendChild(chip);
      } else {
        const [texto, clase] = propio(ESTILO_REQUISITO, r.estado, [r.estado || '', '']);
        const btnReq = elemento('button', ('req-estado ' + clase).trim());
        btnReq.type = 'button';
        btnReq.setAttribute('data-req', r.nombre || '');
        btnReq.textContent = texto;
        if (r.aprendido) {
          // Lo marcaste vos: queda aprendido (ícono con su explicación en el title del botón)
          btnReq.setAttribute('title', 'Lo marcaste vos: queda aprendido');
          btnReq.title = 'Lo marcaste vos: queda aprendido';
          const ico = icono('graduation-cap', 'ico-sm');
          if (ico) btnReq.appendChild(ico);
        }
        fila.appendChild(btnReq);
      }
      // .req-nombre (flex-basis 10rem): en el celular, «Brecha fuerte» y «Me descarta» bajan a otro renglón antes de
      // que el nombre quede tan angosto que parta palabras (overflow-wrap:anywhere queda solo para links largos)
      fila.appendChild(elemento('span', 'req-nombre', r.nombre || ''));

      if (r.dura && r.estado === 'BRECHA_REAL') {
        const marcaDura = elemento('span', 'chip chip-danger', 'Brecha fuerte');
        marcaDura.setAttribute('data-brecha-fuerte', '');
        if (r.justificacion) {
          marcaDura.setAttribute('title', r.justificacion);
          marcaDura.title = r.justificacion;
        }
        fila.appendChild(marcaDura);
      }

      const confirmado = motivos.some((m) => m.requisito === r.nombre);
      const marcado = r.eliminatorio_usuario === true || (r.eliminatorio_usuario !== false && confirmado);
      // «Me descarta» en las brechas excluyentes y en toda «Brecha fuerte» (creador, 15/11): manda la oferta a
      // Ignoradas y aprende el rubro para las próximas
      const visibleDescarte = !cerrado && (marcado || (r.estado === 'BRECHA_REAL'
        && ((!r.logistico && r.tipo === 'REQUISITO_EXCLUYENTE') || r.dura)));
      if (visibleDescarte) {
        // Marcado o no lo dice aria-pressed (radar.css lo pinta con el tono de error)
        const control = elemento('button', 'req-descarta', 'Me descarta');
        if (marcado) control.setAttribute('data-descarte-marca', '');
        control.type = 'button';
        control.setAttribute('data-descarte-req', r.nombre || '');
        control.setAttribute('aria-label', 'Me descarta: ' + String(r.nombre || ''));
        control.setAttribute('title', 'Manda la oferta a Ignoradas');
        control.title = 'Manda la oferta a Ignoradas';
        control.setAttribute('aria-pressed', String(marcado));
        control.addEventListener('click', () => ejecutar(control, async () => {
          control.textContent = marcado ? 'Restaurando…' : 'Descartando…';
          control.setAttribute('aria-busy', 'true');
          await marcarDescarte(r.nombre, !marcado);
        }));
        fila.appendChild(control);
      }
      destino.appendChild(fila);
    });
  }

  const NOMBRE_ETAPA = { analisis: 'Análisis', redactor: 'CV', auditoria: 'Auditoría', completar: 'Completar', mail: 'Mail' };

  /** "Análisis 42 s · CV 160 s · Auditoría 9 s · Mail 12 s" (tooltip: modelo, razonamiento y llamadas). */
  function htmlTiempos(p) {
    const etapas = [];
    const a = p.analisis && typeof p.analisis === 'object' ? p.analisis.etapa_analisis : null;
    if (a && typeof a === 'object') etapas.push(a);
    let tel = p.postulacion ? p.postulacion.telemetry : null;
    if (typeof tel === 'string') {
      try { tel = JSON.parse(tel); } catch (e) { tel = null; }
    }
    if (tel && Array.isArray(tel.etapas)) etapas.push(...tel.etapas.filter((e) => e && typeof e === 'object'));
    if (!etapas.length) return '';
    const items = etapas.map((e) => {
      const seg = Math.round(Number(e.segundos) || 0);
      const info = [e.modelo, e.effort, e.llamadas ? `${e.llamadas} llamada${e.llamadas === 1 ? '' : 's'}` : '', e.error || '']
        .filter(Boolean).join(' · ');
      const clase = e.error ? 'tiempo-error' : 'tiempo';
      return `<span class="${clase}" title="${escapeHtml(info)}">${escapeHtml(NOMBRE_ETAPA[e.etapa] || e.etapa || '')} ${seg} s</span>`;
    });
    return `<div class="det-tiempos">${svgIcono('clock')}${items.join('<span class="sep">·</span>')}</div>`;
  }

  /** Logo del portal y «Postular en el sitio» en el detalle (con createElement: datos del portal). */
  function extrasDePortal(cont, p) {
    if (!cont || !esDePortal(p) || typeof document.createElement !== 'function') return;
    const fila = document.createElement('div');
    fila.className = 'det-fila';
    fila.dataset.portalDetalle = p.fuente;
    const logo = logoPortal(p);
    if (logo) fila.appendChild(logo);
    const postular = botonPostularSitio(p);
    if (postular) fila.appendChild(postular);
    if (cont.firstChild) cont.insertBefore(fila, cont.firstChild); else cont.appendChild(fila);
  }

  function renderDetalle() {
    const cont = $('radarDetallePost');
    if (!cont) return;
    const p = S.detalle;
    if (!p) {
      cont.innerHTML = '<p class="vacio">Elegí un post de la lista para ver su detalle.</p>';
      return;
    }
    const t = p.triage && typeof p.triage === 'object' ? p.triage : {};
    const url = urlPublicacion(p.post_url);
    const perfil = urlSegura(p.url);
    const emails = (p.emails || []).map((e) =>
      `<span class="chip mono">${escapeHtml(e)}</span>`).join(' ');
    const matchPipeline = p.postulacion && p.postulacion.match_score !== undefined && p.postulacion.match_score !== null
      ? `<span class="chip num" title="Afinidad del CV">CV ${escapeHtml(Math.round(Number(p.postulacion.match_score)))}%</span>`
      : '';

    // CV general (15/11; «simple» en los datos): la IA de la selección decidió que el puesto no pide conocimientos
    // técnicos o no tiene que ver con el perfil; dice por qué
    let reporteCv = p.postulacion ? p.postulacion.reporte_auditoria : null;
    if (typeof reporteCv === 'string') { try { reporteCv = JSON.parse(reporteCv); } catch (_) { reporteCv = null; } }
    const chipSimple = reporteCv && reporteCv.tipo_cv === 'simple'
      ? `<span class="chip chip-info" title="${escapeHtml(reporteCv.motivo_tipo || '')}">CV general</span>`
      : '';

    const partes = [];
    partes.push(`
      <div class="det-cab">
        <div class="det-autor">
          <div class="det-nombre">${escapeHtml(p.autor || 'Autor desconocido')}</div>
          <div class="det-meta">${escapeHtml(p.cargo_autor || '')}</div>
          <div class="det-meta">${escapeHtml([p.fecha_rel, p.fuente === 'feed' ? 'Mi feed' : ''].filter(Boolean).join(' · '))}</div>
        </div>
        <div class="oferta-estados">${badgeEstado(p)}${subEstado(p)}</div>
      </div>`);

    const chips = [badgeMatch(p.match_score), matchPipeline, chipSimple].filter(Boolean).join(' ');
    const puestoEmpresa = [p.puesto, p.empresa].filter(Boolean).join(' · ');
    if (chips || puestoEmpresa) {
      partes.push(`<div data-score-detalle class="det-score">${chips}<span class="det-puesto">${escapeHtml(puestoEmpresa)}</span></div>`);
    }
    const enlaces = [];
    if (url) {
      enlaces.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="Ver la publicación en LinkedIn" class="enlace">${svgIcono('external-link')}Post</a>`);
    }
    if (perfil) {
      enlaces.push(`<a href="${escapeHtml(perfil)}" target="_blank" rel="noopener noreferrer" title="Perfil de quien publicó" class="enlace">${svgIcono('user')}Autor</a>`);
    }
    if (emails) enlaces.push(emails);
    // Vínculo a la vista de Postulaciones (solo si el post ya generó una; el id va numérico)
    const pidPostulacion = Number(p.postulacion_id);
    if (pidPostulacion > 0) {
      enlaces.push(`<button type="button" data-ver-postulacion="${pidPostulacion}" title="Abrir esta postulación en la vista Postulaciones" class="enlace">${svgIcono('folder')}Ver postulación</button>`);
    }
    partes.push(`<div class="det-enlaces">${enlaces.join('')}</div>`);
    const tiempos = htmlTiempos(p);
    if (tiempos) partes.push(tiempos);
    if (!esEmail(p)) {
      partes.push(bloque('Canal de postulación',
        `${escapeHtml(iconoCanal(p.canal))} ${escapeHtml(nombreCanal(p.canal))}` +
        (p.destino_canal ? `<br><span class="mono det-meta">${escapeHtml(p.destino_canal)}</span>` : '')));
    }

    if (p.estado === 'GENERANDO') {
      partes.push(bloque('Generando el CV', `<div id="radarProgresoDetalle">${htmlProgreso(p, true)}</div>`, 'info'));
    }
    const analisis = htmlAnalisis(p.analisis);
    if (analisis) partes.push(bloque('Análisis', analisis, 'ok'));
    if (t.motivo_descarte === 'manual') {
      partes.push(bloque('Descartado', 'Descartado manualmente. Podés analizarlo o generar el CV igual.'));
    }
    if (t.alcance) {
      const detalleAlcance = [etiquetaAlcance(t.alcance), t.pais].filter(Boolean).join(' · ');
      partes.push(`<div class="det-meta det-fila" title="${escapeHtml(t.alcance_motivo || '')}">${svgIcono('pin')}${escapeHtml(detalleAlcance)}</div>`);
    }
    if (t.instrucciones_postulacion) {
      partes.push(bloque('Cómo postularse', escapeHtml(t.instrucciones_postulacion), 'warn'));
    }
    if (p.error) partes.push(bloque('Error', escapeHtml(p.error), 'danger'));
    if (p.estado === 'ENVIADO') {
      const manual = String(p.message_id || '').startsWith('manual:');
      partes.push(bloque(manual ? 'Postulado (manual)' : 'Enviado',
        `${escapeHtml(formatearFecha(p.sent_at))}<br><span class="mono det-meta">${escapeHtml(p.message_id || '')}</span>`,
        'ok'));
    }
    partes.push(bloque('Post', htmlTextoPost(p)));

    cont.innerHTML = `<div class="det">${partes.join('')}</div>`;
    extrasDePortal(cont.firstElementChild || cont, p);
    const sello = selloNoApto(p, true);
    if (sello) (cont.querySelector('[data-score-detalle]') || cont).appendChild(sello);
    renderRequisitos(cont, p);
    if (S.detalleFallido === p.id && typeof p.texto !== 'string') {
      const reintentar = $('radarPostReintentar');
      if (reintentar) reintentar.onclick = () => cargarDetalle(p.id, true);
    }
    const textoImagen = p.texto_imagen || '';
    if (textoImagen.trim()) {
      const detalle = document.createElement('details');
      detalle.className = 'det-imagen';
      const resumen = document.createElement('summary');
      resumen.textContent = 'Leímos el texto de la imagen';
      const texto = document.createElement('pre');
      texto.className = 'texto-oferta';
      texto.textContent = textoImagen;
      detalle.append(resumen, texto);
      cont.append(detalle);
    }
    cont.scrollTop = 0;
  }

  // ------------------------------------------------------------------ email (columna derecha)

  const CAMPOS_EMAIL = ['radarEmailTo', 'radarEmailSubject', 'radarEmailBody'];

  function textoEstadoEmail(p) {
    if (!esEmail(p)) {
      switch (p.estado) {
        case 'LISTO': return 'Copiá el mensaje, postulate y tocá «Ya me postulé».';
        case 'ENVIADO': return etiquetaEstado(p).texto + (p.sent_at ? ' · ' + formatearFecha(p.sent_at) : '');
        default: break;
      }
    }
    switch (p.estado) {
      case 'LISTO':
        if (p.aprobado_at) return 'En cola de envío.';
        return String(p.email_to || '').trim() ? 'Revisá y enviá.' : 'Sin destinatario: elegí uno, o tocá «Ya me postulé» si te postulaste por otro lado.';
      case 'GENERANDO': return 'Generando…';
      case 'ENVIANDO': return 'Enviando…';
      case 'ENVIADO': return etiquetaEstado(p).texto + (p.sent_at ? ' · ' + formatearFecha(p.sent_at) : '');
      case 'DESCARTADO': return p.descarte && p.descarte.largo ? `Descartada: ${p.descarte.largo}.` : 'Descartada.';
      case 'REVISAR': return 'Ubicación desconocida.';
      case 'NUEVO': return 'Sin clasificar.';
      case 'OFERTA': return 'En cola de análisis.';
      case 'ANALIZANDO': return 'Analizando…';
      case 'ANALIZADA': return '';
      case 'ERROR': return p.postulacion_id ? 'Falló la generación o el envío.' : 'Falló el análisis.';
      default: return '';
    }
  }

  /** Destinatario: input libre (type=email) con los emails detectados como sugerencias (datalist). */
  function llenarDestinatarios(p) {
    const input = $('radarEmailTo');
    const lista = $('radarEmailToOpciones');
    if (lista) {
      lista.textContent = '';
      const vistos = [];
      [p.email_to, ...(p.emails || [])].forEach((e) => {
        if (e && !vistos.includes(e)) vistos.push(e);
      });
      vistos.forEach((e) => {
        const o = document.createElement('option');
        o.value = e;
        lista.appendChild(o);
      });
    }
    // Sin destinatario elegido el campo queda vacío (los detectados son sugerencias): aprobar exige
    // que el usuario elija o escriba uno, nunca se toma el primero en silencio
    input.value = p.email_to || '';
  }

  /** Muestra/oculta lo propio de cada canal: en email el destinatario y el asunto; en los demás el destino
   *  como texto, el mensaje editable y las acciones de copiar / descargar CV / abrir / marcar postulado. */
  function renderCanal(p, correo) {
    const ocultar = (id, oculto) => { const el = $(id); if (el) el.hidden = oculto; };
    ocultar('radarParaWrap', !correo);
    ocultar('radarAsuntoWrap', !correo);
    ocultar('radarDestinoCanalWrap', correo);
    const titulo = $('radarEmailTitulo');
    if (titulo) titulo.textContent = correo ? 'Mail de postulación' : 'Mensaje de postulación';
    const etCuerpo = $('radarCuerpoLabel');
    if (etCuerpo) etCuerpo.textContent = correo ? 'Escribir' : 'Mensaje';
    const destino = $('radarDestinoCanal');
    if (destino) {
      destino.textContent = `${iconoCanal(p.canal)} ${nombreCanal(p.canal)}` +
        (p.destino_canal ? ` · ${p.destino_canal}` : '');
    }
    const ac = accionesCanal(p);
    ocultar('radarCanalAcciones', !ac.panel);
    // `flex` no va en el markup: la utilidad de Tailwind pisaría el atributo `hidden`
    const panelAcciones = $('radarCanalAcciones');
    if (panelAcciones && panelAcciones.classList) panelAcciones.classList.toggle('flex', !!ac.panel);
    ocultar('radarCopiarMensajeBtn', !ac.copiar);
    ocultar('radarDescargarCvBtn', !ac.panel || !cvDisponible(p));
    ocultar('radarAbrirDestinoBtn', !ac.panel || !urlDestino(p, ''));
    ocultar('radarMarcarPostuladoBtn', !ac.marcarPostulado);
  }

  /** `forzar`: reescribe los campos aunque el usuario esté editando (cambio de post o de estado). */
  function renderEmail(forzar) {
    const vacio = $('radarEmailVacio');
    const panel = $('radarEmailPanel');
    const p = S.detalle;
    if (!panel || !vacio) return;
    if (!p) {
      vacio.hidden = false;
      panel.hidden = true;
      S.revisionVista = null;
      return;
    }
    vacio.hidden = true;
    panel.hidden = false;
    const acc = accionesPara(p);

    $('radarEmailEstado').textContent = textoEstadoEmail(p);
    $('radarEmailEstado').hidden = !textoEstadoEmail(p);
    const campos = $('radarCamposMensaje');
    if (campos) campos.hidden = !p.email_body && !['LISTO', 'ENVIANDO', 'ENVIADO'].includes(p.estado);

    const correo = esEmail(p);
    const editando = CAMPOS_EMAIL.some((id) => document.activeElement === $(id)) || !!S.edit;
    if (forzar || !editando) {
      if (correo) {
        llenarDestinatarios(p);
      } else {
        // `email_to` guarda el destino del canal (URL / número), no un email: nunca va al input
        $('radarEmailTo').value = '';
        const lista = $('radarEmailToOpciones');
        if (lista) lista.textContent = '';
      }
      $('radarEmailSubject').value = correo ? (p.email_subject || '') : '';
      $('radarEmailBody').value = p.email_body || '';
      S.revisionVista = { id: p.id, updated_at: p.updated_at };
    }
    renderVistaPrevia(correo ? ($('radarEmailBody').value || '') : '');
    CAMPOS_EMAIL.forEach((id) => { $(id).disabled = !acc.editable; });
    renderCanal(p, correo);

    // El CV en PDF y DOCX: en los mails y en toda postulación enviada (cualquier canal). En un canal sin
    // email todavía sin enviar, el CV se baja con el botón del panel del canal.
    const pdf = $('radarVerPdf');
    const docx = $('radarVerDocx');
    const pid = Number(p.postulacion_id);
    pdf.hidden = !(correo || p.estado === 'ENVIADO') || !cvDisponible(p);
    if (!pdf.hidden) {
      pdf.href = `/api/ofertas/${pid}/export/pdf`;
    }
    if (docx) {
      docx.hidden = pdf.hidden;
      if (!docx.hidden) docx.href = `/api/ofertas/${pid}/export/docx`;
    }

    $('radarAprobarBtn').hidden = !acc.aprobar;
    // Otro post seleccionado: la confirmación pendiente del anterior no aplica
    if (confirmaDescarte.id !== null && confirmaDescarte.id !== p.id) resetBotonDescartar();
    if (confirmaListo.id !== null && confirmaListo.id !== p.id) resetBotonListo();
    $('radarDescartarBtn').hidden = !acc.descartar;
    $('radarRegenerarBtn').hidden = !acc.regenerar;
    $('radarAnalizarBtn').hidden = !acc.analizar;
    iconoYTexto($('radarAnalizarBtn'), 'search', p.analisis || p.estado === 'ERROR' ? 'Volver a analizar' : 'Analizar ahora');
    $('radarGenerarCvBtn').hidden = !acc.generarCv;
    const postular = $('radarPostularIgualBtn');
    if (postular) postular.hidden = !acc.postularIgual;
    $('radarReintentarBtn').hidden = !acc.reintentar;
    $('radarAbrirEditorBtn').hidden = !acc.editor;
    if (forzar) $('radarGuardado').textContent = '';
  }

  // ------------------------------------------------------------------ edición con debounce

  const EMAIL_RE = /^[^@\s,;<>()]+@[^@\s,;<>()]+\.[^@\s,;<>()]+$/;

  function versionRevisada(post) {
    return S.revisionVista && S.revisionVista.id === post.id ? S.revisionVista.updated_at : post.updated_at;
  }

  function programarEdicion(campo, valor) {
    if (!S.detalle || !S.selId) return;
    // En canales sin email, `email_to` es el destino del canal y el asunto no se usa: no se editan
    if (!esEmail(S.detalle) && (campo === 'email_to' || campo === 'email_subject')) return;
    if (!S.edit || S.edit.postId !== S.selId) {
      S.edit = { postId: S.selId, updated_at: versionRevisada(S.detalle), campos: {}, timer: null };
    }
    S.edit.campos[campo] = valor;
    clearTimeout(S.edit.timer);
    // Un destinatario a medio escribir ("juan@") no se autoguarda (sería un 422 por tecla); queda
    // pendiente y se guarda al completarlo o antes de cualquier acción (donde un inválido la bloquea)
    const incompleto = campo === 'email_to' && String(valor || '').trim() && !EMAIL_RE.test(String(valor).trim());
    S.edit.timer = incompleto ? null : setTimeout(flushEdicion, MS_DEBOUNCE_EDICION);
    const g = $('radarGuardado');
    if (g) g.textContent = 'Editando…';
  }

  const CAMPO_DE_INPUT = { radarEmailTo: 'email_to', radarEmailSubject: 'email_subject', radarEmailBody: 'email_body' };

  /** Antes de aprobar: lo que muestra el formulario manda. Si algún campo difiere de lo guardado (o
   *  pendiente) — p. ej. Safari no disparó `change` en el destinatario — se programa su guardado, así
   *  el flush posterior lo envía y nunca se aprueba un texto distinto del que ve el usuario. */
  function sincronizarFormulario() {
    const p = S.detalle;
    if (!p || !S.selId || p.id !== S.selId || !accionesPara(p).editable) return;
    Object.keys(CAMPO_DE_INPUT).forEach((id) => {
      const el = $(id);
      if (!el || el.disabled || typeof el.value !== 'string') return;
      const campo = CAMPO_DE_INPUT[id];
      if (!esEmail(p) && campo !== 'email_body') return;
      const pendiente = S.edit && S.edit.postId === S.selId && campo in S.edit.campos;
      const actual = pendiente ? S.edit.campos[campo] : (p[campo] || '');
      if (el.value !== actual) programarEdicion(campo, el.value);
    });
  }

  /** Envía ya los cambios pendientes y espera la cola de guardado.
   *  Devuelve true solo si todo lo que el usuario editó quedó guardado en el servidor: si un PATCH
   *  falló (red, 5xx, 422) los cambios vuelven a quedar pendientes y devuelve false, para que
   *  aprobar/regenerar/descartar no actúen sobre el texto viejo mientras el formulario muestra otro. */
  async function flushEdicion() {
    const e = S.edit;
    let ok = true;
    if (e) {
      clearTimeout(e.timer);
      S.edit = null;
      const guardado = colaGuardado.then(() => guardarCampos(e));
      colaGuardado = guardado.catch(() => false);
      ok = await guardado;
    } else {
      await colaGuardado;
    }
    return ok && !S.edit;
  }

  /** Guarda los cambios de un email o mensaje. true si quedó guardado (o no había nada que guardar). */
  async function guardarCampos(e) {
    if (!Object.keys(e.campos).length) return true;
    const r = await api('PATCH', `/posts/${e.postId}`, { ...e.campos, updated_at: e.updated_at });
    const g = $('radarGuardado');
    if (r.ok && r.data) {
      guardarPost(r.data);
      if (S.detalle && S.detalle.id === e.postId) S.detalle = { ...S.detalle, ...normalizar(r.data) };
      if (S.selId === e.postId) {
        S.revisionVista = { id: e.postId, updated_at: r.data.updated_at };
        // Un segundo guardado propio puede haberse programado durante el primero.
        if (S.edit && S.edit.postId === e.postId && S.edit.updated_at === e.updated_at) {
          S.edit.updated_at = r.data.updated_at;
        }
      }
      if (g) g.textContent = S.selId === e.postId ? 'Guardado' : '';
      return true;
    }
    const post = S.detalle && S.detalle.id === e.postId ? S.detalle : S.posts.get(e.postId);
    const que = esEmail(post) ? 'el mail' : 'el mensaje';
    mostrarMensaje(`No se pudo guardar ${que}: ${mensajeDeDetalle(r.detail)}`, 'error');
    if (r.status === 409 || r.status === 404) {
      // El post ya no es editable: se recarga lo que hay en el servidor (el formulario deja de mentir)
      if (g) g.textContent = '';
      await cargarDetalle(e.postId, true);
    } else if (e.postId === S.selId) {
      // Fallo transitorio: los cambios siguen pendientes (y visibles en el formulario) para reintentar
      S.edit = { postId: e.postId, updated_at: e.updated_at, campos: { ...e.campos, ...(S.edit ? S.edit.campos : {}) }, timer: null };
      if (g) g.textContent = 'Sin guardar';
    }
    return false;
  }

  /** Guarda lo pendiente antes de una acción; si no se pudo, la acción no debe ejecutarse. */
  async function guardadoOk(accion) {
    if (await flushEdicion()) return true;
    const que = esEmail(S.detalle) ? 'del mail' : 'del mensaje';
    mostrarMensaje(`No se ${accion}: no se pudieron guardar tus cambios ${que}. Revisá la conexión y reintentá.`, 'error');
    return false;
  }

  // ------------------------------------------------------------------ carga de datos

  async function cargarEstado() {
    const r = await api('GET', '/status');
    if (r.ok && r.data) onStatus(r.data);
    return r;
  }

  async function cargarPosts() {
    const genModo = S.genModo;
    // Con dos cargas en vuelo, solo vale la última pedida: una respuesta vieja que llega tarde borraría lo nuevo
    // (creador, 19/11: tras «Varios links» quedaba 1 oferta en vez de 6)
    const turno = S.turnoLista = (S.turnoLista || 0) + 1;
    const ruta = S.modo === 'manual'
      ? `/posts?limite_descartados=${LIMITE_DESCARTADOS}&fuente=manual`
      : (S.modo === 'portales'
        ? `/posts?limite_descartados=${LIMITE_DESCARTADOS}&fuente=portales`
        : `/posts?limite_descartados=${LIMITE_DESCARTADOS}`);
    const r = await api('GET', ruta);
    if (genModo !== S.genModo || turno !== S.turnoLista) return;
    if (!r.ok || !Array.isArray(r.data)) {
      mostrarMensaje(`No se pudieron cargar los posts: ${mensajeDeDetalle(r.detail)}`, 'error');
      return;
    }
    const posts = r.data.filter((p) => perteneceAlModo(p));
    const ids = new Set(posts.map((p) => p.id));
    S.posts.forEach((_, id) => { if (!ids.has(id)) S.posts.delete(id); });
    posts.forEach((p) => guardarPost(p));
    renderLista();
    if (S.selId && S.posts.has(S.selId)) marcarSeleccion();
  }

  /** Trae el detalle completo; `forzar` reescribe el formulario aunque haya foco. */
  async function cargarDetalle(id, forzar) {
    const genModo = S.genModo;
    const token = ++S.tokenDetalle;
    const r = await api('GET', `/posts/${id}`);
    if (genModo !== S.genModo || token !== S.tokenDetalle || id !== S.selId) return;
    if (!r.ok || !r.data) {
      mostrarMensaje(`No se pudo cargar el post: ${mensajeDeDetalle(r.detail)}`, 'error');
      // El detalle sigue mostrando el extracto, ahora con «Reintentar» en lugar de un bloque vacío.
      S.detalleFallido = id;
      if (S.detalle && S.detalle.id === id) renderDetalle();
      return;
    }
    if (S.detalleFallido === id) S.detalleFallido = null;
    aplicarDetalle(r.data, forzar);
  }

  /** Aplica un post completo (de GET o de una acción) a la lista y, si está abierto, a las columnas. */
  function aplicarDetalle(data, forzar) {
    const previo = S.detalle;
    const nuevo = guardarPost(data);
    if (!nuevo) return;
    upsertCard(nuevo);
    if (data.id !== S.selId) return;
    const cambioForma = !previo || previo.id !== data.id || previo.estado !== data.estado ||
      (previo.aprobado_at || null) !== (data.aprobado_at || null);
    S.detalle = { ...nuevo, postulacion: data.postulacion };
    renderDetalle();
    renderEmail(!!forzar || cambioForma);
  }

  async function seleccionar(id) {
    const genModo = S.genModo;
    if (id === S.selId && S.detalle) {
      if (window.innerWidth < 1024) setTab('detalle');
      return;
    }
    await flushEdicion();
    if (genModo !== S.genModo) return;
    S.selId = id;
    S.detalle = null;
    marcarSeleccion();
    // Muestra enseguida lo que ya se sabe del resumen mientras llega el detalle
    const resumen = S.posts.get(id);
    if (resumen) {
      S.detalle = { ...resumen };
      renderDetalle();
      renderEmail(true);
    }
    if (window.innerWidth < 1024) setTab('detalle');
    await cargarDetalle(id, true);
  }

  // ------------------------------------------------------------------ acciones sobre el post

  async function ejecutar(btn, fn) {
    if (btn) btn.disabled = true;
    try {
      await fn();
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /** Respuesta de una acción que devuelve el post actualizado. */
  async function resolverAccion(r, exito, accion) {
    if (r.ok && r.data) {
      aplicarDetalle(r.data, true);
      if (exito) mostrarMensaje(exito, 'ok', undefined, accion);
      return true;
    }
    mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
    if (S.selId) await cargarDetalle(S.selId, true);
    return false;
  }

  async function aprobar(confirmarDuplicado) {
    const id = S.selId;
    if (!id) return;
    if (!esEmail(S.detalle)) {
      mostrarMensaje('Este canal no se envía por Gmail: copiá el mensaje, postulate y tocá «Ya me postulé».', 'error');
      return;
    }
    sincronizarFormulario();
    if (!(await guardadoOk('aprobó'))) return;
    const r = await api('POST', `/posts/${id}/approve`, {
      updated_at: S.detalle && versionRevisada(S.detalle),
      confirm_duplicate: !!confirmarDuplicado,
    });
    if (r.ok && r.data) {
      aplicarDetalle(r.data, true);
      mostrarMensaje('Aprobado: el mail queda en cola y se envía con el ritmo del Radar.', 'ok');
      return;
    }
    if (r.status === 400) {
      mostrarAvisoGmail(mensajeDeDetalle(r.detail));
      return;
    }
    const d = r.detail;
    if (r.status === 409 && d && typeof d === 'object' && d.duplicate) {
      const email = d.email || 'este destinatario';
      if (!confirmarDuplicado && window.confirm(`Ya se envió (o está por enviarse) un email a ${email} en los últimos 30 días.\n¿Enviar de todos modos?`)) {
        await aprobar(true);
      }
      return;
    }
    mostrarMensaje(mensajeDeDetalle(d), 'error');
    if (r.status === 409) await cargarDetalle(id, true);
  }

  // Descartar en 2 pasos sobre el mismo botón (como en Postulaciones): el primer clic pide
  // confirmación por 3,5 s; el segundo descarta. Sin popups ni modales.
  const MS_CONFIRMAR_DESCARTE = 3500;
  const confirmaDescarte = { id: null, timer: null, clase: '', html: '' };

  function resetBotonDescartar() {
    const btn = $('radarDescartarBtn');
    clearTimeout(confirmaDescarte.timer);
    confirmaDescarte.timer = null;
    if (btn && confirmaDescarte.id !== null) {
      btn.className = confirmaDescarte.clase;
      btn.innerHTML = confirmaDescarte.html;
    }
    confirmaDescarte.id = null;
  }

  async function descartar() {
    const id = S.selId;
    const btn = $('radarDescartarBtn');
    if (!id) return;
    if (confirmaDescarte.id !== id) {
      resetBotonDescartar();
      confirmaDescarte.id = id;
      confirmaDescarte.clase = btn.className;
      confirmaDescarte.html = btn.innerHTML;
      btn.className = 'btn btn-borde-danger';
      btn.innerHTML = svgIcono('triangle-alert') + '<span>¿Confirmar descarte?</span>';
      confirmaDescarte.timer = setTimeout(resetBotonDescartar, MS_CONFIRMAR_DESCARTE);
      return;
    }
    resetBotonDescartar();
    if (!(await guardadoOk('descartó'))) return;
    // Si ya estaba descartada no hay a qué volver: «Deshacer» fallaría
    const yaDescartada = [S.detalle, S.posts.get(id)].some((p) => p && p.id === id && p.estado === 'DESCARTADO');
    await resolverAccion(
      await api('POST', `/posts/${id}/discard`, { desde: 'detalle' }),
      'Oferta descartada.',
      yaDescartada ? undefined : { texto: 'Deshacer', alClick: () => restaurar(id) }
    );
  }

  /** «Deshacer» tras descartar: devuelve el post a su estado anterior. */
  async function restaurar(id) {
    const r = await api('POST', `/posts/${id}/restaurar`);
    if (r.ok && r.data) {
      aplicarDetalle(r.data, true);
      mostrarMensaje('Oferta restaurada.', 'ok');
    } else {
      mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
    }
  }

  async function regenerar() {
    const id = S.selId;
    if (!id) return;
    if (!(await guardadoOk('regeneró'))) return;
    // CV desde el maestro: siempre se rehacen CV y mail
    await resolverAccion(await api('POST', `/posts/${id}/regenerate`, { cv: true }), 'Regenerando CV y email…');
  }

  async function analizarPost(id) {
    if (!id) return;
    const r = await api('POST', `/posts/${id}/analizar`);
    if (r.ok && r.data) {
      guardarPost(r.data);
      upsertCard(S.posts.get(id));
      if (id === S.selId) aplicarDetalle(r.data, true);
      mostrarMensaje('Primera en la cola de análisis.', 'ok');
      return;
    }
    mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
  }

  async function generarCvPost(id) {
    if (!id) return;
    const r = await api('POST', `/posts/${id}/generar-cv`);
    if (r.ok && r.data) {
      guardarPost(r.data);
      upsertCard(S.posts.get(id));
      if (id === S.selId) aplicarDetalle(r.data, true);
      mostrarMensaje('CV en cola: se genera en segundo plano (de a uno). Podés seguir navegando.', 'ok');
      return;
    }
    mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
  }

  async function postularIgual(id) {
    const post = id === S.selId ? S.detalle : S.posts.get(id);
    if (!puedePostularIgual(post)) return;
    if (!(await guardadoOk('postuló'))) return;
    await resolverAccion(await api('POST', `/posts/${id}/force`), 'Postulación priorizada. Revisá el contenido antes de aprobar.');
  }

  async function descartarPost(id) {
    if (S.selId !== id) await seleccionar(id);
    if (S.selId === id) await descartar();
  }

  async function reintentar() {
    const id = S.selId;
    if (!id) return;
    const error = (S.detalle && S.detalle.error) || '';
    if (envioDudoso(error) && !window.confirm(
      'Este email puede haberse enviado ya: el envío se cortó a mitad de camino.\n' +
      'Revisá la carpeta Enviados de Gmail antes de continuar. ¿Reintentar de todos modos?')) return;
    await resolverAccion(await api('POST', `/posts/${id}/retry`), 'Reintentando…');
  }

  /** Copia el texto actual del mensaje (tras guardar lo pendiente) al portapapeles. */
  /** Vista previa del mail como lo recibe el reclutador: el teléfono de la firma linkeado a wa.me y la
   *  línea «LinkedIn» al perfil (lo mismo que arma `email_html` al enviar). Solo DOM, sin innerHTML. */
  function renderVistaPrevia(texto) {
    const caja = $('radarEmailPreviewBox');
    const destino = $('radarEmailPreview');
    if (!caja || !destino) return;
    destino.textContent = '';
    caja.hidden = !texto;
    if (!texto) return;
    const f = S.estado.firma || {};
    const sinBarra = (u) => String(u || '').trim().toLowerCase().replace(/^https?:\/\/(www\.)?/, '').replace(/\/+$/, '');
    const link = (href, etiqueta) => {
      const a = document.createElement('a');
      a.href = href;
      a.textContent = etiqueta;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.className = 'enlace';
      return a;
    };
    texto.split('\n').forEach((linea, i) => {
      if (i) destino.appendChild(document.createTextNode('\n'));
      const limpia = linea.trim();
      const esUrl = limpia.startsWith('http');
      if (f.telefono && (limpia === f.telefono || (esUrl && sinBarra(limpia) === sinBarra(f.wa_me)))) {
        destino.appendChild(link(f.wa_me, f.telefono));
      } else if (f.linkedin && (limpia.toLowerCase() === String(f.etiqueta || 'LinkedIn').toLowerCase()
                 || (esUrl && sinBarra(limpia) === sinBarra(f.linkedin)))) {
        destino.appendChild(link(f.linkedin, f.etiqueta || 'LinkedIn'));
      } else {
        destino.appendChild(document.createTextNode(linea));
      }
    });
  }

  async function copiarMensaje() {
    await flushEdicion();
    const texto = ($('radarEmailBody') || {}).value || '';
    if (!texto) {
      mostrarMensaje('No hay mensaje para copiar.', 'error');
      return;
    }
    let ok = false;
    try {
      await navigator.clipboard.writeText(texto);
      ok = true;
    } catch (_) {
      // Sin permiso de portapapeles (http o navegador estricto): copia con un textarea temporal
      const ta = document.createElement('textarea');
      ta.value = texto;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand('copy'); } catch (_e) { ok = false; }
      ta.remove();
    }
    mostrarMensaje(ok ? 'Mensaje copiado al portapapeles.' : 'No se pudo copiar: seleccioná el texto y copialo a mano.', ok ? 'ok' : 'error');
  }

  function descargarCv() {
    const p = S.detalle;
    if (!p || !cvDisponible(p)) return;
    descargarPdf(p.postulacion_id);
  }

  /** Descarga el PDF del CV con fetch y un link temporal (creador, 08/11: con un link o una pestaña nueva el
   *  navegador a veces cancelaba la descarga, NS_BINDING_CANCELLED_OLD_LOAD, porque el PDF tarda en armarse y
   *  otra carga o un re-render la pisaba). Avisa mientras se genera y si falla. */
  async function descargarPdf(postulacionId, ev) {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    const id = Number(postulacionId);
    if (!id || S.descargandoPdf === id) return;
    S.descargandoPdf = id;
    mostrarMensaje('Generando el PDF del CV…', 'ok');
    try {
      const r = await fetch(`/api/ofertas/${id}/export/pdf`);
      if (!r.ok) throw new Error(r.status === 404 ? 'el CV no está disponible' : `error ${r.status}`);
      const blob = await r.blob();
      const m = /filename="?([^";]+)"?/i.exec(r.headers.get('Content-Disposition') || '');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = m ? m[1] : 'CV.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      ocultarMensaje();
    } catch (e) {
      mostrarMensaje('No se pudo descargar el CV: ' + (e && e.message ? e.message : 'falló la conexión'), 'error');
    } finally {
      S.descargandoPdf = null;
    }
  }

  /** Abre el destino del canal (link / WhatsApp con el mensaje / perfil de LinkedIn); no envía nada. */
  function abrirDestino() {
    const p = S.detalle;
    if (!p) return;
    // La ventana se abre en el mismo gesto del clic (Safari bloquea popups tras un await) con el texto
    // que muestra el formulario; recién después se guardan en segundo plano las ediciones pendientes.
    const url = urlDestino(p, ($('radarEmailBody') || {}).value || '');
    if (!url) {
      mostrarMensaje('Este post no tiene un destino seguro para abrir.', 'error');
      return;
    }
    window.open(url, '_blank', 'noopener,noreferrer');
    sincronizarFormulario();
    flushEdicion().catch(() => {});
  }

  /** Botón «Ya me postulé»: el usuario se postuló por su cuenta; queda registrado (nunca envía nada). Como
   *  Descartar, el primer clic pide confirmación en el mismo botón. */
  const confirmaListo = { id: null, timer: null, texto: '' };

  function resetBotonListo() {
    const btn = $('radarMarcarPostuladoBtn');
    clearTimeout(confirmaListo.timer);
    if (btn && confirmaListo.id !== null) btn.textContent = confirmaListo.texto;
    confirmaListo.id = null;
  }

  async function marcarPostulado() {
    const id = S.selId;
    if (!id || !S.detalle || !accionesCanal(S.detalle).marcarPostulado) return;
    const btn = $('radarMarcarPostuladoBtn');
    if (confirmaListo.id !== id) {
      resetBotonListo();
      confirmaListo.id = id;
      if (btn) {
        confirmaListo.texto = btn.textContent;
        btn.textContent = '¿Confirmás? Tocá de nuevo';
      }
      confirmaListo.timer = setTimeout(resetBotonListo, MS_CONFIRMAR_DESCARTE);
      return;
    }
    resetBotonListo();
    if (!(await guardadoOk('marcó como postulado'))) return;
    await resolverAccion(await api('POST', `/posts/${id}/marcar-postulado`), 'Listo: quedó como postulada.');
  }

  /** Un toque en el estado de un requisito: brecha → transferible → coincidencia. Queda aprendido. */
  async function ciclarRequisito(nombre) {
    const id = S.selId;
    if (!id || !nombre) return;
    const post = S.detalle || S.posts.get(id);
    const reqs = requisitosPost(post);
    const req = Array.isArray(reqs) && reqs.find((r) => r && r.nombre === nombre);
    if (req && req.logistico) return;
    await resolverAccion(await api('POST', `/posts/${id}/requisito`, { nombre }), '');
  }

  async function marcarDescarte(nombre, eliminatorio) {
    const id = S.selId;
    if (!id || !nombre || typeof eliminatorio !== 'boolean') return;
    await resolverAccion(await api('POST', `/posts/${id}/requisito`, { nombre, eliminatorio }), '');
  }

  function abrirEditor() {
    const pid = S.detalle && S.detalle.postulacion_id;
    if (!pid) return;
    // La lista manual no incluye las postulaciones del radar: seleccionarOferta carga por id
    if (typeof cambiarVista === 'function') cambiarVista('editor');
    if (typeof seleccionarOferta === 'function') seleccionarOferta(Number(pid), true);
  }

  function etiquetaPost(id) {
    const p = S.posts.get(id);
    if (!p) return `#${id}`;
    return [p.autor, p.puesto].filter(Boolean).join(' · ') || `#${id}`;
  }

  // ---- Revisión previa de «Aprobar todos»: una fila por email listo, con casilla

  /** Lo que se muestra de un post en la revisión previa, y si arranca marcado. Tolera campos ausentes. */
  function datosAprobacion(post) {
    const p = normalizar(post);
    const alcance = ETIQUETA_ALCANCE[p.alcance] ? p.alcance : 'DESCONOCIDO';
    const umbral = Number(S.estado.umbral_match);
    const afinidad = p.match_score === undefined || p.match_score === null || p.match_score === '' ? NaN : Number(p.match_score);
    const alertas = [];
    if (alcance === 'EXTERIOR') alertas.push('Exterior');
    if (alcance === 'DESCONOCIDO') alertas.push('Sin ubicación');
    if (Number.isFinite(afinidad) && Number.isFinite(umbral) && afinidad < umbral) alertas.push('Afinidad bajo el mínimo');
    if (p.oferta_cerrada) alertas.push('Oferta cerrada');
    return {
      id: p.id,
      titulo: [p.puesto, p.empresa].filter(Boolean).join(' · ') || etiquetaPost(p.id),
      destinatario: p.email_to || '',
      ubicacion: ETIQUETA_ALCANCE[alcance],
      afinidad: Number.isFinite(afinidad) ? Math.round(afinidad) : null,
      alertas,
      marcada: alcance !== 'EXTERIOR' && alcance !== 'DESCONOCIDO',
    };
  }

  function elemento(tag, clase, texto) {
    const el = document.createElement(tag);
    if (clase) el.className = clase;
    if (texto !== undefined) el.textContent = texto;
    return el;
  }

  function filaAprobacion(d) {
    const fila = elemento('label', 'aprobar-fila');
    const check = elemento('input', 'aprobar-check');
    check.type = 'checkbox';
    check.checked = d.marcada;
    fila.appendChild(check);
    const cuerpo = elemento('div', 'crece');
    cuerpo.appendChild(elemento('div', 'aprobar-titulo', d.titulo));
    const detalle = elemento('div', 'aprobar-detalle');
    detalle.appendChild(elemento('span', 'mono-corte', d.destinatario || 'Sin destinatario'));
    detalle.appendChild(elemento('span', 'chip', d.ubicacion));
    if (d.afinidad !== null) detalle.appendChild(elemento('span', 'num', 'Afinidad ' + d.afinidad + '%'));
    cuerpo.appendChild(detalle);
    if (d.alertas.length) cuerpo.appendChild(elemento('div', 'aprobar-alerta', d.alertas.join(' · ')));
    fila.appendChild(cuerpo);
    return { id: d.id, check, fila };
  }

  function idsMarcados() {
    return S.aprobar ? S.aprobar.filas.filter((f) => f.check.checked).map((f) => f.id) : [];
  }

  function actualizarConteoAprobar() {
    const n = idsMarcados().length;
    const btn = $('radarAprobarConfirmarBtn');
    btn.textContent = `Aprobar ${n}`;
    btn.disabled = n === 0;
  }

  /** Abre la revisión previa; resuelve con los ids marcados, o null si se cancela. */
  function pedirAprobacion(listos) {
    if (S.aprobar) return Promise.resolve(null);
    const lista = $('radarAprobarLista');
    lista.textContent = '';
    const filas = listos.map((p) => filaAprobacion(datosAprobacion(p)));
    filas.forEach((f) => lista.appendChild(f.fila));
    return new Promise((resolver) => {
      S.aprobar = { filas, resolver, foco: document.activeElement || null, abiertoEn: Date.now(), fondoPulsado: false };
      actualizarConteoAprobar();
      $('radarAprobarModal').hidden = false;
      const btn = $('radarAprobarConfirmarBtn');
      if (typeof btn.focus === 'function') btn.focus();
    });
  }

  // Clic en el fondo oscuro del modal: cierra solo si el clic entero (apretar y soltar) fue en el fondo y
  // no justo al abrir (el 2.º clic de un doble clic en «Aprobar…» cae ahí y no es para cerrar).
  const MS_SIN_CERRAR_AL_ABRIR = 400;

  function pulsarFondoAprobar(e) {
    if (S.aprobar) S.aprobar.fondoPulsado = e.target === $('radarAprobarModal');
  }

  function clicFondoAprobar(e) {
    const estado = S.aprobar;
    if (!estado) return;
    const empezoAca = estado.fondoPulsado;
    estado.fondoPulsado = false;
    if (e.target !== $('radarAprobarModal') || !empezoAca) return;
    if (Date.now() - estado.abiertoEn < MS_SIN_CERRAR_AL_ABRIR) return;
    cerrarAprobar(false);
  }

  function cerrarAprobar(confirmado) {
    const estado = S.aprobar;
    if (!estado) return;
    const ids = confirmado ? idsMarcados() : null;
    if (confirmado && !ids.length) return;
    S.aprobar = null;
    $('radarAprobarModal').hidden = true;
    if (estado.foco && typeof estado.foco.focus === 'function') estado.foco.focus();
    estado.resolver(ids);
  }

  // ---- Ofertas omitidas por duplicado: «Enviar igual» / «Descartar» en la fila del mensaje

  const CLASE_BTN_FILA = 'btn btn-sm';

  /** "DD/MM" de un "YYYY-MM-DD HH:MM:SS", o '' si no se puede leer. */
  function diaMes(str) {
    const d = parseFecha(str);
    return d ? `${dos(d.getDate())}/${dos(d.getMonth() + 1)}` : '';
  }

  /** «<Puesto · Empresa>: ya le escribiste a <email> el DD/MM por «<puesto previo>»» (o «ya está en cola…»). */
  function textoDuplicado(o) {
    const p = S.posts.get(o.id);
    const titulo = p ? datosAprobacion(p).titulo : etiquetaPost(o.id);
    const email = o.email || 'ese destinatario';
    const previo = o.previo;
    if (!previo) return `${titulo}: ya le escribiste a ${email}`;
    const nombre = previo.puesto || previo.empresa || 'otra oferta';
    if (previo.sent_at) {
      const dia = diaMes(previo.sent_at);
      return `${titulo}: ya le escribiste a ${email}${dia ? ' el ' + dia : ''} por «${nombre}»`;
    }
    return `${titulo}: ya está en cola para ${email} por «${nombre}»`;
  }

  /** Fila del resultado de «Aprobar todos» para un duplicado. `visto`: el updated_at con el que se aprobó
   *  en lote; `alResolver()` avisa que la fila quedó resuelta (para soltar el mensaje fijo). */
  function filaDuplicado(o, visto, alResolver) {
    const id = o.id;
    const fila = elemento('div', 'proc-acciones');
    fila.appendChild(elemento('span', 'min-w-0 break-words', textoDuplicado(o)));
    const acciones = elemento('span', 'flex items-center gap-2 flex-shrink-0');
    const enviar = elemento('button', CLASE_BTN_FILA, 'Enviar igual');
    const descartarBtn = elemento('button', CLASE_BTN_FILA, 'Descartar');
    enviar.type = 'button';
    descartarBtn.type = 'button';
    const resultado = elemento('span', 'font-semibold');
    acciones.appendChild(enviar);
    acciones.appendChild(descartarBtn);
    acciones.appendChild(resultado);
    fila.appendChild(acciones);

    let ocupada = false;
    let resuelta = false;
    const bloquear = (si) => { enviar.disabled = si; descartarBtn.disabled = si; };
    const terminar = (texto, ok) => {
      resultado.textContent = texto;
      resultado.className = 'resultado ' + (ok ? 'resultado-ok' : 'resultado-error');
      if (ok) {
        bloquear(true);
        if (!resuelta) { resuelta = true; alResolver(); }
      } else {
        bloquear(false);
      }
    };
    // Una sola acción a la vez por fila: el segundo clic (o el del otro botón) no hace nada mientras tanto
    const correr = async (fn) => {
      if (ocupada || resuelta) return;
      ocupada = true;
      bloquear(true);
      try {
        await fn();
      } finally {
        ocupada = false;
      }
    };

    enviar.addEventListener('click', () => correr(async () => {
      // El clic ya es la confirmación explícita, pero solo vale para lo que se revisó: si la oferta
      // cambió desde entonces (o hay un cambio sin guardar), hay que abrirla y aprobarla de nuevo.
      const actual = S.posts.get(id);
      const cambio = actual && visto && String(actual.updated_at || '') !== String(visto);
      if (cambio || (S.edit && S.edit.postId === id)) {
        terminar('Cambió desde que la revisaste: abrila y aprobala de nuevo', false);
        return;
      }
      // `updated_at`: el servidor también rechaza si cambió desde que se revisó (otra pestaña, otro equipo)
      const r = await api('POST', `/posts/${id}/approve`, { confirm_duplicate: true, updated_at: visto || null });
      if (r.ok && r.data) {
        aplicarDetalle(r.data, true);
        terminar('Encolado', true);
        return;
      }
      if (r.status === 400) mostrarAvisoGmail(mensajeDeDetalle(r.detail));
      terminar(mensajeDeDetalle(r.detail), false);
    }));

    // Descartar es irreversible para el envío: como en el detalle, el primer clic pide confirmación
    let confirmar = null;
    descartarBtn.addEventListener('click', () => {
      if (ocupada || resuelta) return;
      if (!confirmar) {
        descartarBtn.textContent = '¿Confirmar descarte?';
        confirmar = setTimeout(() => { confirmar = null; descartarBtn.textContent = 'Descartar'; }, MS_CONFIRMAR_DESCARTE);
        return;
      }
      clearTimeout(confirmar);
      confirmar = null;
      descartarBtn.textContent = 'Descartar';
      return descartarConfirmado();
    });
    const descartarConfirmado = () => correr(async () => {
      const r = await api('POST', `/posts/${id}/discard`, { desde: 'duplicado' });
      if (r.ok && r.data) {
        aplicarDetalle(r.data, true);
        terminar('Descartado', true);
        return;
      }
      terminar(mensajeDeDetalle(r.detail), false);
    });
    return fila;
  }

  async function aprobarTodos() {
    const listos = listosSinAprobar();
    const n = listos.length;
    if (!n) return;
    const elegidos = await pedirAprobacion(listos);
    if (!elegidos || !elegidos.length) return;
    // Solo se aprueba lo que el usuario marcó, y en la versión que vio (el servidor omite lo que cambió)
    const vistos = new Map(listos.filter((p) => elegidos.includes(p.id)).map((p) => [p.id, p.updated_at || null]));
    sincronizarFormulario();
    const editado = S.edit ? S.edit.postId : null;
    if (!(await guardadoOk('aprobaron los emails'))) return;
    if (editado !== null && vistos.has(editado)) {
      // Su propia edición recién guardada cambió el updated_at de ese post: se toma el nuevo
      const p = S.posts.get(editado);
      vistos.set(editado, (p && p.updated_at) || null);
    }
    const updatedAt = {};
    vistos.forEach((v, k) => { updatedAt[k] = v; });
    const r = await api('POST', '/approve-all', { ids: Array.from(vistos.keys()), updated_at: updatedAt });
    if (r.status === 400) {
      mostrarAvisoGmail(mensajeDeDetalle(r.detail));
      return;
    }
    if (!r.ok || !r.data) {
      mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
      return;
    }
    const enc = r.data.encolados || [];
    const omi = r.data.omitidos || [];
    // Los duplicados se resuelven ahí mismo; el resto es solo texto (textContent: sin HTML)
    const dups = omi.filter((o) => o && o.tipo === 'duplicado');
    const lineas = omi.filter((o) => !dups.includes(o)).map((o) => 'Omitido ' + etiquetaPost(o.id) + ': ' + o.motivo);
    let pendientes = dups.length;
    let idMsg = 0;
    const filas = dups.map((o) => filaDuplicado(o, vistos.get(o.id), () => {
      pendientes -= 1;
      if (pendientes === 0) soltarMensajeFijo(idMsg);
    }));
    idMsg = mostrarMensaje(
      `Encolados: ${enc.length} · Omitidos: ${omi.length}`,
      omi.length && !enc.length ? 'error' : 'ok',
      lineas,
      undefined,
      { filas, fijo: filas.length > 0 }
    );
    await cargarPosts();
    if (S.selId) await cargarDetalle(S.selId, true);
  }

  // ------------------------------------------------------------------ control del radar

  async function alternarRadar() {
    const btn = $('radarToggleBtn');
    if (S.estado.desired_running) {
      S.deteniendo = true;
      renderControl();
      clearTimeout(timerDetener);
      const r = await api('POST', '/stop');
      if (!r.ok) {
        S.deteniendo = false;
        mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
        renderControl();
        return;
      }
      if (r.data) onStatus(r.data);
      // El POST responde ya; DETENIDO llega por SSE. Si no llega, se consulta el estado real.
      if (S.deteniendo) {
        timerDetener = setTimeout(async () => {
          await cargarEstado();
          S.deteniendo = false;
          renderControl();
        }, MS_FALLBACK_DETENER);
      }
      return;
    }
    await ejecutar(btn, async () => {
      const r = await api('POST', '/start');
      if (r.ok && r.data) onStatus(r.data);
      else mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
    });
  }

  async function mostrarNavegador() {
    await ejecutar($('radarMostrarNavegadorBtn'), async () => {
      const r = await api('POST', '/show-browser');
      if (r.ok && r.data) onStatus(r.data);
      else mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
    });
  }

  async function reintentarRadar() {
    await ejecutar($('radarReintentarAhoraBtn'), async () => {
      const r = await api('POST', '/reintentar');
      if (r.ok && r.data) onStatus(r.data);
      else mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
    });
  }

  // ------------------------------------------------------------------ uso desde el celular

  /** Solo la PC recibe el QR de emparejamiento (desde otro equipo el GET da 403 y el botón no aparece). */
  async function prepararCelular() {
    const btn = $('radarCelularBtn');
    if (!btn) return;
    const r = await api('GET', '/acceso-celular');
    btn.hidden = !(r.ok && r.data);
    if (r.ok && r.data) pintarCelular(r.data);
  }

  function pintarCelular(d) {
    const qr = $('radarCelularQr');
    const url = $('radarCelularUrl');
    if (url) url.textContent = d.url || '';
    if (!qr || typeof DOMParser === 'undefined') return;
    const svg = new DOMParser().parseFromString(d.qr_svg || '', 'image/svg+xml').documentElement;
    if (!svg || svg.nodeName.toLowerCase() !== 'svg') return;
    svg.removeAttribute('width');
    svg.removeAttribute('height');
    svg.style.width = '100%';
    svg.style.height = 'auto';
    qr.replaceChildren(document.importNode(svg, true));
  }

  function alternarCelular() {
    const panel = $('radarCelularPanel');
    if (!panel) return;
    panel.hidden = !panel.hidden;
    $('radarCelularBtn').setAttribute('aria-expanded', String(!panel.hidden));
  }

  /** Igual que Descartar: el primer clic pide confirmación en el mismo botón. */
  let confirmaRegenerar = null;
  async function regenerarCelular() {
    const btn = $('radarCelularRegenerarBtn');
    if (!confirmaRegenerar) {
      btn.textContent = '¿Confirmar? Hay que volver a escanear';
      confirmaRegenerar = setTimeout(() => {
        confirmaRegenerar = null;
        btn.textContent = 'Desemparejar todos los celulares';
      }, MS_CONFIRMAR_DESCARTE);
      return;
    }
    clearTimeout(confirmaRegenerar);
    confirmaRegenerar = null;
    btn.textContent = 'Desemparejar todos los celulares';
    await ejecutar(btn, async () => {
      const r = await api('POST', '/acceso-celular/regenerar');
      if (r.ok && r.data) {
        pintarCelular(r.data);
        mostrarMensaje('Celulares desemparejados: escaneá el código nuevo para volver a usarlo.', 'ok');
      } else {
        mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
      }
    });
  }

  async function reanudarEnvios() {
    await ejecutar($('radarReanudarEnviosBtn'), async () => {
      const r = await api('POST', '/resume-sends');
      if (r.ok && r.data) {
        onStatus(r.data);
        mostrarMensaje('Envíos reanudados.', 'ok');
      } else {
        mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
      }
    });
  }

  async function levantarBloqueo() {
    if (!window.confirm('¿Ya comprobaste en LinkedIn que la búsqueda funciona? Las búsquedas vuelven en el próximo ciclo, dentro del presupuesto.')) {
      return;
    }
    await ejecutar($('radarLevantarBloqueoBtn'), async () => {
      const r = await api('POST', '/levantar-bloqueo');
      if (r.ok && r.data) {
        onStatus(r.data);
        mostrarMensaje('Pausa levantada: las búsquedas vuelven en el próximo ciclo.', 'ok');
      } else {
        mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
      }
    });
  }

  async function guardarUmbral() {
    const input = $('radarUmbralInput');
    const valor = Number(input.value);
    if (input.value === '' || !Number.isInteger(valor) || valor < 0 || valor > 100) {
      mostrarMensaje('La afinidad mínima debe ser un entero entre 0 y 100.', 'error');
      input.value = S.estado.umbral_match;
      return;
    }
    const r = await api('PATCH', '/config', { umbral_match: valor });
    if (r.ok && r.data) {
      onStatus(r.data);
      renderLista();
    } else {
      mostrarMensaje(mensajeDeDetalle(r.detail), 'error');
      input.value = S.estado.umbral_match;
    }
  }

  // ------------------------------------------------------------------ SSE

  function onStatus(data) {
    if (!data || typeof data !== 'object') return;
    S.estado = { ...S.estado, ...data };
    if (data.cola !== undefined) S.colaDesde = Date.now();
    if (data.cola && data.cola.actual && data.cola.actual.progreso) {
      sembrarProgreso(data.cola.actual.post_id, data.cola.actual.progreso);
      pintarProgreso(data.cola.actual.post_id);
    }
    if (S.estado.estado === 'DETENIDO') {
      S.deteniendo = false;
      clearTimeout(timerDetener);
    }
    if (S.iniciado) renderControl();
    renderCola();
    debouncedCargarProcesos();
  }

  function onPost(data) {
    if (!data || typeof data !== 'object' || data.id === undefined || !perteneceAlModo(data)) return;
    const previo = S.posts.get(data.id);
    const parcial = { id: data.id, estado: data.estado, autor: data.autor };
    if (data.puesto) parcial.puesto = data.puesto;
    if (data.fuente) parcial.fuente = data.fuente;
    if (data.canal) parcial.canal = data.canal;
    if (data.match_score !== undefined && data.match_score !== null) parcial.match_score = data.match_score;
    const merged = guardarPost(previo ? { ...previo, ...parcial } : parcial);
    if (data.estado !== 'GENERANDO' && S.progreso.delete(data.id) && previo && previo.estado === 'GENERANDO') {
      const nombre = merged.puesto || merged.autor || `post ${data.id}`;
      if (data.estado === 'LISTO') mostrarMensaje(`CV listo: ${nombre}. Revisá el mensaje y aprobalo.`, 'ok');
      else if (data.estado === 'ERROR') mostrarMensaje(`Falló la generación del CV: ${nombre}. Mirá el detalle y reintentá.`, 'error');
    }
    if (previo && previo.estado !== data.estado) refrescarCola();
    debouncedCargarProcesos();
    if (S.iniciado) {
      upsertCard(merged);
      // El evento no trae email ni aprobación: se trae el post (debounce) para tener el estado completo
      clearTimeout(timersRefresco.get(data.id));
      const genModo = S.genModo;
      timersRefresco.set(data.id, setTimeout(() => {
        if (genModo !== S.genModo) return;
        timersRefresco.delete(data.id);
        refrescarPost(data.id);
      }, MS_DEBOUNCE_REFRESCO));
    }
  }

  /** El post no era una oferta: se borró en el servidor y se saca de la lista. */
  function onPostRemoved(data) {
    if (!data || data.id === undefined) return;
    const id = Number(data.id);
    S.posts.delete(id);
    document.querySelectorAll(`[data-post-id="${id}"]`).forEach((el) => el.remove());
    if (S.selId === id) {
      S.selId = null;
      S.detalle = null;
      renderDetalle();
      renderEmail(true);
    }
    renderChips();
    renderVacio();
  }

  async function refrescarPost(id) {
    const genModo = S.genModo;
    const r = await api('GET', `/posts/${id}`);
    if (genModo !== S.genModo || !r.ok || !r.data) return;
    // Si es el post abierto, no se pisa lo que el usuario está escribiendo (renderEmail respeta foco/edición)
    aplicarDetalle(r.data, false);
  }

  // ------------------------------------------------------------------ pestañas móviles

  function setTab(tab) {
    S.tab = tab;
    if (tab === 'lista' || tab === 'feed') S.listaTab = tab;
    // Celular: un paso por pantalla (#radarTabs). Escritorio (radar.css): los 3 paneles a la vista y, en la lista,
    // pestañas Feed general · Mi feed (#radarTabsLista) que recuerdan cuál está abierta aunque se vea el detalle
    document.querySelectorAll('#radarTabs [data-radar-tab]').forEach((b) => {
      b.setAttribute('aria-selected', String(b.dataset.radarTab === tab));
    });
    document.querySelectorAll('#radarTabsLista [data-lista-tab]').forEach((b) => {
      b.setAttribute('aria-selected', String(b.dataset.listaTab === S.listaTab));
    });
    const paneles = { lista: 'radar-panel-lista', feed: 'radar-panel-lista', detalle: 'radar-panel-detalle', email: 'radar-panel-email' };
    Object.keys(paneles).forEach((k) => {
      const el = $(paneles[k]);
      if (el) el.classList.toggle('hidden', paneles[k] !== paneles[tab]);
    });
    const pb = $('radarPanelBusqueda');
    const pf = $('radarPanelFeed');
    if (pb) pb.classList.toggle('hidden', S.listaTab === 'feed');
    if (pf) pf.classList.toggle('hidden', S.listaTab !== 'feed');
  }

  // ------------------------------------------------------------------ inicio

  function enlazarEventos() {
    $('radarToggleBtn').addEventListener('click', alternarRadar);
    $('radarMostrarNavegadorBtn').addEventListener('click', mostrarNavegador);
    if ($('radarReintentarAhoraBtn')) $('radarReintentarAhoraBtn').addEventListener('click', reintentarRadar);
    if ($('radarCelularBtn')) {
      $('radarCelularBtn').addEventListener('click', alternarCelular);
      $('radarCelularRegenerarBtn').addEventListener('click', regenerarCelular);
    }
    if ($('radarRevisionBtn')) $('radarRevisionBtn').addEventListener('click', alternarRevision);
    $('radarReanudarEnviosBtn').addEventListener('click', reanudarEnvios);
    if ($('radarLevantarBloqueoBtn')) $('radarLevantarBloqueoBtn').addEventListener('click', levantarBloqueo);
    $('radarAprobarTodosBtn').addEventListener('click', aprobarTodos);
    $('radarAprobarConfirmarBtn').addEventListener('click', () => cerrarAprobar(true));
    $('radarAprobarCancelarBtn').addEventListener('click', () => cerrarAprobar(false));
    $('radarAprobarLista').addEventListener('change', actualizarConteoAprobar);
    $('radarAprobarModal').addEventListener('mousedown', pulsarFondoAprobar);
    $('radarAprobarModal').addEventListener('click', clicFondoAprobar);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (S.aprobar) cerrarAprobar(false);
        const p = $('radarProcesosPanel');
        if (p && !p.hidden) cerrarProcesos();
      }
    });
    if ($('radarCola')) $('radarCola').addEventListener('click', alternarProcesos);
    if ($('radarProcesosCerrar')) $('radarProcesosCerrar').addEventListener('click', cerrarProcesos);
    if ($('radarBacklogBtn')) $('radarBacklogBtn').addEventListener('click', alternarBacklog);
    if ($('radarGeneracionBtn')) $('radarGeneracionBtn').addEventListener('click', alternarGeneracion);
    if ($('radarAnalisisBtn')) $('radarAnalisisBtn').addEventListener('click', alternarAnalisis);
    for (const [id] of INTERRUPTORES) {
      if ($(id)) $(id).addEventListener('click', () => alternarInterruptor(id));
    }
    if ($('radarModoSerie')) $('radarModoSerie').addEventListener('change', guardarModoSerie);
    const panelProc = $('radarProcesosPanel');
    if (panelProc) {
      panelProc.addEventListener('mousedown', pulsarFondoProcesos);
      panelProc.addEventListener('click', clicFondoProcesos);
    }
    $('radarMensajeCerrar').addEventListener('click', ocultarMensaje);
    $('radarUmbralInput').addEventListener('change', guardarUmbral);

    $('radarFiltros').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-estado]');
      if (!b) return;
      S.filtro = b.dataset.estado;
      renderLista();
      marcarSeleccion();
      cerrarMenuEstados(true);
    });
    if (typeof $('radarFiltros').addEventListener === 'function') $('radarFiltros').addEventListener('keydown', teclaMenuEstados);
    const historial = $('manualHistorial');
    const resumenHistorial = historial && historial.querySelector ? historial.querySelector('summary') : null;
    if (resumenHistorial) {
      // El clic (también Enter o Espacio sobre el resumen) llega antes de que cambie `open`: se recuerda lo que va a quedar
      resumenHistorial.addEventListener('click', () => {
        if (S.modo === 'manual') recordarHistorial(!historial.open);
      });
    }
    const btnEstado = $('radarEstadoBtn');
    if (btnEstado) {
      btnEstado.addEventListener('click', () => {
        const menu = $('radarFiltros');
        if (menu && !menu.hidden) cerrarMenuEstados(false);
        else abrirMenuEstados();
      });
      btnEstado.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          abrirMenuEstados();
        }
      });
    }
    const noApto = $('radarFiltroNoApto');
    if (noApto) noApto.addEventListener('click', () => {
      S.filtro = 'NO_APTO';
      renderLista();
      marcarSeleccion();
    });

    const filtroPortal = $('radarFiltroPortal');
    if (filtroPortal) {
      filtroPortal.addEventListener('click', (e) => {
        const b = e.target.closest('button[data-portal]');
        if (!b || !PORTALES_FILTRO.includes(b.dataset.portal)) return;
        S.portal = b.dataset.portal;
        renderLista();
        marcarSeleccion();
      });
    }

    const filtroCanal = $('radarFiltroCanal');
    if (filtroCanal) {
      filtroCanal.addEventListener('click', (e) => {
        const b = e.target.closest('button[data-canal]');
        if (!b || !CANALES_FILTRO.includes(b.dataset.canal)) return;
        S.canal = b.dataset.canal;
        renderLista();
        marcarSeleccion();
      });
    }

    const btnFiltros = $('radarFiltrosBtn');
    if (btnFiltros) {
      btnFiltros.addEventListener('click', () => {
        const panel = $('radarFiltrosPanel');
        if (!panel) return;
        if (panel.hidden) cerrarMenuEstados(false);
        panel.hidden = !panel.hidden;
        btnFiltros.setAttribute('aria-expanded', String(!panel.hidden));
      });
    }
    const activos = $('radarFiltrosActivos');
    if (activos) {
      activos.addEventListener('click', (e) => {
        const b = e.target.closest('button[data-quitar-filtro]');
        if (b) quitarFiltro(b.dataset.quitarFiltro);
      });
    }

    const selOrden = $('radarOrden');
    if (selOrden) {
      selOrden.addEventListener('change', () => {
        S.orden = selOrden.value === 'recientes' ? 'recientes' : 'match';
        renderLista();
        marcarSeleccion();
      });
    }

    $('radarFiltroAlcance').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-alcance]');
      if (!b || !ALCANCES_FILTRO.includes(b.dataset.alcance)) return;
      S.alcance = b.dataset.alcance;
      renderLista();
      marcarSeleccion();
    });

    const detalle = $('radarDetallePost');
    if (detalle) {
      detalle.addEventListener('click', (e) => {
        const req = e.target.closest('button[data-req]');
        if (req) ciclarRequisito(req.dataset.req);
        const ver = e.target.closest('button[data-ver-postulacion]');
        if (ver && typeof window.verPostulacion === 'function') window.verPostulacion(Number(ver.dataset.verPostulacion));
      });
    }

    [$('radarLista'), $('radarListaFeed')].filter(Boolean).forEach((lista) => {
      lista.addEventListener('click', (e) => {
        const card = e.target.closest('[data-post-id]');
        if (!card) return;
        if (e.target.closest('a[data-postular-sitio]')) return;
        const boton = e.target.closest('button[data-accion]');
        if (boton) {
          const id = Number(card.dataset.postId);
          const fn = { analizar: analizarPost, 'generar-cv': generarCvPost,
            'postular-igual': postularIgual, descartar: descartarPost }[boton.dataset.accion];
          if (!fn) return;
          ejecutar(boton, () => fn(id));
          return;
        }
        seleccionar(Number(card.dataset.postId));
      });
      lista.addEventListener('keydown', (e) => {
        if (e.target.closest('button[data-accion], a[data-postular-sitio]')) return;
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const card = e.target.closest('[data-post-id]');
        if (card) {
          e.preventDefault();
          seleccionar(Number(card.dataset.postId));
        }
      });
    });

    document.querySelectorAll('#radarTabs [data-radar-tab]').forEach((b) => {
      b.addEventListener('click', () => setTab(b.dataset.radarTab));
    });
    document.querySelectorAll('#radarTabsLista [data-lista-tab]').forEach((b) => {
      b.addEventListener('click', () => setTab(b.dataset.listaTab));
    });

    // `input` (con debounce) además de `change`: Safari no siempre dispara `change` antes del clic en Aprobar
    ['input', 'change'].forEach((ev) => $('radarEmailTo').addEventListener(ev, (e) => programarEdicion('email_to', e.target.value)));
    $('radarEmailSubject').addEventListener('input', (e) => programarEdicion('email_subject', e.target.value));
    $('radarEmailBody').addEventListener('input', (e) => {
      programarEdicion('email_body', e.target.value);
      renderVistaPrevia(S.detalle && esEmail(S.detalle) ? e.target.value : '');
    });

    const conBoton = (id, fn) => $(id).addEventListener('click', (e) => ejecutar(e.currentTarget, () => fn()));
    conBoton('radarAprobarBtn', () => aprobar(false));
    conBoton('radarDescartarBtn', descartar);
    conBoton('radarRegenerarBtn', regenerar);
    conBoton('radarAnalizarBtn', () => analizarPost(S.selId));
    conBoton('radarGenerarCvBtn', () => generarCvPost(S.selId));
    if ($('radarPostularIgualBtn')) conBoton('radarPostularIgualBtn', () => postularIgual(S.selId));
    conBoton('radarReintentarBtn', reintentar);
    $('radarAbrirEditorBtn').addEventListener('click', abrirEditor);
    conBoton('radarCopiarMensajeBtn', copiarMensaje);
    if ($('radarCuotaReintentarBtn')) $('radarCuotaReintentarBtn').addEventListener('click', () => reintentarCuota());
    $('radarDescargarCvBtn').addEventListener('click', descargarCv);
    if ($('radarVerPdf')) {
      $('radarVerPdf').addEventListener('click', (e) => {
        const p = S.detalle;
        if (p && cvDisponible(p)) descargarPdf(p.postulacion_id, e);
      });
    }
    conBoton('radarAbrirDestinoBtn', abrirDestino);
    conBoton('radarMarcarPostuladoBtn', marcarPostulado);
  }

  async function init() {
    if (S.iniciado) return;
    if (!$('vista-radar')) return;
    S.iniciado = true;
    crearControlSerie();
    enlazarEventos();
    // El tiempo transcurrido de la generación avanza aunque no lleguen eventos
    setInterval(() => { S.progreso.forEach((_, id) => pintarProgreso(id)); }, 1000);
    setTab('lista');
    aplicarModo();
    renderChips();
    renderVacio();
    renderControl();
    // La cuenta regresiva de PAUSA se actualiza sola
    setInterval(() => { if (S.estado.estado === 'PAUSA' && !S.deteniendo) renderPill(); }, 1000);
    // La cuota de AGY no emite eventos: el estado se relee cada minuto
    setInterval(() => { cargarEstado(); }, 60000);
    prepararCelular();
    const tareas = [cargarEstado(), cargarPosts(), cargarHistorial()];
    await Promise.all(tareas);
  }

  /** Al mostrar la vista se refresca todo (por si se perdió algún evento SSE).
   *  Si cambia el modo: vacía S.posts, S.selId, S.detalle, S.progreso y recarga. */
  async function mostrar(modo = 'radar') {
    const cambioModo = modo && modo !== S.modo;
    if (cambioModo) {
      S.genModo++;
      S.tokenDetalle++;
      timersRefresco.forEach((timer) => clearTimeout(timer));
      timersRefresco.clear();
      S.modo = modo;
      S.posts.clear();
      S.selId = null;
      S.detalle = null;
      S.progreso.clear();
      // El historial es otro según la vista: cerrado al cambiar (abierto en el Radar tapa la lista de posts). En Manual,
      // abierto solo si el usuario lo abrió en esta sesión (creador, 19/11: arranca cerrado)
      const historial = $('manualHistorial');
      if (historial) historial.open = modo === 'manual' && historialAbiertoEnLaSesion();
      if (modo !== 'radar' && (S.tab === 'feed' || S.listaTab === 'feed')) setTab('lista');
      S.portal = 'TODOS';
      aplicarModo();
      renderLista();
      renderDetalle();
      renderEmail();
    } else {
      aplicarModo();
    }

    if (!S.iniciado) {
      await init();
      return;
    }

    const tareas = [cargarEstado(), cargarPosts(), cargarHistorial()];
    await Promise.all(tareas);
    if (S.selId) await cargarDetalle(S.selId, false);
  }

  /** Selecciona un post por id (desde otra vista). `false` si no existe en el Radar. */
  async function abrirPost(postId) {
    const id = Number(postId);
    if (!id) return false;
    if (!S.posts.has(id)) {
      await cargarPosts();
    }
    if (!S.posts.has(id)) return false;
    await seleccionar(id);
    const card = document.querySelector && document.querySelector(`[data-post-id="${id}"]`);
    if (card && card.scrollIntoView) card.scrollIntoView({ block: 'nearest' });
    return true;
  }

  /** Abre el post que originó la postulación `postulacionId`. Busca en el modo actual y, si no, en el otro. */
  async function abrirPostDePostulacion(postulacionId) {
    const pid = Number(postulacionId);
    if (!pid) return false;
    const buscar = () => Array.from(S.posts.values()).find((q) => Number(q.postulacion_id) === pid);
    let post = buscar();
    if (!post) {
      await cargarPosts();
      post = buscar();
    }
    if (!post) {
      const modoOriginal = S.modo || 'radar';
      const otroModo = modoOriginal === 'manual' ? 'radar' : 'manual';
      // Puede ser de un portal: se busca también ahí antes de rendirse
      const terceroModo = ['radar', 'manual', 'portales'].find((m) => m !== modoOriginal && m !== otroModo);
      const postsPrevios = new Map(S.posts);
      await mostrar(otroModo);
      post = buscar();
      if (!post) {
        await mostrar(terceroModo);
        post = buscar();
      }
      if (!post) {
        await mostrar(modoOriginal);
        if (S.posts.size === 0 && postsPrevios.size > 0) {
          postsPrevios.forEach((v, k) => S.posts.set(k, v));
        }
        mostrarMensaje('No encontramos el post de esta postulación en el Radar.', 'error');
        return false;
      }
    }
    return abrirPost(post.id);
  }

  window.Radar = {
    init,
    abrirPost,
    abrirPostDePostulacion,
    recargar: cargarPosts,
    avisarPausaAlCargar,
    onStatus,
    onPost,
    onPostRemoved,
    onProgreso,
    mostrar,
    _test: { S, textoAvisoPausa, filtrosActivos, quitarFiltro, postularIgual, marcarDescarte, enlazarEventos, desgloseAfinidad, resumenAfinidad, crearCard, ciclarRequisito, onProgreso, htmlProgreso, programarEdicion, flushEdicion, aprobar, aprobarTodos, datosAprobacion, idsMarcados, actualizarConteoAprobar, cerrarAprobar, pulsarFondoAprobar, clicFondoAprobar, descartar, restaurar, reintentar, marcarPostulado, abrirDestino, upsertCard, renderEmail, renderCanal, renderVistaPrevia, renderAvisoCuota, textoVacio, itemRevision, textoCola, renderCola, onPost, renderDetalle, lineaPasos, textoTopes, textoCuotaProceso, filaProceso, sembrarProgreso, abrirProcesos, cerrarProcesos, cargarProcesos, perteneceAlModo, aplicarModo, filaHistorial, cargarHistorial, cargarPosts, textoEstadoCv, alternarAnalisis, alternarGeneracion, renderInterruptores, alternarInterruptor, textoPausaBusquedas, levantarBloqueo },
    _portales: { modoDePost, coincidePortal, linkPostularSitio, logoPortal, botonPostularSitio, extrasDePortal },
    _util: { urlSegura, urlPublicacion, urlDestino, iconoCanal, nombreCanal, esEmail, accionesCanal, coincideFiltro, coincideAlcance, coincideCanal, etiquetaEstado, textoEstadoEmail, etiquetaAlcance, describirPill, accionesPara, mensajeDeDetalle, formatearCuenta, envioDudoso, textoPausaBusquedas },
  };

  document.addEventListener('DOMContentLoaded', init);
})();
