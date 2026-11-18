"""Unitarios de `core/profile_parser.py` con textos ficticios: secciones no laborales, roles desde
archivo, habilidades de la base, nombre desde la experiencia y empresas."""
from core import profile_parser as pp

BASE = """# Alex Rivera

## Acme
### Analista Técnico Funcional | 2019 - 2026
• Relevé requerimientos.

## EDUCACIÓN
### Tecnicatura | 2010 - 2012
• No es un rol.

Experiencia
## Korva
### Operador | 2015 - 2018
• Monitoreé alertas.

## HABILIDADES TÉCNICAS
• **Datos:** SQL.
- Python

## HABILIDADES INTERPERSONALES
* Comunicación clara.

## Proyectos personales
• Bot de reportes.
"""


def test_roles_desde_archivo_y_desde_texto(tmp_path):
    """Los roles se leen del archivo de la carpeta dada; sin archivo no hay roles. Las secciones
    de educación no generan roles."""
    (tmp_path / "experiencia.txt").write_text(BASE, encoding="utf-8")
    roles = pp.get_candidate_roles(candidato_dir=tmp_path)
    assert [(r["empresa"], r["rol"]) for r in roles] == [("Acme", "Analista Técnico Funcional"), ("Korva", "Operador")]
    assert pp.get_candidate_roles(candidato_dir=tmp_path / "vacia") == []
    assert pp.get_candidate_roles(exp_text=BASE) == roles


def test_secciones_de_habilidades_de_la_base():
    """Se separan las viñetas técnicas de las interpersonales; otras secciones no cuentan."""
    assert pp.secciones_de_habilidades(BASE) == {
        "tecnicas": ["**Datos:** SQL.", "Python"],
        "interpersonales": ["Comunicación clara."],
    }
    assert pp.secciones_de_habilidades("") == {"tecnicas": [], "interpersonales": []}


def test_nombre_desde_el_titulo_de_la_experiencia():
    """Sin datos personales, el nombre sale del «# Título» de la experiencia (si no es una sección)."""
    assert pp._extract_name("", "# Experiencia\n# Alex Rivera\n## Acme") == "Alex Rivera"
    assert pp._extract_name("", "## Acme") == "Candidato"


def test_items_de_seccion_saltean_lineas_vacias():
    """Las líneas vacías dentro de una sección no generan ítems."""
    texto = "IDIOMAS\n• Español: Nativo\n\n• Inglés: Intermedio\n"
    assert pp._extract_section_items(texto, r"IDIOMAS") == ["Español: Nativo", "Inglés: Intermedio"]


def test_empresas_desde_texto_dado():
    """Con texto de experiencia, las empresas salen de sus encabezados «##» laborales."""
    assert pp.get_candidate_companies(BASE)[:2] == ["Acme", "Korva"]
