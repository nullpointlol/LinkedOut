"""El perfil de ejemplo versionado (config/ejemplo/candidato) tiene la forma que el portal espera.
El perfil real de cada usuario vive en <DATA_DIR>/candidato y nunca se versiona."""
import json
import re
from pathlib import Path

EJEMPLO = Path(__file__).resolve().parent.parent / "config" / "ejemplo" / "candidato"


def test_candidate_personales_data():
    content = (EJEMPLO / "personales.txt").read_text(encoding="utf-8")
    lineas = content.splitlines()
    assert lineas[0].strip(), "la primera línea es el nombre"
    assert lineas[1].startswith("Puesto: ")
    contacto = next(l for l in lineas if "|" in l)
    assert re.search(r"\+\d[\d ]+-\d{4}", contacto)
    assert "@example.com" in contacto
    assert "https://wa.me/" in contacto and "https://www.linkedin.com/in/" in contacto
    assert "EDUCACIÓN" in content and "IDIOMAS" in content


def test_candidate_experiencia_data():
    content = (EJEMPLO / "experiencia.txt").read_text(encoding="utf-8")
    empresas = re.findall(r"^## (.+)$", content, re.M)
    roles = re.findall(r"^### (.+) \| (\d{4}) - (\d{4}|Presente)$", content, re.M)
    assert len(empresas) >= 3 and "HABILIDADES TÉCNICAS" in empresas
    assert len(roles) >= 2, "un `###` por rol, con sus fechas"
    assert all(int(desde) <= 2100 for _, desde, _ in roles)


def test_candidate_config_json_valido():
    for nombre in ("restricciones.json", "busquedas.json", "conocimientos_ausentes.json"):
        assert isinstance(json.loads((EJEMPLO / nombre).read_text(encoding="utf-8")), dict), nombre


def test_el_perfil_real_no_se_versiona():
    raiz = Path(__file__).resolve().parent.parent
    gitignore = (raiz / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in gitignore
