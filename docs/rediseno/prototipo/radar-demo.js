/* Navegación del prototipo (no es la lógica del portal). Parámetros: ?paso=lista|detalle|mail&procesos=1&filtros=1&extra=1 */
(() => {
  const q = new URLSearchParams(location.search);
  const body = document.body;
  const ir = (paso) => {
    body.dataset.paso = paso;
    document.querySelectorAll('.seg [data-ir]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.ir === paso)));
    window.scrollTo(0, 0);
  };
  const abrirProcesos = (si) => {
    document.getElementById('procesos').hidden = !si;
    document.getElementById('velo').hidden = !si;
    body.dataset.extra = '0';
  };
  document.addEventListener('click', (e) => {
    const card = e.target.closest('.oferta');
    if (card && !e.target.closest('button')) {
      document.querySelectorAll('.oferta').forEach((c) => c.setAttribute('aria-selected', String(c === card)));
      ir('detalle');
      return;
    }
    const a = e.target.closest('[data-ir]');
    if (a) { ir(a.dataset.ir); return; }
    if (e.target.closest('[data-abre="procesos"]')) { abrirProcesos(true); return; }
    if (e.target.closest('[data-cierra]')) { abrirProcesos(false); return; }
    const t = e.target.closest('[data-toggle]');
    if (t) { const p = document.getElementById(t.dataset.toggle); p.hidden = !p.hidden; t.setAttribute('aria-expanded', String(!p.hidden)); return; }
    if (e.target.closest('[data-extra]')) { body.dataset.extra = body.dataset.extra === '1' ? '0' : '1'; return; }
    const c = e.target.closest('[data-cuerpo]');
    if (c) {
      const ver = c.dataset.cuerpo === 'ver';
      document.getElementById('cuerpo').hidden = ver;
      document.getElementById('vistaPrevia').hidden = !ver;
      document.querySelectorAll('[data-cuerpo]').forEach((b) => b.setAttribute('aria-selected', String(b === c)));
    }
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') abrirProcesos(false); });
  ir(q.get('paso') || 'lista');
  if (q.get('procesos') === '1') abrirProcesos(true);
  if (q.get('filtros') === '1') document.querySelector('[data-toggle="filtros"]').click();
  if (q.get('extra') === '1') body.dataset.extra = '1';
})();
