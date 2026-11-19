/* M07: formularios sin proveedores, con borradores y versiones por tarjeta. */
(() => {
    'use strict';

    const definiciones = [
        ['experiencia', 'Experiencia'], ['personales', 'Datos personales'],
        ['voz_email', 'Voz del mail'], ['restricciones', 'Dónde y cómo trabajás'],
        ['anios_por_area_creador', 'Años de experiencia por área'],
        ['roles_objetivo', 'Puestos que buscás y áreas de tu trayectoria'], ['stack', 'Habilidades que tenés'],
        ['conocimientos_ausentes', 'Lo que no sabés'], ['reglas_radar', 'Radar de LinkedIn'],
        ['estructura_cv', 'Cargos en el CV'],
        ['reglas_portales', 'Portales'], ['aprendizajes', 'Lo que marcaste en las ofertas'],
        ['aprendizaje_puntaje', 'Ofertas con puntaje bajo'],
    ];
    // Archivos JSON del perfil que se editan con formulario
    const JSON_FORMULARIO = ['restricciones', 'anios_por_area_creador', 'roles_objetivo', 'stack', 'conocimientos_ausentes',
        'reglas_radar', 'reglas_portales', 'estructura_cv', 'aprendizaje_puntaje'];
    // Portales (14/11): cada portal con sus reglas; si le falta un valor, ese portal no corre y se abre un ticket
    const PORTALES = [['jooble', 'Jooble (API oficial)'], ['indeed', 'Indeed'],
        ['computrabajo', 'Computrabajo (listados públicos)'], ['bumeran', 'Bumeran (con el navegador, como LinkedIn)'],
        ['remoteok', 'RemoteOK (API pública, remoto)'], ['remotive', 'Remotive (API pública, remoto)'],
        ['himalayas', 'Himalayas (API pública, remoto)']];
    const NOMBRE_PORTAL = {jooble: 'Jooble', indeed: 'Indeed', computrabajo: 'Computrabajo', bumeran: 'Bumeran',
        remoteok: 'RemoteOK', remotive: 'Remotive', himalayas: 'Himalayas'};
    // 15/11: lo que la fuente de cada portal necesita para conectarse también va en el perfil (core/reglas_portales.PROPIOS)
    const PROPIOS_PORTAL = {
        jooble: [['url', 'Dirección de la API (con {clave})', 'text']],
        indeed: [['pais', 'País de búsqueda', 'text']],
        computrabajo: [['url', 'Dirección del sitio', 'text']],
        bumeran: [['url', 'Dirección del sitio', 'text'], ['sitemap', 'Sitemap de avisos', 'text'],
            ['plazo_pagina_s', 'Espera de una página (segundos)', 'number']],
        remoteok: [['url', 'Dirección de la API', 'text'], ['url_busqueda', 'API para búsquedas (con {palabras})', 'text']],
        remotive: [['url', 'Dirección de la API', 'text'], ['url_busqueda', 'API para búsquedas (con {palabras})', 'text']],
        himalayas: [['url', 'Dirección de la API', 'text'], ['url_busqueda', 'API para búsquedas (con {palabras})', 'text']],
    };
    const REGLAS_PORTAL = [
        ['consultas_por_dia', 'Consultas por día', 'number'],
        ['minutos_entre_consultas', 'Ritmo: minutos entre consultas', 'number'],
        ['resultados_por_consulta', 'Ofertas por consulta', 'number'],
        ['dias', 'Antigüedad máxima (días)', 'number'],
        ['ubicacion', 'Ubicación', 'text'],
        // 15/11: «todas» trae las ofertas recientes sin búsquedas; «busquedas», las marcadas en la tarjeta Búsquedas
        ['modo', 'Modo: todas o busquedas', 'text'],
        ['listados', 'Listados que recorre con «todas» (uno por renglón; puede quedar vacío)', 'lista'],
        ['leer_pagina_aviso', 'Lee el aviso completo de su página', 'checkbox'],
        ['plazo_consulta_s', 'Espera de la respuesta (segundos)', 'number'],
        ['min_descripcion_completa', 'Caracteres desde los que un aviso está completo', 'number'],
    ];
    // Reglas del Radar (creador, 12/11): todos los números del Radar están acá, ninguno en el código
    const REGLAS_RADAR = [
        ['Presupuesto de búsquedas', [
            [['busquedas_por_hora'], 'Búsquedas por hora (cada cuenta)'],
            [['busquedas_por_dia'], 'Búsquedas por día (cada cuenta)'],
            [['porcentaje_si_las_dos_cuentas_se_pausaron'], 'Búsquedas que quedan (en %) si LinkedIn frenó a las dos cuentas en las últimas 24 h'],
        ]],
        ['Pausas', [
            // Rediseño E3 (§6): cada pausa es una fila «desde [x] a [y] min»
            [['pausa_entre_lecturas_min'], 'Pausa entre lecturas', 'rango'],
            [['pausa_entre_vueltas_min'], 'Pausa entre vueltas', 'rango'],
        ]],
        ['Largo de cada lectura', [
            [['lectura_busqueda', 'posts'], 'Búsqueda: posts por lectura'],
            [['lectura_busqueda', 'scrolls'], 'Búsqueda: scrolls por lectura'],
            [['lectura_busqueda', 'pantallas_sin_nuevos'], 'Búsqueda: pantallas seguidas sin nada nuevo antes de cortar'],
            [['lectura_feed', 'posts'], 'Feed: posts por lectura'],
            [['lectura_feed', 'scrolls'], 'Feed: scrolls por lectura'],
        ]],
        ['Pausa por bloqueo de LinkedIn (se duplica con cada bloqueo en 24 h)', [
            [['pausa_bloqueo_horas', 'primera'], 'Pausa por bloqueo: primera (horas)'],
            [['pausa_bloqueo_horas', 'maxima'], 'Pausa por bloqueo: máxima (horas)'],
        ]],
        ['Envíos', [[['tope_envios_dia'], 'Envíos por día']]],
    ];
    // Con qué cuenta de LinkedIn se lee cada cosa (creador, 13/11). Sin cuenta, esa lectura no se hace
    const CUENTAS_RADAR = [
        ['cuenta_feed', 'Cuenta para el feed'], ['cuenta_busquedas', 'Cuenta para las búsquedas'],
    ];
    const OPCIONES_CUENTA = [['', 'Ninguna (no se lee)'], ['principal', 'Principal'], ['secundaria', 'Secundaria']];
    const tarjetas = new Map();
    let raiz, vistaPrevia, estadoLote, botonLote, carga, busquedas, secuencia = 0, previewTurno = 0;
    let barra, barraTexto, botonGuardarTodo, botonDescartar, dialogoSalir, guardando = false, ubicaciones, transferiblesEl, respaldoEl;
    let busquedasDato = null;

    const copia = dato => JSON.parse(JSON.stringify(dato));
    const esObjeto = dato => dato !== null && typeof dato === 'object' && !Array.isArray(dato);
    // ---- experiencia.txt sin perder el formato del motor (rediseño E3, plan §6)
    // Cada línea es un nodo: sección («## Empresa»), cargo («### Título | desde - hasta»), viñeta («• texto») o texto
    // que el editor no reconoce, que se guarda intacto en su lugar. serializar(leer(t)) === t, byte a byte.
    const RE_SECCION = /^## (.+)$/;
    const RE_CARGO = /^### (.+) \| (\S+) - (\S+)$/;
    const RE_VINETA = /^• (.*)$/;
    function leerExperiencia(texto) {
        const eol = texto.includes('\r\n') ? '\r\n' : '\n';
        const partes = texto === '' ? [] : texto.split(eol);
        const final = partes.length > 0 && partes[partes.length - 1] === '';
        if (final) partes.pop();
        const lineas = partes.map((linea) => {
            let m;
            if ((m = RE_SECCION.exec(linea))) return {tipo: 'seccion', nombre: m[1]};
            if ((m = RE_CARGO.exec(linea))) return {tipo: 'cargo', titulo: m[1], desde: m[2], hasta: m[3]};
            if ((m = RE_VINETA.exec(linea))) return {tipo: 'vineta', texto: m[1]};
            return {tipo: 'texto', texto: linea};
        });
        return {eol, final, lineas};
    }
    function lineaExperiencia(n) {
        if (n.tipo === 'seccion') return `## ${n.nombre}`;
        if (n.tipo === 'cargo') return `### ${n.titulo} | ${n.desde} - ${n.hasta}`;
        if (n.tipo === 'vineta') return `• ${n.texto}`;
        return n.texto;
    }
    function serializarExperiencia(modelo) {
        return modelo.lineas.map(lineaExperiencia).join(modelo.eol) + (modelo.final ? modelo.eol : '');
    }
    // El editor visual se usa solo si el archivo vuelve idéntico (p. ej., con fines de línea mezclados no)
    function experienciaSegura(texto) {
        return serializarExperiencia(leerExperiencia(texto)) === texto && !(texto.includes('\r\n') && /(^|[^\r])\n/.test(texto));
    }
    // Vista para el editor: secciones con sus cargos (cada uno con sus viñetas) y las viñetas sueltas de la sección
    function seccionesExperiencia(modelo) {
        const secciones = [];
        let actual = null, cargo = null;
        for (const n of modelo.lineas) {
            if (n.tipo === 'seccion') { actual = {nombre: n.nombre, nodo: n, cargos: [], vinetas: []}; secciones.push(actual); cargo = null; }
            else if (!actual) continue;
            else if (n.tipo === 'cargo') { cargo = {nodo: n, vinetas: []}; actual.cargos.push(cargo); }
            else if (n.tipo === 'vineta') (cargo ? cargo.vinetas : actual.vinetas).push(n);
        }
        return secciones;
    }

    // ---- personales.txt (rediseño E3, plan §6): nombre, «Puesto:», contacto separado por « | », títulos en
    // mayúsculas (EDUCACIÓN, IDIOMAS…) con sus ítems «• ». El resto queda intacto. serializar(leer(t)) === t.
    const RE_PUESTO = /^Puesto: (.*)$/;
    const RE_TITULO = /^[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ ]{2,}$/;
    const RE_ITEM = /^• (.*)$/;
    function tipoContacto(texto, i) {
        if (/wa\.me\//i.test(texto)) return 'whatsapp';
        if (/linkedin\.com/i.test(texto)) return 'linkedin';
        if (/^[^\s@]+@[^\s@]+$/.test(texto)) return 'email';
        if (/^\+?[\d\s()-]{6,}$/.test(texto)) return 'telefono';
        return i === 0 ? 'ciudad' : 'otro';
    }
    function leerPersonales(texto) {
        const eol = texto.includes('\r\n') ? '\r\n' : '\n';
        const partes = texto === '' ? [] : texto.split(eol);
        const final = partes.length > 0 && partes[partes.length - 1] === '';
        if (final) partes.pop();
        let vioContacto = false, vioTitulo = false;
        const lineas = partes.map((linea, i) => {
            let m;
            if (i === 0 && linea.trim()) return {tipo: 'nombre', texto: linea};
            if ((m = RE_PUESTO.exec(linea))) return {tipo: 'puesto', texto: m[1]};
            if (!vioContacto && !vioTitulo && linea.includes(' | ')) {
                vioContacto = true;
                return {tipo: 'contacto', partes: linea.split(' | ').map((t, j) => ({tipo: tipoContacto(t, j), texto: t}))};
            }
            if (RE_TITULO.test(linea)) { vioTitulo = true; return {tipo: 'titulo', texto: linea}; }
            if ((m = RE_ITEM.exec(linea))) return {tipo: 'item', texto: m[1]};
            return {tipo: 'texto', texto: linea};
        });
        return {eol, final, lineas};
    }
    function lineaPersonales(n) {
        if (n.tipo === 'puesto') return `Puesto: ${n.texto}`;
        if (n.tipo === 'contacto') return n.partes.map(p => p.texto).join(' | ');
        if (n.tipo === 'item') return `• ${n.texto}`;
        return n.texto;
    }
    function serializarPersonales(modelo) {
        return modelo.lineas.map(lineaPersonales).join(modelo.eol) + (modelo.final ? modelo.eol : '');
    }
    function camposPersonales(modelo) {
        const c = {nombre: null, puesto: null, contacto: null, secciones: {}, titulos: []};
        let actual = null;
        for (const n of modelo.lineas) {
            if (n.tipo === 'nombre') c.nombre = n;
            else if (n.tipo === 'puesto') c.puesto = n;
            else if (n.tipo === 'contacto') c.contacto = n;
            else if (n.tipo === 'titulo') { actual = n.texto; c.secciones[actual] = []; c.titulos.push(n); }
            else if (n.tipo === 'item' && actual) c.secciones[actual].push(n);
        }
        return c;
    }
    // Formulario solo si el archivo vuelve idéntico y tiene nombre y titular o contacto
    function personalesSeguro(texto) {
        if (texto.includes('\r\n') && /(^|[^\r])\n/.test(texto)) return false;
        const modelo = leerPersonales(texto);
        if (serializarPersonales(modelo) !== texto) return false;
        const c = camposPersonales(modelo);
        return !!(c.nombre && (c.puesto || c.contacto));
    }

    function nodo(tag, texto, clase) {
        const el = document.createElement(tag);
        if (texto !== undefined) el.textContent = texto;
        if (clase) el.className = clase;
        return el;
    }
    function limpiar(el) {
        while (el.firstChild) el.removeChild(el.firstChild);
    }
    function boton(texto, accion) {
        const el = nodo('button', texto);
        el.type = 'button';
        el.addEventListener('click', accion);
        return el;
    }
    function estado() {
        const el = nodo('p', '', 'perfil-estado');
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        el.setAttribute('aria-atomic', 'true');
        return el;
    }
    function mensajeError(error) {
        return error instanceof Error ? error.message : 'No se pudo completar la operación.';
    }
    async function pedir(url, metodo = 'GET', payload) {
        const opciones = {method: metodo};
        if (payload !== undefined) {
            opciones.headers = {'Content-Type': 'application/json'};
            opciones.body = JSON.stringify(payload);
        }
        let respuesta;
        try {
            respuesta = await fetch(url, opciones);
        } catch (_) {
            throw new Error('No se pudo conectar. Tu edición sigue acá. Volvé a intentar.');
        }
        let dato;
        try { dato = await respuesta.json(); }
        catch (_) { throw new Error('No se pudo leer la respuesta del servidor. Tu edición sigue acá.'); }
        if (!respuesta.ok) {
            const error = new Error(typeof dato.detail === 'string' ? dato.detail :
                `No se pudo completar la operación (${respuesta.status}).`);
            error.status = respuesta.status;
            throw error;
        }
        return dato;
    }
    function pendiente(t, valor) {
        t.pendiente = valor;
        t.el.setAttribute('aria-busy', String(valor));
        for (const control of [...t.cuerpo.querySelectorAll('input, textarea, button, select'),
            ...[].concat(t.extra || []).flatMap(x => [...x.querySelectorAll('input, textarea, button, select')])]) {
            control.disabled = valor || !t.lista;
        }
        if (t.guardar) t.guardar.disabled = valor || !t.lista;
        t.recargar.disabled = valor;
    }
    function editar(t, clave) {
        t.cambios.add(clave);
        t.revision++;
        actualizarBarra();
    }

    // ---- Guardado único (rediseño E3, plan §6): una barra con los cambios de todas las tarjetas
    function conCambios() {
        const lista = [...tarjetas.values()].filter(t => t.cambios.size && t.lista);
        if (busquedas && busquedas.cambios.size) lista.push(busquedas);
        return lista;
    }
    function hayCambios() {
        return conCambios().length > 0;
    }
    function actualizarBarra() {
        if (!barra) return;
        const lista = conCambios();
        barra.hidden = lista.length === 0;
        barraTexto.textContent = lista.length
            ? `${lista.length} ${lista.length === 1 ? 'tarjeta' : 'tarjetas'} sin guardar: ${lista.map(t => t.titulo).join(', ')}.`
            : '';
        botonGuardarTodo.disabled = guardando;
        botonDescartar.disabled = guardando;
        for (const t of tarjetas.values()) t.el.classList.toggle('editada', t.cambios.size > 0);
        if (busquedas) busquedas.el.classList.toggle('editada', busquedas.cambios.size > 0);
        for (const ir of raiz.querySelectorAll('.perfil-ir')) {
            const seccion = raiz.querySelector(`#perfil-${ir.dataset.seccion}`);
            ir.classList.toggle('con-cambios', !!(seccion && seccion.querySelector('fieldset.editada')));
            const res = ir.querySelector('.perfil-ir-res');
            if (res) res.textContent = resumenSeccion(ir.dataset.seccion);
        }
    }
    // Resumen de cada sección en el índice (§6): qué hay, con los datos ya cargados
    const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
    function resumenSeccion(id) {
        const dato = (clave) => { const t = tarjetas.get(clave); return t && t.lista ? t.original : null; };
        const partes = [];
        if (id === 'datos') {
            const per = dato('personales');
            if (typeof per === 'string' && per.trim()) partes.push(per.split(/\r?\n/)[0].trim());
            const exp = dato('experiencia');
            if (typeof exp === 'string') {
                const secciones = seccionesExperiencia(leerExperiencia(exp)).filter(x => x.cargos.length);
                partes.push(plural(secciones.length, 'empresa', 'empresas'),
                    plural(secciones.reduce((n, x) => n + x.cargos.length, 0), 'cargo', 'cargos'));
            }
        } else if (id === 'sabe') {
            const st = dato('stack');
            if (esObjeto(st) && Array.isArray(st.coincidencias)) partes.push(plural(st.coincidencias.length, 'habilidad', 'habilidades'));
            const co = dato('conocimientos_ausentes');
            if (esObjeto(co) && Array.isArray(co.conocimientos)) partes.push(`${co.conocimientos.length} que no tenés`);
        } else if (id === 'busco') {
            const ro = dato('roles_objetivo');
            if (esObjeto(ro) && Array.isArray(ro.roles_objetivo)) partes.push(plural(ro.roles_objetivo.length, 'puesto', 'puestos'));
            if (busquedasDato && Array.isArray(busquedasDato.busquedas)) partes.push(plural(busquedasDato.busquedas.length, 'búsqueda', 'búsquedas'));
        } else if (id === 'cv') {
            partes.push('Voz del mail · cargos · CV general');
        } else if (id === 'aprendio') {
            const t = tarjetas.get('aprendizajes');
            if (t && t.aprendizajes) partes.push(plural(Object.keys(t.aprendizajes).length, 'decisión', 'decisiones'));
        } else if (id === 'automatizacion') {
            partes.push('Avanzado · Radar y portales');
        }
        return partes.join(' · ');
    }
    // Guarda en orden cada archivo cambiado, cada uno con su versión y su respaldo. Si uno falla, los demás
    // quedan guardados y ese conserva su borrador, su error y «Recargar» (409).
    async function guardarTodo() {
        if (guardando) return;
        guardando = true;
        actualizarBarra();
        try {
            for (const t of conCambios()) {
                if (t === busquedas) await guardarPortales();
                else await guardar(t);
            }
        } finally {
            guardando = false;
            actualizarBarra();
        }
    }
    async function descartarTodo() {
        if (guardando || !window.confirm('¿Descartar todos los cambios sin guardar?')) return;
        for (const t of conCambios()) {
            if (t === busquedas) { busquedas.cambios.clear(); await cargarBusquedas(); }
            else await cargar(t, true);
        }
        actualizarBarra();
    }
    // Aviso al salir de Mi perfil con cambios: «Seguir editando · Salir sin guardar · Guardar y salir».
    // Devuelve true si se queda esperando la respuesta (app.js no cambia de vista todavía).
    function pedirSalida(continuar) {
        if (!hayCambios() || !dialogoSalir) return false;
        dialogoSalir.continuar = continuar;
        dialogoSalir.hidden = false;
        dialogoSalir.querySelector('button').focus();
        return true;
    }
    function cerrarSalida() {
        if (dialogoSalir) dialogoSalir.hidden = true;
    }
    function crearDialogoSalir() {
        const fondo = nodo('div', undefined, 'perfil-salir');
        fondo.hidden = true;
        fondo.setAttribute('role', 'dialog');
        fondo.setAttribute('aria-modal', 'true');
        fondo.setAttribute('aria-labelledby', 'perfilSalirTitulo');
        const caja = nodo('div', undefined, 'perfil-salir-caja');
        const titulo = nodo('h3', 'Tenés cambios sin guardar');
        titulo.id = 'perfilSalirTitulo';
        const texto = nodo('p', 'Si salís sin guardar, los cambios quedan como borrador hasta que vuelvas o recargues la página.');
        const acciones = nodo('div', undefined, 'perfil-salir-acciones');
        const seguir = boton('Seguir editando', cerrarSalida);
        const salir = boton('Salir sin guardar', () => { cerrarSalida(); if (fondo.continuar) fondo.continuar(); });
        const guardarSalir = boton('Guardar y salir', async () => {
            await guardarTodo();
            if (hayCambios()) { cerrarSalida(); return; }  // algo falló: queda a la vista con su error
            cerrarSalida();
            if (fondo.continuar) fondo.continuar();
        });
        guardarSalir.classList.add('perfil-primario');
        acciones.append(seguir, salir, guardarSalir);
        caja.append(titulo, texto, acciones);
        fondo.append(caja);
        fondo.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrarSalida(); });
        return fondo;
    }
    function campo(t, contenedor, texto, tipo, valor, cambio) {
        const grupo = nodo('div', undefined, tipo === 'checkbox' ? 'perfil-casilla' : 'perfil-campo');
        const label = nodo('label', texto);
        const input = nodo(tipo === 'textarea' ? 'textarea' : 'input');
        input.id = `perfil-campo-${++secuencia}`;
        label.htmlFor = input.id;
        if (tipo !== 'textarea') input.type = tipo;
        if (tipo === 'checkbox') input.checked = valor === true;
        else input.value = valor == null ? '' : String(valor);
        if (tipo === 'textarea') { input.rows = 9; input.spellcheck = false; }
        input.addEventListener('input', () => { editar(t, texto); if (cambio) cambio(); });
        if (tipo === 'checkbox') grupo.append(input, label);
        else grupo.append(label, input);
        contenedor.append(grupo);
        return input;
    }

    // Selector con opciones fijas; un valor que no está en la lista se conserva como opción (no se pierde al guardar)
    function selector(t, contenedor, texto, opciones, valor) {
        const grupo = nodo('div', undefined, 'perfil-campo');
        const label = nodo('label', texto);
        const select = nodo('select');
        select.id = `perfil-campo-${++secuencia}`;
        label.htmlFor = select.id;
        const lista = opciones.some(([v]) => v === valor) || valor == null || valor === '' ? opciones : [...opciones, [valor, String(valor)]];
        for (const [v, rotulo] of lista) {
            const opcion = nodo('option', rotulo);
            opcion.value = v;
            select.append(opcion);
        }
        select.value = valor == null || valor === '' ? opciones[0][0] : valor;
        select.addEventListener('change', () => editar(t, texto));
        grupo.append(label, select);
        contenedor.append(grupo);
        return select;
    }

    // Listas de textos del perfil: un ítem por renglón
    const aLineas = lista => (Array.isArray(lista) ? lista : []).join('\n');
    const deLineas = texto => texto.split('\n').map(s => s.trim()).filter(Boolean);
    // Listas como chips (rediseño E3, §6): cada ítem es un chip con «Quitar»; el campo con la etiqueta agrega
    // (Enter, o pegar varios renglones). Devuelve {value}: los ítems unidos por renglón, como antes la caja de texto.
    function campoLista(t, contenedor, texto, lista, filas) {
        const items = (Array.isArray(lista) ? lista : []).map(String);
        const grupo = nodo('div', undefined, 'perfil-campo');
        const label = nodo('label', texto);
        const caja = nodo('div', undefined, 'tags perfil-chips');
        caja.setAttribute('role', 'group');
        caja.setAttribute('aria-label', `Lista: ${texto}`);
        const entrada = nodo('textarea', undefined, 'perfil-chips-entrada');
        entrada.id = `perfil-campo-${++secuencia}`;
        entrada.rows = 1;
        entrada.spellcheck = false;
        entrada.placeholder = 'Agregar…';
        label.htmlFor = entrada.id;
        const dibujarChips = () => {
            for (const chip of [...caja.querySelectorAll('.tag')]) chip.remove();
            items.forEach((item, i) => {
                const chip = nodo('span', undefined, 'tag');
                chip.append(nodo('span', item));
                const quitar = boton('×', () => { items.splice(i, 1); editar(t, texto); dibujarChips(); });
                quitar.setAttribute('aria-label', `Quitar ${item}`);
                chip.append(quitar);
                caja.insertBefore(chip, entrada);
            });
        };
        const agregar = (valor) => {
            const nuevos = valor.split('\n').map(x => x.trim()).filter(Boolean);
            if (!nuevos.length) return false;
            items.push(...nuevos);
            editar(t, texto);
            dibujarChips();
            return true;
        };
        entrada.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); if (agregar(entrada.value)) entrada.value = ''; }
            else if (e.key === 'Backspace' && entrada.value === '' && items.length) { items.pop(); editar(t, texto); dibujarChips(); }
        });
        // Pegar (o escribir de golpe) varios renglones agrega uno por chip
        entrada.addEventListener('input', () => { if (entrada.value.includes('\n') && agregar(entrada.value)) entrada.value = ''; });
        entrada.addEventListener('blur', () => { if (agregar(entrada.value)) entrada.value = ''; });
        caja.append(entrada);
        dibujarChips();
        grupo.append(label, caja);
        contenedor.append(grupo);
        return {
            get value() { return [...items, ...entrada.value.split('\n').map(x => x.trim()).filter(Boolean)].join('\n'); },
            entrada,
        };
    }
    // Filas editables con columnas de texto, de lista o casilla. Conserva las claves que no muestra.
    function tabla(t, titulo, filas, columnas, vacia, textoAgregar, destino = t.cuerpo, conTitulo = true) {
        if (conTitulo) destino.append(nodo('h3', titulo));
        const caja = nodo('div', undefined, 'perfil-filas');
        destino.append(caja);
        const modelos = [];
        function agregar(original) {
            const fila = nodo('div', undefined, 'perfil-area perfil-tabla');
            const modelo = {original: copia(original), inputs: {}};
            for (const [clave, rotulo, tipo, opciones] of columnas) {
                if (tipo === 'opciones') modelo.inputs[clave] = selector(t, fila, rotulo, opciones, original[clave]);
                else if (tipo === 'lista') modelo.inputs[clave] = campoLista(t, fila, rotulo, original[clave], 3);
                else if (tipo === 'checkbox') modelo.inputs[clave] = campo(t, fila, rotulo, 'checkbox', original[clave] !== false);
                else modelo.inputs[clave] = campo(t, fila, rotulo, 'text', original[clave]);
            }
            fila.append(boton('Quitar', () => {
                editar(t, titulo);
                modelos.splice(modelos.indexOf(modelo), 1);
                fila.remove();
            }));
            modelos.push(modelo);
            caja.append(fila);
        }
        for (const fila of Array.isArray(filas) ? filas : []) if (esObjeto(fila)) agregar(fila);
        destino.append(boton(textoAgregar, () => {
            if (!t.lista || t.pendiente) return;
            editar(t, titulo);
            agregar(copia(vacia));
        }));
        return () => modelos.map(m => {
            const fila = copia(m.original);
            for (const [clave, , tipo] of columnas) {
                const input = m.inputs[clave];
                fila[clave] = tipo === 'lista' ? deLineas(input.value) : (tipo === 'checkbox' ? input.checked : input.value);
            }
            return fila;
        });
    }
    // Cargos en el CV (creador, 15/11): cómo se muestran los cargos de cada empresa; una empresa que no está acá
    // muestra todos sus cargos con sus fechas reales
    function estructuraCv(t, dato) {
        t.cuerpo.append(nodo('p', 'Cómo aparecen los cargos de cada empresa en el CV. «todos»: cada cargo con su título ' +
            'y sus fechas reales. «segun_oferta»: los cargos separados si todos suman para la oferta; si no, uno solo ' +
            'con el título unificado y el período de la empresa. Topes en 0 = sin tope. Siempre queda al menos 1 viñeta por cargo.'));
        const entre = campo(t, t.cuerpo, 'Un cargo suma si alguna viñeta está entre las N de más puntaje (N)', 'text',
            String(dato.cargos_relevantes_entre ?? 10));
        const numero = v => { const n = parseInt(String(v).trim(), 10); return Number.isFinite(n) ? n : 0; };
        const filas = tabla(t, 'Empresas', dato.empresas, [
            ['empresa', 'Empresa (como en tu experiencia)', 'text'], ['cargos', 'Cargos en el CV', 'opciones', [['todos', 'Cada cargo con su título y fechas'], ['segun_oferta', 'Según la oferta (si no todos suman, uno solo con el título unificado)']]],
            ['titulo_unificado', 'Título unificado', 'text'], ['titulo_unificado_en', 'Título unificado en inglés', 'text'],
            ['vinetas_unificado', 'Viñetas con el título unificado (tope)', 'text'],
            ['vinetas_max_por_cargo', 'Viñetas por cargo (tope)', 'text'],
            ['largo_max_vineta', 'Caracteres por viñeta (tope, para sintetizar)', 'text'],
        ], {empresa: '', cargos: 'todos'}, 'Agregar empresa');
        const general = cvGeneral(t, dato.cv_simple);
        t.contenido = () => {
            const nuevo = {...copia(t.original), cargos_relevantes_entre: numero(entre.value) || 10,
                empresas: filas().map(f => ({...f, vinetas_unificado: numero(f.vinetas_unificado),
                    vinetas_max_por_cargo: numero(f.vinetas_max_por_cargo), largo_max_vineta: numero(f.largo_max_vineta)}))};
            const cv = general();
            if (cv) nuevo.cv_simple = cv; else delete nuevo.cv_simple;
            return JSON.stringify(nuevo);
        };
        t.confirmar = nuevo => dibujar(t, nuevo);
    }
    // CV general (creador, 15/11; en los datos, «cv_simple»): para ofertas no técnicas o ajenas al perfil. La IA decide
    // en cada oferta si va; la casilla lo activa o lo apaga. Sin el bloque en los datos y apagado, no se guarda nada
    function cvGeneral(t, original) {
        const hay = esObjeto(original);
        const cs = hay ? original : {};
        const entero = (v, def) => { const n = parseInt(String(v).trim(), 10); return Number.isFinite(n) && n > 0 ? n : def; };
        t.cuerpo.append(nodo('h3', 'CV general'));
        t.cuerpo.append(nodo('p', 'Para ofertas que no son técnicas o no tienen que ver con tu perfil (comercio, depósito, ' +
            'atención, limpieza…). La IA decide en cada oferta si va este CV: cada empresa con su mejor viñeta, en lenguaje ' +
            'simple, con las habilidades de las secciones que elijas y sin años de experiencia en el perfil.'));
        const activo = campo(t, t.cuerpo, 'Activado', 'checkbox', hay && cs.activo !== false);
        const uno = campo(t, t.cuerpo, 'Un solo cargo por empresa (la IA lo nombra a partir de tus cargos reales)',
            'checkbox', cs.un_cargo_por_empresa === true);
        const llenar = campo(t, t.cuerpo, 'Llenar la página', 'checkbox', cs.llenar_pagina === true);
        const titular = campo(t, t.cuerpo, 'Titular bajo tu nombre (vacío = sin titular)', 'text', cs.titular ?? '');
        const titularEn = campo(t, t.cuerpo, 'Titular en inglés', 'text', cs.titular_en ?? '');
        const vinetas = campo(t, t.cuerpo, 'Viñetas por cargo', 'number', cs.vinetas_por_cargo ?? 1);
        const secciones = campoLista(t, t.cuerpo, 'Secciones de tu CV maestro de donde salen las habilidades (una por renglón)',
            cs.secciones_habilidades ?? ['habilidades interpersonales'], 3);
        const lineas = campo(t, t.cuerpo, 'Líneas de habilidades', 'number', cs.lineas_habilidades ?? 3);
        const tituloH = campo(t, t.cuerpo, 'Título de la sección de habilidades', 'text', cs.titulo_habilidades ?? 'HABILIDADES');
        const tituloHEn = campo(t, t.cuerpo, 'Título de la sección de habilidades en inglés', 'text', cs.titulo_habilidades_en ?? 'SKILLS');
        return () => {
            if (!hay && !activo.checked) return null;
            return {...copia(cs), activo: activo.checked, un_cargo_por_empresa: uno.checked, llenar_pagina: llenar.checked,
                titular: titular.value.trim(), titular_en: titularEn.value.trim(), vinetas_por_cargo: entero(vinetas.value, 1),
                secciones_habilidades: deLineas(secciones.value), lineas_habilidades: entero(lineas.value, 3),
                titulo_habilidades: tituloH.value.trim(), titulo_habilidades_en: tituloHEn.value.trim()};
        };
    }
    // Aprendizaje (creador, 15/11): qué tienen en común las ofertas con puntaje bajo y no tienen las demás. Solo informa
    const ORIGEN_TERMINO = {puesto: 'en el puesto', brecha: 'brecha', aviso: 'en el aviso'};
    function aprendizajePuntaje(t, dato) {
        t.cuerpo.append(nodo('p', 'Palabras que se repiten en las ofertas con puntaje menor que el umbral y casi no ' +
            'aparecen en las demás. No descarta nada.'));
        const umbral = campo(t, t.cuerpo, 'Umbral de puntaje bajo (%)', 'number', dato.umbral ?? '');
        const minimo = campo(t, t.cuerpo, 'Mínimo de ofertas en que aparece', 'number', dato.min_ofertas ?? '');
        const lista = nodo('div');
        t.cuerpo.append(lista);
        t.contenido = () => JSON.stringify({umbral: Number(umbral.value), min_ofertas: parseInt(minimo.value, 10)});
        t.confirmar = nuevo => dibujar(t, nuevo);
        senalesPuntaje(lista);
    }
    async function senalesPuntaje(lista) {
        limpiar(lista);
        lista.append(nodo('p', 'Calculando…'));
        try {
            const r = await pedir('/api/candidato/puntaje-bajo');
            limpiar(lista);
            if (r.error) { lista.append(nodo('p', r.error)); return; }
            lista.append(nodo('p', `${r.bajas} ofertas con puntaje menor que ${r.umbral} %, ${r.resto} con más.`));
            if (!Array.isArray(r.terminos) || !r.terminos.length) { lista.append(nodo('p', 'Todavía no hay nada en común.')); return; }
            const ul = nodo('ul', undefined, 'perfil-puntaje-bajo');
            for (const x of r.terminos) {
                const ejemplos = Array.isArray(x.ejemplos) && x.ejemplos.length ? ` · ej.: ${x.ejemplos.join('; ')}` : '';
                ul.append(nodo('li', `«${x.termino}» (${ORIGEN_TERMINO[x.origen] || x.origen}): en ${x.bajas} de puntaje bajo ` +
                    `y ${x.resto} del resto${ejemplos}`));
            }
            lista.append(ul);
        } catch (error) {
            limpiar(lista);
            lista.append(nodo('p', mensajeError(error)));
        }
    }
    function rolesObjetivo(t, dato) {
        t.cuerpo.append(nodo('p', 'Los roles que buscás son el grupo A del análisis y del Radar. ' +
            'Uno por renglón; con «=» adelante exige el título completo. Sin roles no hay grupo A.'));
        const roles = campoLista(t, t.cuerpo, 'Roles que buscás', dato.roles_objetivo, 8);
        const areas = tabla(t, 'Áreas de tu trayectoria (el orden desempata el titular del CV)', dato.areas, [
            ['area', 'Nombre del área', 'text'], ['titulos', 'Títulos de oferta que la detectan (uno por renglón)', 'lista'],
            ['sinonimos', 'Sinónimos para la afinidad (expresiones regulares, una por renglón)', 'lista'],
            ['nombres', 'Cómo aparece en tus años por área (uno por renglón; vacío = el nombre del área)', 'lista'],
            ['perfil_actual', 'Es tu perfil actual (si no, sus roles no van al titular)', 'checkbox'],
        ], {area: '', titulos: [], perfil_actual: true}, 'Agregar área de trayectoria');
        t.contenido = () => JSON.stringify({...copia(t.original), roles_objetivo: deLineas(roles.value), areas: areas()});
        t.confirmar = nuevo => dibujar(t, nuevo);
    }
    function stack(t, dato) {
        t.cuerpo.append(nodo('p', 'Lo que el análisis sin IA (cuando falla la IA) valida por palabra. ' +
            'Sin esto, el análisis sin IA no valida nada. Se guarda junto con Habilidades transferibles ' +
            'y Habilidades del CV de respaldo.'));
        const coincidencias = tabla(t, 'Coincidencias directas', dato.coincidencias, [
            ['nombre', 'Nombre', 'text'], ['palabras', 'Palabras que la detectan (una por renglón)', 'lista'],
            ['justificacion', 'Justificación', 'text'],
        ], {nombre: '', palabras: [], justificacion: ''}, 'Agregar coincidencia', t.cuerpo, false);
        // Rediseño E3 (§6): transferibles y habilidades base van en sus propias tarjetas; mismo archivo y guardado
        const cuerpoTransferibles = transferiblesEl.querySelector('.perfil-extra-cuerpo');
        const cuerpoRespaldo = respaldoEl.querySelector('.perfil-extra-cuerpo');
        limpiar(cuerpoTransferibles);
        limpiar(cuerpoRespaldo);
        t.extra = [transferiblesEl, respaldoEl];
        const transferibles = tabla(t, 'Transferibles', dato.transferibles, [
            ['nombre', 'Nombre', 'text'], ['palabras', 'Palabras que la detectan (una por renglón)', 'lista'],
            ['equivalente', 'Lo que tenés equivalente', 'text'],
        ], {nombre: '', palabras: [], equivalente: ''}, 'Agregar transferible', cuerpoTransferibles, false);
        const base = esObjeto(dato.habilidades_base) ? dato.habilidades_base : {};
        const gestion = campoLista(t, cuerpoRespaldo, 'Gestión y metodologías (una por renglón)', base.gestion, 5);
        const tecnicas = campoLista(t, cuerpoRespaldo, 'Técnicas (una por renglón)', base.tecnicas, 7);
        t.contenido = () => {
            const original = copia(t.original);
            return JSON.stringify({...original, coincidencias: coincidencias(), transferibles: transferibles(),
                habilidades_base: {...(esObjeto(original.habilidades_base) ? original.habilidades_base : {}),
                    gestion: deLineas(gestion.value), tecnicas: deLineas(tecnicas.value)}});
        };
        t.confirmar = nuevo => dibujar(t, nuevo);
    }
    function conocimientosAusentes(t, dato) {
        t.cuerpo.append(nodo('p', 'Lo que no tenés: el CV no lo incluye y el análisis sin IA lo marca como brecha.'));
        const lista = campoLista(t, t.cuerpo, 'Conocimientos que no tenés (uno por renglón)', dato.conocimientos, 6);
        t.contenido = () => JSON.stringify({...copia(t.original), conocimientos: deLineas(lista.value)});
    }
    function restricciones(t, dato) {
        const campos = {};
        for (const [clave, titulo, tipo] of [
            ['base', 'Base', 'text'], ['presencial_hibrido_permitido', 'Presencial o híbrido en', 'text'],
            ['remoto_desde', 'Remoto desde', 'text'],
            ['viajes_frecuentes_o_interior', 'Acepta viajes frecuentes o al interior', 'checkbox'],
            ['reubicacion', 'Acepta reubicación', 'checkbox'],
            ['rubro_o_funcion_exigida_sin_experiencia', 'Postularte aunque pidan experiencia en un rubro o función que no tenés', 'checkbox'],
        ]) {
            campos[clave] = {titulo, tipo, input: campo(t, t.cuerpo, titulo, tipo, dato[clave])};
        }
        const edades = nodo('div', undefined, 'perfil-fila');
        t.cuerpo.append(edades);
        const actual = campo(t, edades, 'Edad', 'number', dato.edad?.actual);
        actual.min = '1'; actual.max = '129'; actual.step = '1';
        const al = campo(t, edades, 'Edad al', 'date', dato.edad?.al);
        const tituloLocalidades = 'Localidades donde aceptás presencial o híbrido (una por renglón; sin ninguna no se descarta por ubicación)';
        const localidades = campoLista(t, t.cuerpo, tituloLocalidades, dato.localidades_permitidas, 6);
        // Residencia (15/11): un requisito de «residir en…» se compara con donde vivís, no con donde aceptás trabajar
        const tituloResidencia = 'Dónde vivís: localidad, zona, región y país (uno por renglón; una oferta que exige residir en otro lugar se descarta)';
        const residencia = campoLista(t, t.cuerpo, tituloResidencia, dato.residencia, 4);
        const tituloCerca = 'Lugares de los que vivís cerca (uno por renglón; «residir en ese lugar o alrededores» no descarta)';
        const cerca = campoLista(t, t.cuerpo, tituloCerca, dato.residencia_cerca_de, 2);
        t.contenido = () => {
            const nuevo = copia(t.original);
            for (const [clave, c] of Object.entries(campos)) {
                if (t.cambios.has(c.titulo)) nuevo[clave] = c.tipo === 'checkbox' ? c.input.checked : c.input.value;
            }
            if (t.cambios.has(tituloLocalidades)) nuevo.localidades_permitidas = deLineas(localidades.value);
            if (t.cambios.has(tituloResidencia)) nuevo.residencia = deLineas(residencia.value);
            if (t.cambios.has(tituloCerca)) nuevo.residencia_cerca_de = deLineas(cerca.value);
            if (t.cambios.has('Edad') || t.cambios.has('Edad al')) {
                nuevo.edad = {...nuevo.edad};
                if (t.cambios.has('Edad')) nuevo.edad.actual = actual.value === '' ? '' : Number(actual.value);
                if (t.cambios.has('Edad al')) nuevo.edad.al = al.value;
            }
            return JSON.stringify(nuevo);
        };
    }
    // «Título (minutos)  desde [x] a [y] min»: cada número conserva su nombre accesible de siempre
    function rangoMinutos(t, contenedor, ruta, titulo, dato) {
        const grupo = nodo('div', undefined, 'perfil-campo perfil-rango');
        grupo.setAttribute('role', 'group');
        const rotulo = nodo('span', `${titulo} (minutos)`, 'perfil-rango-titulo');
        rotulo.id = `perfil-campo-${++secuencia}`;
        grupo.setAttribute('aria-labelledby', rotulo.id);
        const linea = nodo('div', undefined, 'perfil-rango-linea');
        const base = ruta.reduce((o, k) => (esObjeto(o) ? o[k] : undefined), dato);
        const campos = ['desde', 'hasta'].map((extremo) => {
            const nombre = `${titulo}: ${extremo} (min)`;
            const input = nodo('input');
            input.type = 'number'; input.min = '1'; input.step = '1';
            input.id = `perfil-campo-${++secuencia}`;
            input.setAttribute('aria-label', nombre);
            const valor = esObjeto(base) ? base[extremo] : undefined;
            input.value = valor == null ? '' : String(valor);
            input.addEventListener('input', () => editar(t, nombre));
            return {ruta: [...ruta, extremo], titulo: nombre, input};
        });
        linea.append(nodo('span', 'desde', 'perfil-rango-texto'), campos[0].input,
            nodo('span', 'a', 'perfil-rango-texto'), campos[1].input, nodo('span', 'min', 'perfil-rango-texto'));
        grupo.append(rotulo, linea);
        contenedor.append(grupo);
        return campos;
    }

    function reglasRadar(t, dato) {
        t.cuerpo.append(nodo('p', 'Sin estas reglas completas el Radar no lee nada. Rigen desde la próxima vuelta.'));
        const campos = [];
        for (const [grupo, items] of REGLAS_RADAR) {
            t.cuerpo.append(nodo('h3', grupo));
            const fila = nodo('div', undefined, 'perfil-fila');
            t.cuerpo.append(fila);
            for (const [ruta, titulo, tipo] of items) {
                if (tipo === 'rango') {
                    campos.push(...rangoMinutos(t, fila, ruta, titulo, dato));
                    continue;
                }
                const valor = ruta.reduce((o, k) => (esObjeto(o) ? o[k] : undefined), dato);
                const input = campo(t, fila, titulo, 'number', valor);
                input.min = '1'; input.step = '1';
                campos.push({ruta, titulo, input});
            }
        }
        t.cuerpo.append(nodo('h3', 'Cuentas de LinkedIn (nunca se usa otra: si la elegida no puede, no se lee)'));
        const filaCuentas = nodo('div', undefined, 'perfil-fila');
        t.cuerpo.append(filaCuentas);
        const cuentas = CUENTAS_RADAR.map(([clave, titulo]) => {
            const grupo = nodo('div', undefined, 'perfil-campo');
            const label = nodo('label', titulo);
            const select = nodo('select');
            select.id = `perfil-campo-${++secuencia}`;
            label.htmlFor = select.id;
            for (const [valor, texto] of OPCIONES_CUENTA) {
                const opcion = nodo('option', texto);
                opcion.value = valor;
                select.append(opcion);
            }
            select.value = OPCIONES_CUENTA.some(([v]) => v && v === dato[clave]) ? dato[clave] : '';
            select.addEventListener('change', () => editar(t, titulo));
            grupo.append(label, select);
            filaCuentas.append(grupo);
            return {clave, titulo, select};
        });
        t.contenido = () => {
            const nuevo = copia(t.original);
            for (const {ruta, titulo, input} of campos) {
                if (!t.cambios.has(titulo)) continue;
                const texto = input.value.trim();
                // Un valor que no es un entero viaja tal cual: el servidor lo rechaza con el campo (sin inventar)
                const valor = /^\d+$/.test(texto) ? Number(texto) : texto;
                let destino = nuevo;
                for (const k of ruta.slice(0, -1)) {
                    if (!esObjeto(destino[k])) destino[k] = {};
                    destino = destino[k];
                }
                destino[ruta[ruta.length - 1]] = valor;
            }
            for (const {clave, titulo, select} of cuentas) {
                if (!t.cambios.has(titulo)) continue;
                if (select.value) nuevo[clave] = select.value;
                else delete nuevo[clave];
            }
            return JSON.stringify(nuevo);
        };
    }
    function reglasPortales(t, dato) {
        t.cuerpo.append(nodo('p', 'Cada portal corre solo si tiene todas sus reglas. Marcá en cada búsqueda ' +
            'en qué portales se busca (tarjeta «Búsquedas»). Rigen desde la próxima vuelta del Radar. ' +
            'La ubicación de cada portal se edita en Qué busco.'));
        const campos = [];
        // Rediseño E3 (§6): «Ubicación» va en Qué busco › Ubicación que usa cada portal; se guarda con esta tarjeta
        const filaUbicacion = ubicaciones ? ubicaciones.querySelector('.perfil-fila') : null;
        if (filaUbicacion) limpiar(filaUbicacion);
        t.extra = ubicaciones || null;
        for (const [portal, titulo] of PORTALES) {
            t.cuerpo.append(nodo('h3', titulo));
            const regla = esObjeto(dato[portal]) ? dato[portal] : {};
            const rotuloActivo = `${NOMBRE_PORTAL[portal]}: encendido`;
            const activo = campo(t, t.cuerpo, rotuloActivo, 'checkbox', regla.activo);
            activo.dataset.portal = portal;
            campos.push({portal, clave: 'activo', titulo: rotuloActivo, input: activo, tipo: 'checkbox'});
            const fila = nodo('div', undefined, 'perfil-fila');
            t.cuerpo.append(fila);
            for (const [clave, texto, tipo] of [...REGLAS_PORTAL, ...(PROPIOS_PORTAL[portal] || [])]) {
                const rotulo = `${NOMBRE_PORTAL[portal]}: ${texto}`;
                const destino = clave === 'ubicacion' && filaUbicacion ? filaUbicacion : fila;
                const input = tipo === 'lista' ? campoLista(t, destino, rotulo, regla[clave], 4)
                    : campo(t, destino, rotulo, tipo, regla[clave]);
                if (tipo === 'number') { input.min = '1'; input.step = '1'; }
                campos.push({portal, clave, titulo: rotulo, input, tipo});
            }
        }
        // Duplicados entre portales: misma empresa y título parecido dentro de unos días
        t.cuerpo.append(nodo('h3', 'Ofertas repetidas entre portales'));
        const duplicados = esObjeto(dato.duplicados) ? dato.duplicados : {};
        const filaDup = nodo('div', undefined, 'perfil-fila');
        t.cuerpo.append(filaDup);
        for (const [clave, texto] of [['umbral_titulo', 'Parecido mínimo del título (1 a 100)'], ['dias', 'Dentro de cuántos días']]) {
            const rotulo = `Repetidas: ${texto}`;
            const input = campo(t, filaDup, rotulo, 'number', duplicados[clave]);
            input.min = '1'; input.step = '1';
            if (clave === 'umbral_titulo') input.max = '100';
            campos.push({portal: 'duplicados', clave, titulo: rotulo, input, tipo: 'number'});
        }
        t.contenido = () => {
            const nuevo = copia(t.original);
            for (const {portal, clave, titulo, input, tipo} of campos) {
                if (!t.cambios.has(titulo)) continue;
                if (!esObjeto(nuevo[portal])) nuevo[portal] = {};
                if (tipo === 'checkbox') nuevo[portal][clave] = input.checked;
                else if (tipo === 'number') {
                    const texto = input.value.trim();
                    // Un valor que no es un entero viaja tal cual: el servidor lo rechaza con el campo
                    nuevo[portal][clave] = /^\d+$/.test(texto) ? Number(texto) : texto;
                } else if (tipo === 'lista') nuevo[portal][clave] = deLineas(input.value);
                else nuevo[portal][clave] = input.value.trim();
            }
            return JSON.stringify(nuevo);
        };
    }
    function areas(t, dato) {
        const filas = nodo('div', undefined, 'perfil-filas');
        const modelos = [];
        t.cuerpo.append(filas);
        function agregar(original, nueva = false) {
            const fila = nodo('div', undefined, 'perfil-area');
            const modelo = {original: copia(original), fila, cambios: new Set()};
            for (const [clave, titulo] of [['area', 'Área'], ['anios', 'Años'], ['reemplaza', 'Reemplaza']]) {
                const valor = clave === 'reemplaza' ? (original.reemplaza || []).join(', ') : original[clave];
                modelo[clave] = campo(t, fila, titulo, 'text', valor, () => modelo.cambios.add(clave));
            }
            modelo.nueva = nueva;
            fila.append(boton('Quitar', () => {
                editar(t, 'filas');
                modelos.splice(modelos.indexOf(modelo), 1);
                fila.remove();
                agregarBoton.disabled = modelos.length >= 8;
            }));
            modelos.push(modelo);
            filas.append(fila);
        }
        for (const entrada of (Array.isArray(dato) ? dato : dato.anios_por_area)) agregar(entrada);
        const agregarBoton = boton('Agregar área', () => {
            if (modelos.length >= 8) return;
            editar(t, 'filas');
            agregar({area: '', anios: ''}, true);
            agregarBoton.disabled = modelos.length >= 8;
        });
        agregarBoton.disabled = modelos.length >= 8;
        t.cuerpo.append(agregarBoton);
        t.ajustar = () => { agregarBoton.disabled = t.pendiente || !t.lista || modelos.length >= 8; };
        t.contenido = () => {
            const entradas = modelos.map(m => {
                const entrada = copia(m.original);
                for (const k of ['area', 'anios', 'reemplaza']) {
                    if (m.cambios.has(k) || (m.nueva && k !== 'reemplaza')) {
                        entrada[k] = k === 'reemplaza' ? m[k].value.split(',').map(s => s.trim()).filter(Boolean) : m[k].value;
                    }
                }
                return entrada;
            });
            return JSON.stringify(Array.isArray(t.original) ? entradas : {...t.original, anios_por_area: entradas});
        };
        t.confirmar = nuevo => {
            const entradas = Array.isArray(nuevo) ? nuevo : nuevo.anios_por_area;
            modelos.forEach((m, i) => { m.original = copia(entradas[i]); m.cambios.clear(); m.nueva = false; });
        };
    }
    function interpretar(t, respuesta) {
        if (typeof respuesta.contenido !== 'string' || typeof respuesta.version !== 'string' || !respuesta.version) {
            throw new Error('No se pudo leer el dato con su versión. Recargá antes de guardar.');
        }
        if (!JSON_FORMULARIO.includes(t.clave)) return respuesta.contenido;
        let dato;
        try {
            dato = respuesta.contenido === '' && respuesta.existe === false ?
                (t.clave === 'anios_por_area_creador' ? {anios_por_area: []} : {}) : JSON.parse(respuesta.contenido);
        } catch (_) { throw new Error('No es un JSON válido. Recargá antes de guardar.'); }
        if (t.clave !== 'anios_por_area_creador') {
            if (!esObjeto(dato)) throw new Error(`${t.titulo}: el archivo no es un objeto JSON. Recargá antes de guardar.`);
        } else {
            const entradas = Array.isArray(dato) ? dato : (esObjeto(dato) ? dato.anios_por_area : null);
            if (!Array.isArray(entradas) || entradas.some(e => !esObjeto(e) ||
                typeof e.area !== 'string' || typeof e.anios !== 'string' ||
                ('reemplaza' in e && (!Array.isArray(e.reemplaza) || e.reemplaza.some(r => typeof r !== 'string'))))) {
                throw new Error('No se pudieron leer las áreas. Recargá antes de guardar.');
            }
        }
        return dato;
    }
    // ---- Editor de Experiencia: empresa → cargos (título, desde, hasta) → viñetas. Escribe experiencia.txt con
    // serializarExperiencia: lo que no edita queda intacto. Si el archivo no vuelve idéntico, se abre como texto.
    function experiencia(t, texto, comoTexto) {
        const modelo = leerExperiencia(texto);
        // Editor solo si el archivo vuelve idéntico y tiene alguna empresa («## »); si no, no habría nada que mostrar
        const seguro = experienciaSegura(texto) && modelo.lineas.some(n => n.tipo === 'seccion');
        const barra = nodo('div', undefined, 'exp-modo');
        if (comoTexto || !seguro) {
            if (!seguro) {
                barra.append(nodo('p', 'Este archivo tiene un formato que el editor no reconoce entero: se edita como texto y no se toca nada más.', 'perfil-ayuda'));
            }
            const input = campo(t, t.cuerpo, t.titulo, 'textarea', texto);
            t.contenido = () => input.value;
            if (seguro) {
                barra.append(boton('Volver al editor', () => {
                    if (!experienciaSegura(input.value) || !/^## /m.test(input.value)) { t.estado.textContent = 'El texto tiene un formato que el editor no reconoce entero: seguí como texto.'; return; }
                    limpiar(t.cuerpo); experiencia(t, input.value, false);
                }));
            }
            t.cuerpo.prepend(barra);
            return;
        }
        t.contenido = () => serializarExperiencia(modelo);
        barra.append(boton('Editar como texto', () => { const actual = serializarExperiencia(modelo); limpiar(t.cuerpo); experiencia(t, actual, true); }));
        t.cuerpo.append(barra);
        const lista = nodo('div', undefined, 'exp-lista');
        t.cuerpo.append(lista);
        const cambio = () => editar(t, 'Experiencia');
        const indice = (n) => modelo.lineas.indexOf(n);
        const redibujar = () => { limpiar(lista); dibujarSecciones(); };
        // Final del bloque de un nodo: hasta la próxima sección (o cargo, si `hastaCargo`)
        function finDeBloque(n, hastaCargo) {
            let i = indice(n) + 1;
            while (i < modelo.lineas.length && modelo.lineas[i].tipo !== 'seccion' &&
                !(hastaCargo && modelo.lineas[i].tipo === 'cargo')) i++;
            // las líneas en blanco del final quedan después de lo que se agrega
            while (i - 1 > indice(n) && modelo.lineas[i - 1].tipo === 'texto' && modelo.lineas[i - 1].texto.trim() === '') i--;
            return i;
        }
        function campoTexto(rotulo, valor, alCambiar, multilinea) {
            const grupo = nodo('div', undefined, 'perfil-campo');
            const etiqueta = nodo('label', rotulo);
            const input = nodo(multilinea ? 'textarea' : 'input');
            input.id = `perfil-campo-${++secuencia}`;
            etiqueta.htmlFor = input.id;
            if (multilinea) input.rows = 2; else input.type = 'text';
            input.value = valor;
            input.addEventListener('input', () => { alCambiar(input.value.replace(/[\r\n]+/g, ' ')); cambio(); });
            grupo.append(etiqueta, input);
            return grupo;
        }
        function vinetas(contenedor, lista, despuesDe) {
            const ul = nodo('ul', undefined, 'exp-vinetas');
            for (const v of lista) {
                const li = nodo('li', undefined, 'exp-vineta');
                li.append(campoTexto('Viñeta', v.texto, (valor) => { v.texto = valor; }, true),
                    boton('Quitar viñeta', () => { modelo.lineas.splice(indice(v), 1); cambio(); redibujar(); }));
                ul.append(li);
            }
            contenedor.append(ul, boton('Agregar viñeta', () => {
                const pos = lista.length ? indice(lista[lista.length - 1]) + 1 : indice(despuesDe) + 1;
                modelo.lineas.splice(pos, 0, {tipo: 'vineta', texto: ''});
                cambio(); redibujar();
            }));
        }
        function dibujarSecciones() {
            for (const sec of seccionesExperiencia(modelo)) {
                const caja = nodo('div', undefined, 'exp-empresa');
                caja.append(campoTexto('Empresa', sec.nombre, (valor) => { sec.nodo.nombre = valor; }));
                for (const c of sec.cargos) {
                    const cargo = nodo('div', undefined, 'exp-cargo');
                    const fila = nodo('div', undefined, 'perfil-fila');
                    fila.append(campoTexto('Cargo', c.nodo.titulo, (valor) => { c.nodo.titulo = valor; }),
                        campoTexto('Desde', c.nodo.desde, (valor) => { c.nodo.desde = valor.trim(); }),
                        campoTexto('Hasta', c.nodo.hasta, (valor) => { c.nodo.hasta = valor.trim(); }));
                    cargo.append(fila);
                    vinetas(cargo, c.vinetas, c.nodo);
                    cargo.append(boton('Quitar cargo', () => {
                        modelo.lineas.splice(indice(c.nodo), finDeBloque(c.nodo, true) - indice(c.nodo));
                        cambio(); redibujar();
                    }));
                    caja.append(cargo);
                }
                if (sec.vinetas.length || !sec.cargos.length) vinetas(caja, sec.vinetas, sec.nodo);
                caja.append(boton('Agregar cargo', () => {
                    modelo.lineas.splice(finDeBloque(sec.nodo, false), 0,
                        {tipo: 'cargo', titulo: 'Nuevo cargo', desde: 'AAAA', hasta: 'AAAA'}, {tipo: 'vineta', texto: ''});
                    cambio(); redibujar();
                }));
                lista.append(caja);
            }
            lista.append(boton('Agregar empresa', () => {
                // Antes de las secciones de habilidades, si las hay; si no, al final
                const habilidades = modelo.lineas.findIndex(n => n.tipo === 'seccion' && /^HABILIDADES/i.test(n.nombre));
                const pos = habilidades >= 0 ? habilidades : modelo.lineas.length;
                modelo.lineas.splice(pos, 0, {tipo: 'seccion', nombre: 'Nueva empresa'},
                    {tipo: 'cargo', titulo: 'Nuevo cargo', desde: 'AAAA', hasta: 'AAAA'}, {tipo: 'vineta', texto: ''}, {tipo: 'texto', texto: ''});
                cambio(); redibujar();
            }));
        }
        dibujarSecciones();
    }
    const ROTULO_CONTACTO = {ciudad: 'Ciudad', telefono: 'Teléfono', whatsapp: 'WhatsApp', email: 'Email',
        linkedin: 'LinkedIn', otro: 'Otro dato de contacto'};
    const ROTULO_ITEM = {'EDUCACIÓN': 'Estudio', 'IDIOMAS': 'Idioma'};
    function personales(t, texto, comoTexto) {
        const seguro = personalesSeguro(texto);
        const modelo = leerPersonales(texto);
        const barra = nodo('div', undefined, 'exp-modo');
        if (comoTexto || !seguro) {
            if (!seguro && texto.includes(' | ')) {
                barra.append(nodo('p', 'Este archivo tiene un formato que el formulario no reconoce entero: se edita como texto y no se toca nada más.', 'perfil-ayuda'));
            }
            const input = campo(t, t.cuerpo, t.titulo, 'textarea', texto);
            t.contenido = () => input.value;
            if (seguro) barra.append(boton('Volver al formulario', () => {
                if (!personalesSeguro(input.value)) { t.estado.textContent = 'El texto tiene un formato que el formulario no reconoce entero: seguí como texto.'; return; }
                limpiar(t.cuerpo); personales(t, input.value, false);
            }));
            if (barra.childNodes.length) t.cuerpo.prepend(barra);
            return;
        }
        t.contenido = () => serializarPersonales(modelo);
        barra.append(boton('Ver como texto', () => { const actual = serializarPersonales(modelo); limpiar(t.cuerpo); personales(t, actual, true); }));
        t.cuerpo.append(barra);
        const c = camposPersonales(modelo);
        const cambio = () => editar(t, 'Datos personales');
        const linea = (rotulo, valor, alCambiar, contenedor) => {
            const grupo = nodo('div', undefined, 'perfil-campo');
            const etiqueta = nodo('label', rotulo);
            const input = nodo('input');
            input.type = 'text';
            input.id = `perfil-campo-${++secuencia}`;
            etiqueta.htmlFor = input.id;
            input.value = valor;
            input.addEventListener('input', () => { alCambiar(input.value.replace(/[\r\n]+/g, ' ')); cambio(); });
            grupo.append(etiqueta, input);
            (contenedor || t.cuerpo).append(grupo);
            return input;
        };
        const datos = nodo('div', undefined, 'perfil-fila');
        t.cuerpo.append(datos);
        linea('Nombre completo', c.nombre.texto, (v) => { c.nombre.texto = v; }, datos);
        linea('Titular', c.puesto ? c.puesto.texto : '', (v) => {
            if (!c.puesto) {  // sin «Puesto:» en el archivo: se agrega debajo del nombre
                c.puesto = {tipo: 'puesto', texto: ''};
                modelo.lineas.splice(modelo.lineas.indexOf(c.nombre) + 1, 0, c.puesto);
            }
            c.puesto.texto = v;
        }, datos);
        if (c.contacto) {
            const contacto = nodo('div', undefined, 'perfil-fila');
            t.cuerpo.append(contacto);
            for (const parte of c.contacto.partes) {
                linea(ROTULO_CONTACTO[parte.tipo] || 'Contacto', parte.texto, (v) => { parte.texto = v; }, contacto);
            }
        }
        for (const titulo of c.titulos) {
            const items = c.secciones[titulo.texto];
            const caja = nodo('div', undefined, 'exp-empresa');
            caja.append(nodo('h3', titulo.texto.charAt(0) + titulo.texto.slice(1).toLowerCase()));
            for (const item of items) {
                const fila = nodo('div', undefined, 'exp-vineta');
                linea(ROTULO_ITEM[titulo.texto] || 'Ítem', item.texto, (v) => { item.texto = v; }, fila);
                fila.append(boton('Quitar', () => {
                    modelo.lineas.splice(modelo.lineas.indexOf(item), 1);
                    cambio(); limpiar(t.cuerpo); personales(t, serializarPersonales(modelo), false);
                    editar(t, 'Datos personales');
                }));
                caja.append(fila);
            }
            caja.append(boton(`Agregar ${(ROTULO_ITEM[titulo.texto] || 'ítem').toLowerCase()}`, () => {
                const ultimo = items.length ? items[items.length - 1] : titulo;
                modelo.lineas.splice(modelo.lineas.indexOf(ultimo) + 1, 0, {tipo: 'item', texto: ''});
                limpiar(t.cuerpo); personales(t, serializarPersonales(modelo), false);
                editar(t, 'Datos personales');
            }));
            t.cuerpo.append(caja);
        }
    }
    function dibujar(t, dato) {
        limpiar(t.cuerpo);
        t.ajustar = null; t.confirmar = null;
        if (t.clave === 'experiencia') experiencia(t, dato, false);
        else if (t.clave === 'personales') personales(t, dato, false);
        else if (t.clave === 'restricciones') restricciones(t, dato);
        else if (t.clave === 'anios_por_area_creador') areas(t, dato);
        else if (t.clave === 'roles_objetivo') rolesObjetivo(t, dato);
        else if (t.clave === 'stack') stack(t, dato);
        else if (t.clave === 'conocimientos_ausentes') conocimientosAusentes(t, dato);
        else if (t.clave === 'reglas_radar') reglasRadar(t, dato);
        else if (t.clave === 'estructura_cv') estructuraCv(t, dato);
        else if (t.clave === 'aprendizaje_puntaje') aprendizajePuntaje(t, dato);
        else if (t.clave === 'reglas_portales') reglasPortales(t, dato);
        else {
            const input = campo(t, t.cuerpo, t.titulo, 'textarea', dato);
            t.contenido = () => input.value;
        }
    }
    async function cargar(t, forzar = false) {
        if (t.pendiente || (!forzar && t.cambios.size)) return;
        const revision = t.revision;
        // El formulario ya cargado sigue editable durante un GET de refresco.
        // Una edición posterior invalida esa respuesta, incluida su versión.
        if (!t.lista) pendiente(t, true);
        const turno = ++t.turno;
        try {
            const respuesta = await pedir(`/api/candidato/perfil/${t.clave}`);
            if (turno !== t.turno || revision !== t.revision) return;
            const dato = interpretar(t, respuesta);
            t.original = dato; t.version = respuesta.version;
            t.cambios.clear(); t.lista = true;
            dibujar(t, dato);
            t.estado.textContent = '';
            t.recargar.hidden = true;
        } catch (error) {
            if (turno !== t.turno || revision !== t.revision) return;
            t.lista = false;
            t.estado.textContent = mensajeError(error);
            t.recargar.hidden = false;
        } finally {
            if (turno === t.turno) {
                pendiente(t, false);
                if (t.ajustar) t.ajustar();
            }
            actualizarBarra();
        }
    }
    async function guardar(t) {
        if (!t.lista || t.pendiente) return;
        const contenido = t.contenido();
        // Invalida cualquier GET iniciado antes del guardado.
        ++t.turno;
        pendiente(t, true);
        t.estado.textContent = 'Guardando…';
        try {
            const respuesta = await pedir(`/api/candidato/perfil/${t.clave}`, 'PUT', {contenido, version: t.version});
            if (respuesta.ok !== true || typeof respuesta.backup !== 'string' || !respuesta.backup ||
                typeof respuesta.version !== 'string' || !respuesta.version) {
                throw new Error('No se pudo confirmar el respaldo y la versión. Recargá antes de guardar.');
            }
            t.version = respuesta.version;
            t.original = JSON_FORMULARIO.includes(t.clave) ? JSON.parse(contenido) : contenido;
            t.cambios.clear();
            if (t.confirmar) t.confirmar(t.original);
            t.estado.textContent = `Guardado. Copia de respaldo: ${respuesta.backup}.` +
                (typeof respuesta.aviso === 'string' && respuesta.aviso ? ` ${respuesta.aviso}` : '');
            t.recargar.hidden = true;
            await refrescarPreview();
        } catch (error) {
            t.estado.textContent = mensajeError(error);
            if (error.status === 409 || !error.status) t.recargar.hidden = false;
        } finally {
            pendiente(t, false);
            if (t.ajustar) t.ajustar();
            actualizarBarra();
        }
    }
    async function refrescarPreview() {
        const turno = ++previewTurno;
        try {
            const dato = await pedir('/api/candidato/anios-por-area');
            if (turno !== previewTurno) return;
            if (!Array.isArray(dato.areas)) throw new Error('No se pudo leer la vista previa de años por área.');
            limpiar(vistaPrevia);
            if (!dato.vigente || !dato.areas.length) {
                vistaPrevia.append(nodo('p', 'Se calculan con IA en el próximo CV.'));
            } else {
                const lista = nodo('ul');
                for (const area of dato.areas) lista.append(nodo('li', `${area.area}: ${area.anios}`));
                vistaPrevia.append(lista);
            }
        } catch (error) {
            if (turno === previewTurno) {
                limpiar(vistaPrevia);
                vistaPrevia.append(nodo('p', `No se pudo actualizar la vista previa. ${mensajeError(error)}`));
            }
        }
    }
    async function cargarAprendizajes(t) {
        if (t.pendiente) return;
        pendiente(t, true);
        try {
            const dato = await pedir('/api/candidato/aprendizajes');
            if (!esObjeto(dato.aprendizajes)) throw new Error('No se pudieron leer los aprendizajes.');
            t.aprendizajes = dato.aprendizajes;
            t.lista = true;
            dibujarAprendizajes(t);
            actualizarBarra();
            t.estado.textContent = '';
            t.recargar.hidden = true;
        } catch (error) {
            t.estado.textContent = mensajeError(error);
            t.recargar.hidden = false;
        } finally { pendiente(t, false); }
    }
    function dibujarAprendizajes(t) {
        limpiar(t.cuerpo);
        const entradas = Object.entries(t.aprendizajes);
        const etiquetas = {MATCH_DIRECTO: 'Match', TRANSFERIBLE: 'Transferible', BRECHA_REAL: 'Brecha'};
        if (!entradas.length) t.cuerpo.append(nodo('p', 'No hay aprendizajes guardados.'));
        for (const [clave, dato] of entradas) {
            const fila = nodo('div', undefined, 'perfil-aprendizaje');
            const tag = typeof dato.tag === 'string' ? dato.tag : clave;
            const estado = Object.prototype.hasOwnProperty.call(etiquetas, dato.estado)
                ? etiquetas[dato.estado] : dato.estado || '';
            fila.append(nodo('span', estado ? `${tag} · ${estado}` : tag));
            fila.append(boton('Borrar', async () => {
                if (t.pendiente || !window.confirm(`¿Borrar el aprendizaje ${tag}?`)) return;
                pendiente(t, true);
                t.estado.textContent = 'Borrando…';
                try {
                    const respuesta = await pedir(`/api/candidato/aprendizajes/${encodeURIComponent(clave)}`, 'DELETE');
                    if (respuesta.ok !== true || typeof respuesta.backup !== 'string' || !respuesta.backup) {
                        throw new Error('No se pudo confirmar el respaldo del borrado.');
                    }
                    delete t.aprendizajes[clave];
                    dibujarAprendizajes(t);
                    t.estado.textContent = `Guardado. Copia de respaldo: ${respuesta.backup}.`;
                    await refrescarPreview();
                } catch (error) { t.estado.textContent = mensajeError(error); }
                finally { pendiente(t, false); }
            }));
            t.cuerpo.append(fila);
        }
    }
    async function regenerar() {
        if (botonLote.disabled) return;
        botonLote.disabled = true;
        estadoLote.textContent = 'Contando CV listos sin aprobar…';
        try {
            const listas = await Promise.all([
                pedir('/api/radar/posts?estado=LISTO'), pedir('/api/radar/posts?estado=LISTO&fuente=manual'),
            ]);
            if (listas.some(l => !Array.isArray(l))) throw new Error('No se pudo contar los CV listos.');
            const ids = new Set(listas.flat().filter(p => p.estado === 'LISTO' && p.aprobado_at == null).map(p => p.id));
            if (!ids.size) { estadoLote.textContent = 'No hay CV listos sin aprobar.'; return; }
            if (!window.confirm(`Se van a regenerar ${ids.size} CV listos sin aprobar. ¿Seguir?`)) {
                estadoLote.textContent = ''; return;
            }
            const respuesta = await pedir('/api/radar/regenerar-listos', 'POST', {});
            if (!Number.isInteger(respuesta.regenerados) || respuesta.regenerados < 0) {
                throw new Error('No se pudo confirmar cuántos CV se regeneran.');
            }
            estadoLote.textContent = respuesta.regenerados ? `Se regeneran ${respuesta.regenerados} CV.` : 'No hay CV listos sin aprobar.';
        } catch (error) { estadoLote.textContent = mensajeError(error); }
        finally { botonLote.disabled = false; }
    }
    // Búsquedas del Radar (12/11): la consulta es de solo lectura; el promedio por lectura aparece solo si el Radar
    // ya las leyó. Los portales y sus palabras (14/11) se editan acá.
    const METRICAS = [
        ['leidos', 'leído', 'leídos'], ['nuevos', 'nuevo', 'nuevos'], ['con_mail', 'con mail', 'con mail'],
        ['ofertas', 'oferta', 'ofertas'], ['matches', 'match', 'matches'],
        ['descartados', 'descartado', 'descartados'], ['no_oferta', 'no era oferta', 'no eran oferta'],
        ['pendientes', 'sin procesar', 'sin procesar'],
    ];
    const numero = v => v.toLocaleString('es-AR', {maximumFractionDigits: 1});
    const entero = v => Number.isInteger(v) && v > 0;
    function lineaResultados(r) {
        if (!esObjeto(r) || !entero(r.lecturas) || !entero(r.dias) || !esObjeto(r.promedio)) return null;
        const partes = METRICAS
            .filter(([clave]) => typeof r.promedio[clave] === 'number' && Number.isFinite(r.promedio[clave]) && r.promedio[clave] >= 0)
            .map(([clave, uno, varios]) => `${numero(r.promedio[clave])} ${r.promedio[clave] === 1 ? uno : varios}`);
        if (!partes.length) return null;
        const lecturas = r.lecturas === 1 ? '1 lectura' : `${r.lecturas} lecturas`;
        return `Promedio por lectura (${lecturas}, últimos ${r.dias} días): ${partes.join(' · ')}`;
    }
    // Portales de una búsqueda (14/11): casillas y palabras simples (los portales no tienen sintaxis booleana)
    function controlesPortales(c, b, portales) {
        const caja = nodo('div', undefined, 'perfil-portales');
        const modelo = {nombre: b.nombre, casillas: {}, palabras: null};
        const actuales = Array.isArray(b.portales) ? b.portales : [];
        for (const portal of portales) {
            const grupo = nodo('div', undefined, 'perfil-casilla');
            const input = nodo('input');
            input.type = 'checkbox';
            input.id = `perfil-campo-${++secuencia}`;
            input.checked = actuales.includes(portal);
            input.dataset.portal = portal;
            input.addEventListener('change', () => { c.cambios.add(b.nombre); actualizarBarra(); });
            const label = nodo('label', NOMBRE_PORTAL[portal] || portal);
            label.htmlFor = input.id;
            grupo.append(input, label);
            caja.append(grupo);
            modelo.casillas[portal] = input;
        }
        const grupo = nodo('div', undefined, 'perfil-campo');
        const label = nodo('label', 'Palabras para portales (sin comillas ni AND/OR)');
        const input = nodo('input');
        input.type = 'text';
        input.id = `perfil-campo-${++secuencia}`;
        label.htmlFor = input.id;
        input.value = typeof b.palabras === 'string' ? b.palabras : '';
        input.placeholder = 'analista funcional';
        input.addEventListener('input', () => { c.cambios.add(b.nombre); actualizarBarra(); });
        grupo.append(label, input);
        caja.append(grupo);
        modelo.palabras = input;
        c.modelos.push(modelo);
        return caja;
    }
    async function guardarPortales() {
        const c = busquedas;
        if (c.pendiente || !busquedasDato) return;
        const cambios = c.modelos.filter(m => c.cambios.has(m.nombre)).map(m => ({
            nombre: m.nombre,
            portales: Object.entries(m.casillas).filter(([, input]) => input.checked).map(([portal]) => portal),
            palabras: m.palabras.value.trim(),
        }));
        if (!cambios.length) { c.estado.textContent = 'No hay cambios en los portales.'; return; }
        c.pendiente = true;
        c.estado.textContent = 'Guardando…';
        try {
            const respuesta = await pedir('/api/candidato/busquedas/portales', 'PUT', {cambios, version: busquedasDato.version});
            if (respuesta.ok !== true || typeof respuesta.backup !== 'string' || !respuesta.backup) {
                throw new Error('No se pudo confirmar el respaldo. Recargá antes de guardar.');
            }
            c.cambios.clear();
            c.pendiente = false;
            await cargarBusquedas();
            c.estado.textContent = `Guardado. Copia de respaldo: ${respuesta.backup}. Rige desde la próxima vuelta.`;
        } catch (error) {
            c.estado.textContent = mensajeError(error);
        } finally {
            c.pendiente = false;
            actualizarBarra();
        }
    }
    function dibujarBusquedas(dato) {
        const c = busquedas;
        limpiar(c.cuerpo);
        busquedasDato = dato;
        actualizarBarra();
        c.modelos = [];
        c.cambios.clear();
        const portales = Array.isArray(dato.portales) ? dato.portales.filter(p => typeof p === 'string') : [];
        const editable = portales.length > 0 && typeof dato.version === 'string';
        if (!dato.busquedas.length) c.cuerpo.append(nodo('p', 'No hay búsquedas configuradas.'));
        for (const b of dato.busquedas) {
            if (!esObjeto(b) || typeof b.nombre !== 'string' || typeof b.consulta !== 'string') continue;
            const item = nodo('div', undefined, 'perfil-busqueda');
            item.dataset.busqueda = b.nombre;
            item.append(nodo('p', `${b.nombre}${b.activa === false ? ' · pausada' : ''}`, 'perfil-busqueda-nombre'));
            item.append(nodo('code', b.consulta, 'perfil-consulta'));
            const linea = lineaResultados(b.resultados);
            if (linea) item.append(nodo('p', linea, 'perfil-resultados'));
            if (editable) item.append(controlesPortales(c, b, portales));
            c.cuerpo.append(item);
        }
        const errores = dato.errores.filter(e => typeof e === 'string' && e);
        if (errores.length) {
            c.cuerpo.append(nodo('h3', 'Búsquedas con problemas (no se leen)'));
            const lista = nodo('ul');
            for (const e of errores) lista.append(nodo('li', e));
            c.cuerpo.append(lista);
        }
    }
    async function cargarBusquedas() {
        const c = busquedas;
        const turno = ++c.turno;
        c.el.setAttribute('aria-busy', 'true');
        try {
            const dato = await pedir('/api/candidato/busquedas');
            if (turno !== c.turno) return;
            if (!esObjeto(dato) || !Array.isArray(dato.busquedas) || !Array.isArray(dato.errores)) {
                throw new Error('No se pudieron leer las búsquedas.');
            }
            dibujarBusquedas(dato);
            c.estado.textContent = '';
        } catch (error) {
            if (turno !== c.turno) return;
            limpiar(c.cuerpo);
            c.estado.textContent = mensajeError(error);
        } finally {
            if (turno === c.turno) c.el.setAttribute('aria-busy', 'false');
        }
    }
    // Rediseño E3 (docs/rediseno/plan.md §6): seis secciones por tarea; cada tarjeta conserva su borrador y su versión
    const SECCIONES = [
        ['datos', 'Mis datos', 'Lo que va en todos tus CV y mails.', 'id-card',
            ['personales', 'experiencia', 'anios_por_area_creador']],
        ['sabe', 'Lo que sé', 'Lo que el análisis valida cuando la IA no responde, y lo que tu CV nunca dice.', 'book-open',
            ['stack', 'transferibles', 'respaldo', 'conocimientos_ausentes']],
        ['busco', 'Qué busco', 'Qué puestos, dónde y cómo trabajás, y qué busca el Radar por vos.', 'target',
            ['roles_objetivo', 'restricciones', 'ubicaciones', 'busquedas']],
        ['cv', 'Mi CV y mi mail', 'Cómo se arma cada CV y cómo escribe el mail.', 'file-text',
            ['voz_email', 'estructura_cv', 'regenerar']],
        ['aprendio', 'Lo que aprendió', 'De tus decisiones sobre los requisitos y de las ofertas con puntaje bajo.', 'graduation-cap',
            ['aprendizajes', 'aprendizaje_puntaje']],
        ['automatizacion', 'Automatización', 'Ritmo y límites del Radar y de los portales. Rigen desde la próxima vuelta.', 'bot',
            ['reglas_radar', 'reglas_portales']],
    ];
    function icono(nombre) {
        return typeof window.crearIcono === 'function' ? window.crearIcono(nombre) : document.createTextNode('');
    }
    function montar() {
        raiz = document.getElementById('vista-perfil');
        if (!raiz) return false;
        const pagina = nodo('div', undefined, 'perfil');
        // Índice: a la izquierda en escritorio; arriba en el celular. Cada fila lleva a su sección.
        const indice = nodo('nav', undefined, 'perfil-indice');
        indice.setAttribute('aria-label', 'Secciones de Mi perfil');
        indice.append(nodo('h2', 'Mi perfil'), nodo('p', 'Los cambios rigen para los próximos CV. Los CV ya generados no cambian.', 'perfil-ayuda'));
        const lista = nodo('ul', undefined, 'perfil-secciones');
        indice.append(lista);
        const cuerpo = nodo('div', undefined, 'perfil-cuerpo');
        pagina.append(indice, cuerpo);
        raiz.append(pagina);
        const lugar = new Map();
        for (const [id, titulo, ayuda, ico, claves] of SECCIONES) {
            const seccion = nodo('section', undefined, 'perfil-seccion');
            seccion.id = `perfil-${id}`;
            seccion.append(nodo('h2', titulo), nodo('p', ayuda, 'perfil-ayuda'));
            const grilla = nodo('div', undefined, 'perfil-grilla');
            seccion.append(grilla);
            cuerpo.append(seccion);
            for (const clave of claves) lugar.set(clave, grilla);
            const ir = nodo('button', undefined, 'perfil-ir');
            ir.type = 'button';
            ir.dataset.seccion = id;
            const textos = nodo('span', undefined, 'perfil-ir-textos');
            textos.append(nodo('span', titulo, 'perfil-ir-titulo'), nodo('span', '', 'perfil-ir-res'));
            ir.append(icono(ico), textos);
            ir.addEventListener('click', () => seccion.scrollIntoView({block: 'start', behavior: 'smooth'}));
            const item = nodo('li');
            item.append(ir);
            lista.append(item);
        }
        // «Regenerar CV listos sin aprobar» va con Mi CV y mi mail (§6)
        const lote = nodo('div', undefined, 'perfil-lote');
        botonLote = boton('Regenerar CV listos sin aprobar', regenerar);
        estadoLote = estado();
        lote.append(botonLote, estadoLote);
        lugar.get('regenerar').append(lote);
        for (const [clave, titulo] of definiciones) {
            const t = {clave, titulo, cambios:new Set(), lista:false, pendiente:false, revision:0, turno:0};
            t.el = nodo('fieldset', undefined, 'perfil-tarjeta');
            // Las tablas del perfil ocupan todo el ancho
            if (['personales', 'roles_objetivo', 'stack', 'experiencia', 'reglas_portales', 'reglas_radar'].includes(clave)) t.el.classList.add('perfil-ancha');
            t.el.append(nodo('legend', titulo));
            t.cuerpo = nodo('div');
            t.estado = estado();
            t.recargar = boton('Recargar', async () => {
                if (t.pendiente) return;
                if (t.cambios.size && !window.confirm('¿Descartar los cambios sin guardar y recargar este dato?')) return;
                if (clave === 'aprendizajes') await cargarAprendizajes(t);
                else await cargar(t, true);
            });
            t.recargar.hidden = true;
            t.el.append(t.cuerpo);
            t.el.append(t.estado, t.recargar);
            if (clave === 'anios_por_area_creador') {
                t.el.append(nodo('h3', 'Años por área que se usan hoy'));
                vistaPrevia = nodo('div'); t.el.append(vistaPrevia);
            }
            tarjetas.set(clave, t); pendiente(t, false);
        }
        busquedas = {el: nodo('fieldset', undefined, 'perfil-tarjeta perfil-busquedas perfil-ancha'), cuerpo: nodo('div'),
            estado: estado(), turno: 0, cambios: new Set(), modelos: [], pendiente: false};
        busquedas.titulo = 'Búsquedas del Radar';
        busquedas.el.append(nodo('legend', 'Búsquedas del Radar'), busquedas.cuerpo, busquedas.estado);
        barra = nodo('div', undefined, 'perfil-barra');
        barra.hidden = true;
        barra.setAttribute('role', 'region');
        barra.setAttribute('aria-label', 'Cambios sin guardar');
        barraTexto = nodo('p', '', 'perfil-barra-texto');
        barraTexto.setAttribute('aria-live', 'polite');
        botonDescartar = boton('Descartar', descartarTodo);
        botonGuardarTodo = boton('Guardar cambios', guardarTodo);
        botonGuardarTodo.classList.add('perfil-primario');
        const accionesBarra = nodo('div', undefined, 'perfil-barra-acciones');
        accionesBarra.append(botonDescartar, botonGuardarTodo);
        barra.append(barraTexto, accionesBarra);
        raiz.append(barra);
        dialogoSalir = crearDialogoSalir();
        raiz.append(dialogoSalir);
        window.addEventListener('beforeunload', (e) => { if (hayCambios()) { e.preventDefault(); e.returnValue = ''; } });
        ubicaciones = nodo('section', undefined, 'perfil-tarjeta perfil-ubicaciones perfil-ancha');
        ubicaciones.setAttribute('aria-labelledby', 'perfilUbicacionesTitulo');
        const tituloUbicaciones = nodo('h3', 'Ubicación que usa cada portal');
        tituloUbicaciones.id = 'perfilUbicacionesTitulo';
        ubicaciones.append(tituloUbicaciones,
            nodo('p', 'Cómo escribe cada portal tu ubicación en sus búsquedas. Se guarda con las reglas de Portales.', 'perfil-ayuda'),
            nodo('div', undefined, 'perfil-fila'));
        const tarjetaExtra = (clase, titulo, ayuda) => {
            const el = nodo('section', undefined, `perfil-tarjeta perfil-extra perfil-ancha ${clase}`);
            const h = nodo('h3', titulo);
            h.id = `perfil-campo-${++secuencia}`;
            el.setAttribute('aria-labelledby', h.id);
            el.append(h, nodo('p', ayuda, 'perfil-ayuda'), nodo('div', undefined, 'perfil-extra-cuerpo'));
            return el;
        };
        transferiblesEl = tarjetaExtra('perfil-transferibles', 'Habilidades transferibles',
            'Lo que piden y no tenés tal cual, pero sí algo equivalente. Se guarda con Habilidades que tenés.');
        respaldoEl = tarjetaExtra('perfil-respaldo', 'Habilidades del CV de respaldo',
            'Las habilidades del CV general, el que se usa cuando la IA no responde. Se guarda con Habilidades que tenés.');
        const extras = {ubicaciones, transferibles: transferiblesEl, respaldo: respaldoEl};
        // Cada tarjeta en su sección, en el orden del plan (§6)
        for (const [, , , , claves] of SECCIONES) {
            for (const clave of claves) {
                const el = clave === 'busquedas' ? busquedas.el : extras[clave] || (tarjetas.get(clave) || {}).el;
                if (el) lugar.get(clave).append(el);
            }
        }
        return true;
    }
    async function mostrar() {
        if (!raiz && !montar()) return;
        // Entradas concurrentes comparten la carga. Las tarjetas modificadas o
        // guardándose conservan su contenido; no hay reintentos automáticos.
        if (carga) return carga;
        carga = Promise.all([
            ...[...tarjetas.values()].map(t => t.clave === 'aprendizajes' ? cargarAprendizajes(t) : cargar(t)),
            refrescarPreview(), cargarBusquedas(),
        ]);
        try { await carga; } finally { carga = null; }
    }
    window.Perfil = {mostrar, hayCambios, pedirSalida, guardarTodo,
        _experiencia: {leer: leerExperiencia, serializar: serializarExperiencia, secciones: seccionesExperiencia, seguro: experienciaSegura},
        _personales: {leer: leerPersonales, serializar: serializarPersonales, campos: camposPersonales, seguro: personalesSeguro}};
})();
