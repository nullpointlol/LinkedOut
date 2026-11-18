"""El Auditor no da por cubierto un requisito compuesto con una sola de sus herramientas (oferta 11257: «Grafana,
Dynatrace, Elastic» pasó con «Elastic» del perfil). Candidata inventada."""
from core.auditor import evaluate_ats_compliance

CV = """# LUCÍA FERRARO

## PERFIL
Analista de monitoreo con Elastic.

## HABILIDADES TÉCNICAS
• Postman, Kafka.
"""


def _cubre(kw):
    return kw in evaluate_ats_compliance(CV, [kw], [])["found_keywords"]


def test_un_compuesto_exige_todas_sus_herramientas():
    assert not _cubre("Grafana, Dynatrace, Elastic")
    assert not _cubre("Kafka y Airflow")
    assert _cubre("Postman y Kafka")


def test_las_alternativas_y_las_frases_siguen_igual():
    assert _cubre("Kafka / Airflow")
    assert _cubre("Monitoreo de servicios")  # una frase sin nombres propios: alcanza su palabra núcleo
