"""tools/auditar_datos.py: cero datos personales en el árbol, con un candidato inventado.

Absorbe la intención de test_sin_datos_candidato_en_codigo.py (nada del candidato escrito en el código): ahora los
términos salen del perfil (`<DATA_DIR>/candidato`), nunca de una lista escrita en el repo.
"""
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import auditar_datos as ad  # noqa: E402

# Armados por partes: así el propio test no tiene mails, teléfonos ni rutas con forma real en el código.
DOM = "correo-real." + "com"
TEL = "6123" + "-4789"

PERSONALES = f"""Brenda Quirogal Satt
Puesto: Analista de Datos

Villa Zapiola, Buenos Aires, Argentina | +54 9 11 {TEL} | https://wa.me/54911{TEL} | brenda.qsatt@{DOM} | linkedin.com/in/brenda-qsatt-77a1/

EDUCACIÓN
• Licenciatura en Sistemas - Colegio Mayor Valdelomar (2009 - 2013)

Educación
Curso de SQL - Academia Ternal - Buenos Aires, Argentina, 2015 - 2016

IDIOMAS
• Español: Nativo

Referencias
• Rufino Echagüe Bastos - Gerente en Brontes: +54911 {TEL.replace("6", "7")}
"""

EXPERIENCIA = """# Experiencia profesional

## Fundición Brontes
### Analista de Datos | 2019 - 2024
• Migré 340 tableros a la nueva plataforma.
### Operador | 2014 - 2019
• Atendí incidentes.

## ZQX
### Pasante | 2012 - 2014

## Freelance
### Consultor | 2024 - 2025

## HABILIDADES TÉCNICAS
• SQL.
"""

EXPERIENCIA_EN = """# Professional Experience

## Fundición Brontes
### Data Analyst | 2019 - 2024

## Technical Skills
• SQL.

## Education
• Something.
"""


def _candidato(tmp_path: Path) -> Path:
    datos = tmp_path / "datos"
    (datos / "candidato").mkdir(parents=True)
    (datos / "candidato" / "personales.txt").write_text(PERSONALES, encoding="utf-8")
    (datos / "candidato" / "experiencia.txt").write_text(EXPERIENCIA, encoding="utf-8")
    (datos / "candidato" / "experiencia_en.txt").write_text(EXPERIENCIA_EN, encoding="utf-8")
    (datos / "candidato" / "restricciones.json").write_text(RESTRICCIONES, encoding="utf-8")
    return datos


RESTRICCIONES = """{"base": "Villa Zapiola, AMBA", "localidades_permitidas": ["caba", "gba", "loma verde"],
 "residencia": ["Barrio Prueba Norte", "Villa Zapiola"], "residencia_cerca_de": ["Cerro Inventado"]}"""


def _repo(tmp_path: Path, archivos: dict) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for nombre, contenido in archivos.items():
        ruta = repo / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contenido, bytes):
            ruta.write_bytes(contenido)
        else:
            ruta.write_text(contenido, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "inicial")
    return repo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=prueba", "-c", "user.email=prueba@example.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def _docx(texto_xml: str, app_xml: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body>{texto_xml}</w:body></w:document>")
        if app_xml:
            z.writestr("docProps/app.xml", app_xml)
    return buf.getvalue()


def _correr(capsys, *argv: str):
    codigo = ad.main(list(argv))
    return codigo, capsys.readouterr().out


TERMINOS_REALES = ["Brenda", "Quirogal", "brenda.qsatt", "6123-4789", "brenda-qsatt-77a1", "Villa Zapiola",
                   "Brontes", "Valdelomar"]


def test_terminos_del_perfil(tmp_path):
    terminos = {(t.categoria, t.valor) for t in ad.terminos_candidato(_candidato(tmp_path))}
    assert ("nombre", "Brenda Quirogal Satt") in terminos
    assert ("apellido", "Quirogal Satt") in terminos
    assert ("mail", "brenda.qsatt@" + DOM) in terminos
    assert ("linkedin", "brenda-qsatt-77a1") in terminos
    assert ("localidad", "Villa Zapiola") in terminos
    assert ("empleador", "Fundición Brontes") in terminos
    assert ("escuela", "Colegio Mayor Valdelomar") in terminos
    assert ("cronologia", "2019 - 2024") in terminos
    assert ("logro", "340 tableros") in terminos
    assert ("escuela", "Academia Ternal") in terminos
    assert ("empleador", "ZQX") in terminos
    assert ("referencia", "Rufino Echagüe Bastos") in terminos
    assert ("telefono", "61234789") in terminos and ("telefono", "71234789") in terminos
    valores = {v for _, v in terminos}
    assert not valores & {"HABILIDADES TÉCNICAS", "Technical Skills", "Education", "Freelance"}
    assert not any(c == "telefono" and v.startswith("20") for c, v in terminos)  # años, no teléfonos


def test_local_encuentra_cada_dato_y_lo_enmascara(tmp_path, capsys):
    datos = _candidato(tmp_path)
    repo = _repo(tmp_path, {
        "a.py": "autor = 'brenda quirogal satt'\n",
        "b.md": f"Llamá al 11 {TEL.replace('-', ' ')} o escribí a Brenda.QSatt@{DOM}\n",
        "c.json": '{"empresa": "Fundicion Brontes", "perfil": "linkedin.com/in/brenda-qsatt-77a1"}\n',
        "d.txt": "Vive en Villa Zapiola.\nEstudió en el Colegio Mayor Valdelomar.\n",
        "e.txt": "Analista | 2019-2024\nOperador | 2014 a 2019\nMigró 340 tableros\n",
        "limpio.txt": "Sam Ortega, Ciudad Ejemplo\n",
    })
    codigo, salida = _correr(capsys, "--local", "--data-dir", str(datos), "--raiz", str(repo))
    assert codigo == 1
    for categoria in ["nombre", "telefono", "mail", "empleador", "linkedin", "localidad", "escuela", "cronologia",
                      "logro"]:
        assert categoria in salida, categoria
    assert "limpio.txt" not in salida
    for termino in TERMINOS_REALES + ["340 tableros", "2019"]:
        assert termino.lower() not in salida.lower(), termino


def test_local_mira_dentro_de_docx_aun_partido_en_runs(tmp_path, capsys):
    datos = _candidato(tmp_path)
    repo = _repo(tmp_path, {
        "plantilla.docx": _docx("<w:p><w:r><w:t>Bren</w:t></w:r><w:r><w:t>da Quirogal Satt</w:t></w:r></w:p>",
                                "<Properties><TitlesOfParts>CV Fundición Brontes</TitlesOfParts></Properties>"),
    })
    codigo, salida = _correr(capsys, "--local", "--data-dir", str(datos), "--raiz", str(repo))
    assert codigo == 1
    assert "plantilla.docx!word/document.xml" in salida
    assert "plantilla.docx!docProps/app.xml" in salida


def test_un_rango_de_anios_suelto_no_es_cronologia(tmp_path, capsys):
    datos = _candidato(tmp_path)
    repo = _repo(tmp_path, {"a.md": "### Consultor | 2019 - 2024\n", "b.md": "2014-2019 y 2019 - 2024\n"})
    codigo, salida = _correr(capsys, "--local", "--data-dir", str(datos), "--raiz", str(repo))
    assert "a.md" not in salida and "b.md:1  cronologia" in salida


@pytest.mark.parametrize("lista", [
    '["tigre", "pilar", "escobar", "moron", "villa zapiola", "quilmes", "lanus"]\n',
    '[\n "amba",\n "caba",\n "gba",\n "buenos aires",\n "capital federal",\n "villa zapiola",\n "tigre",\n'
    ' "pilar"\n]\n',
])
def test_localidad_dentro_de_lista_generica_no_cuenta(tmp_path, capsys, lista):
    datos = _candidato(tmp_path)
    repo = _repo(tmp_path, {"lista.json": lista})
    codigo, salida = _correr(capsys, "--local", "--data-dir", str(datos), "--raiz", str(repo))
    assert (codigo, "0 hallazgos" in salida) == (0, True)


def test_terminos_de_calco(tmp_path):
    terminos = {(t.categoria, t.valor) for t in ad.terminos_candidato(_candidato(tmp_path))}
    assert {("calco cargo", "Analista de Datos"), ("calco cargo", "Data Analyst"),
            ("calco localidad", "loma verde"), ("calco localidad", "Barrio Prueba Norte"),
            ("calco localidad", "Cerro Inventado")} <= terminos
    calcos = {v.lower() for c, v in terminos if c.startswith("calco")}
    assert not calcos & {"caba", "gba", "pasante", "operador"}  # genéricos y cargos de una palabra no cuentan


def test_calco_en_ejemplo_fixtures_y_docs_pero_no_en_el_codigo(tmp_path, capsys):
    datos = _candidato(tmp_path)
    calco = '{"puesto": "Analista de Datos", "zonas": ["tigre", "pilar", "loma verde", "escobar", "moron"]}\n'
    repo = _repo(tmp_path, {
        "config/ejemplo/candidato/a.json": calco,
        "tests/fixtures/candidato/b.json": calco,
        "docs/c.md": "Barrio Prueba Norte y Cerro Inventado\n",
        "core/d.py": calco,
    })
    codigo, salida = _correr(capsys, "--local", "--data-dir", str(datos), "--raiz", str(repo))
    assert codigo == 1
    for ruta in ["config/ejemplo/candidato/a.json:1", "tests/fixtures/candidato/b.json:1"]:
        assert f"{ruta}  calco cargo" in salida and f"{ruta}  calco localidad" in salida, ruta
    assert "docs/c.md:1  calco localidad" in salida
    assert "core/d.py" not in salida
    assert "loma" not in salida.lower() and "analista de datos" not in salida.lower()


def test_ci_solo_patrones_genericos(tmp_path, capsys):
    repo = _repo(tmp_path, {
        "ok.py": ("MAIL = 'sam.ortega@example.com'\nOTRO = 'rrhh@empresa.test'\nAR = 'rrhh@ejemplo.com.ar'\n"
                  "TEL = '+54 9 11 0000-0000'\nautor = 'Brenda Quirogal'\nRUTA = r'C:\\Users\\<usuario>\\LinkedOut'\n"),
        "mal.py": ("MAIL = 'alguien@" + "gmail.com'\nTEL = '+54 9 11 " + TEL + "'\n"
                   "RUTA = r'C:\\Users\\" + "pepe\\datos'\nCLAVE = 'sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz012345'\n"),
    })
    codigo, salida = _correr(capsys, "--ci", "--raiz", str(repo))
    assert codigo == 1
    assert "ok.py" not in salida
    assert [l.split()[0] for l in salida.splitlines() if l.startswith("mal.py")] == [
        "mal.py:1", "mal.py:2", "mal.py:3", "mal.py:4"]
    assert "gmail" not in salida and "abcdefghij" not in salida and "pepe" not in salida


def test_ci_no_lee_el_perfil(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKEDOUT_DATA_DIR", str(_candidato(tmp_path)))
    assert ad.main(["--ci", "--raiz", str(_repo(tmp_path, {"a.txt": "Brenda Quirogal Satt\n"}))]) == 0


def test_mensajes_de_commit_del_rango(tmp_path, capsys):
    datos = _candidato(tmp_path)
    repo = _repo(tmp_path, {"a.txt": "nada\n"})
    (repo / "a.txt").write_text("nada más\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "arreglo pedido por brenda.qsatt@" + DOM)
    codigo, salida = _correr(capsys, "--local", "--data-dir", str(datos), "--raiz", str(repo),
                             "--commits", "HEAD~1..HEAD")
    assert codigo == 1
    assert "commit " in salida and "mail" in salida and "qsatt" not in salida


def test_sin_perfil_el_modo_local_falla_claro(tmp_path, capsys):
    codigo, salida = _correr(capsys, "--local", "--data-dir", str(tmp_path / "no-existe"),
                             "--raiz", str(_repo(tmp_path, {"a.txt": "x\n"})))
    assert codigo == 2


def test_enmascarar_conserva_solo_iniciales():
    assert ad.enmascarar("Fundición Brontes") == "F******** B******"
    assert ad.enmascarar("brenda.qsatt@" + DOM) == "b***********@c**********.com"


def test_arbol_del_repo_sin_patrones_genericos():
    hallazgos = list(ad.auditar(RAIZ, []))
    assert hallazgos == []


# ---------------------------------------------------------------- autores de los commits
# Un commit hecho con la identidad global de git publica el nombre y el mail personal aunque el mensaje esté limpio.
# Con --commits se auditan también autor y committer; el noreply de GitHub está permitido.

def _commit_como(repo: Path, nombre: str, mail: str, mensaje: str = "cambio") -> None:
    (repo / "a.txt").write_text(f"{mensaje}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "-c", f"user.name={nombre}", "-c", f"user.email={mail}",
                    "-c", "commit.gpgsign=false", "commit", "-q", "-am", mensaje], check=True, capture_output=True)


def test_autor_con_mail_personal_es_hallazgo_en_ci(tmp_path, capsys):
    repo = _repo(tmp_path, {"a.txt": "nada\n"})
    _commit_como(repo, "Alguien", "brenda.qsatt@" + DOM)
    codigo, salida = _correr(capsys, "--ci", "--raiz", str(repo), "--commits", "HEAD~1..HEAD")
    assert codigo == 1
    assert "autor" in salida and "mail" in salida and "qsatt" not in salida


def test_autor_con_el_nombre_del_perfil_es_hallazgo_en_local(tmp_path, capsys):
    datos = _candidato(tmp_path)
    repo = _repo(tmp_path, {"a.txt": "nada\n"})
    _commit_como(repo, "Brenda Quirogal", "prueba@example.com")
    codigo, salida = _correr(capsys, "--local", "--data-dir", str(datos), "--raiz", str(repo),
                             "--commits", "HEAD~1..HEAD")
    assert codigo == 1
    assert "autor" in salida and "Quirogal" not in salida


def test_noreply_de_github_esta_permitido(tmp_path, capsys):
    datos = _candidato(tmp_path)
    repo = _repo(tmp_path, {"a.txt": "nada\n"})
    _commit_como(repo, "cuenta-de-prueba", "12345+cuenta-de-prueba@users.noreply.github.com")
    assert _correr(capsys, "--ci", "--raiz", str(repo), "--commits", "HEAD~1..HEAD")[0] == 0
    assert _correr(capsys, "--local", "--data-dir", str(datos), "--raiz", str(repo),
                   "--commits", "HEAD~1..HEAD")[0] == 0


def test_un_porcentaje_suelto_no_es_un_logro_identificable(tmp_path):
    """«80%» aparece en cualquier código (CSS, umbrales): solo no identifica a nadie (9 falsos positivos)."""
    candidato = _candidato(tmp_path)
    exp = candidato / "candidato" / "experiencia.txt"
    exp.write_text(exp.read_text(encoding="utf-8") + "\n• Reduje un 80% los tiempos de respuesta\n", encoding="utf-8")
    terminos = {(t.categoria, t.valor) for t in ad.terminos_candidato(candidato)}
    assert not any(c == "logro" and v.endswith("%") for c, v in terminos)
    assert ("logro", "340 tableros") in terminos
