"""Prueba funcional de render/filtro con el harness Node del repositorio.
La geometría real se audita aparte en navegador; este test no la certifica.
"""
import json
import shutil
import subprocess
from pathlib import Path
from bs4 import BeautifulSoup
from tests import test_radar_ui as harness


def node(body):
    exe=shutil.which("node")
    assert exe, "Node es necesario para validar UI; no se admite skip"
    completar = """
const crearNodoOriginal = document.createElement;
document.createElement = (tag) => {
  const n = crearNodoOriginal(tag);
  n.dataset = {}; n.style = {}; n.addEventListener = () => {};
  return n;
};
"""
    script=(harness.NODE_PRELUDE % json.dumps(str(Path("static/radar.js").resolve())))+harness.NODE_DOM_FALSO+completar+body
    r=subprocess.run([exe,"-e",script],capture_output=True,text=True,encoding="utf-8",timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_filtro_observa_condicion_sin_cambiar_estado():
    out=node("""
const posts=[{id:1,estado:'ANALIZADA',no_apto:{motivos:[{texto:'Viajar al interior'}]}},
{id:2,estado:'ANALIZADA'},{id:3,estado:'LISTO',analisis:{no_apto:{motivos:[{texto:'Presencial en Córdoba'}]}}}];
console.log(JSON.stringify(posts.filter(p=>U.coincideFiltro(p,'NO_APTO')).map(p=>p.id)));
""")
    assert out == [1,3]


def test_tarjeta_muestra_sello_y_accion_sin_truncar_titulo():
    out=node("""
const p={id:1,estado:'ANALIZADA',puesto:'Analista funcional de operaciones y gestión de incidentes',match_score:90,
no_apto:{motivos:[{requisito:'Ubicación',categoria:'UBICACION',texto:'presencial en Santiago de Chile'}]}};
const card=T.crearCard(p);
const rec=n=>(n.innerHTML||'')+' '+(n.textContent||'')+' '+(n.children||[]).map(rec).join(' ');
console.log(JSON.stringify(rec(card)));
""")
    soup=BeautifulSoup(out,"html.parser")
    texto=soup.get_text(" ",strip=True)
    assert "No apto: presencial en Santiago de Chile" in texto
    assert "Postular igual" in texto and "Descartar" in texto
    assert "Analista funcional de operaciones y gestión de incidentes" in texto
    for el in soup.find_all(string=lambda s:s and "Analista funcional de operaciones" in s):
        assert "truncate" not in el.parent.get("class",[])


def test_filtro_visible_y_conserva_diez_estados_protegidos():
    soup=BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"),"html.parser")
    control=soup.find(id="radarFiltroNoApto")
    assert control is not None and "No apto" in control.get_text(" ",strip=True)
    assert not control.has_attr("hidden")
    assert len(soup.find(id="radarFiltros").find_all("button")) == 10
