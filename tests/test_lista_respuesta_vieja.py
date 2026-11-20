"""La lista del Radar/Manual no se pisa con una respuesta vieja (falla que vio el usuario el 19/11).

Desde LinkedIn, «Nueva oferta › Varios links» pasa a Manual: eso pide la lista (A) antes de que existan las ofertas
nuevas; al responder el lote, abrirPost la pide otra vez (B). Si A llega después que B, borraba las nuevas y solo
quedaba la que se abría («aparece 1»). Medida: con dos cargas en vuelo, queda la del pedido más reciente, sin importar
el orden en que llegan las respuestas.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")

SCRIPT = r"""
global.window = global;
global.document = { addEventListener() {}, getElementById() { return null; }, querySelector() { return null; },
                    querySelectorAll() { return []; } };
global.escapeHtml = (s) => s == null ? '' : String(s);
require(%s);
const T = window.Radar._test;
const pendientes = [];
global.fetch = (url) => new Promise((resolver) => pendientes.push({ url, resolver }));
const responder = (i, posts) => pendientes[i].resolver({ ok: true, status: 200, json: async () => posts });
const post = (id) => ({ id, fuente: 'manual', estado: 'NUEVO', texto: 'https://www.ejemplo.com/empleos/' + id,
                        autor: 'Oferta manual', created_at: '2026-11-19 12:40:48', updated_at: '2026-11-19 12:40:48' });
(async () => {
  T.S.modo = 'manual';
  const a = T.cargarPosts();   // A: se pidió primero (sin las ofertas nuevas)
  const b = T.cargarPosts();   // B: se pidió después (con las 5 nuevas)
  await new Promise((r) => setTimeout(r, 10));
  responder(1, [1, 2, 3, 4, 5].map(post)); await b;
  responder(0, [post(1)]); await a;   // A llega última y vieja
  console.log(JSON.stringify([...T.S.posts.keys()].sort()));
  process.exit(0);
})();
"""


def test_la_respuesta_vieja_no_pisa_la_lista():
    js = SCRIPT % json.dumps(str(RAIZ / "static" / "radar.js"))
    salida = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert salida.returncode == 0, salida.stderr[-2000:]
    assert json.loads(salida.stdout.strip().splitlines()[-1]) == [1, 2, 3, 4, 5]
