"""Regresión del titular decidido: proveedor y perfiles exclusivamente sintéticos."""
import hashlib
import unicodedata
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import server
from core import perfil_areas, profile_parser, prompts_evaluacion as pe
from core.completitud import REGLA_PUESTO, problemas_puesto
from core.cv_guardas import COMPLETO, UNA_PAGINA, corregir_encabezados, encabezado_canonico
from core.docx_generator import generate_cv_filename
from core.writer import WriterAgent
from tests.perfil_ti import roles_ti  # noqa: F401

pytestmark = pytest.mark.usefixtures("roles_ti")

ROLES = [
    ('Producto Test', 'Product Owner Analítico', '2024', '2026'),
    ('Infra Test', 'Ingeniero DevOps Jr.', '2022', '2024'),
    ('Soporte Test', 'Analista de Soporte de Aplicaciones', '2020', '2022'),
    ('Funcional Test', 'Analista Técnico Funcional', '2016', '2020'),
    ('Incidentes Test', 'Gestor de Incidentes y Guardias ITSM', '2013', '2016'),
    ('Monitoreo Test', 'Técnico de Monitoreo NOC', '2010', '2013'),
    ('Desarrollo Test', 'Programador Java', '2004', '2007'),
]
EXP = '\n\n'.join(f'## {e}\n### {r} | {d} - {h}\n• Trabajé con SQL y Java.' for e, r, d, h in ROLES)
PER = 'Persona Sintética\nPuesto: Analista Técnico Funcional\npersona@example.test\n\nEDUCACIÓN\n• Tecnicatura\n\nIDIOMAS\n• Español: Nativo'
NORMALIZADOS = ('PRODUCT OWNER ANALÍTICO', 'INGENIERO DEVOPS', 'ANALISTA DE SOPORTE DE APLICACIONES',
                'ANALISTA TÉCNICO FUNCIONAL', 'GESTOR DE INCIDENTES', 'TÉCNICO DE MONITOREO NOC',
                'PROGRAMADOR JAVA')
# desarrollo no es el perfil actual; el caso 3 no ofrece roles de desarrollo.
CERCANOS = NORMALIZADOS[:-1]


def cv(titulo='ANALISTA TÉCNICO FUNCIONAL', ingles=False):
    return (f'# PERSONA SINTÉTICA\n{titulo}\npersona@example.test\n\n'
            + ('## SUMMARY\nProfessional experience.\n\n## EXPERIENCE\n' if ingles else
               '## PERFIL\nExperiencia profesional.\n\n## EXPERIENCIA\n')
            + '\n'.join(encabezado_canonico(r, e, d, h) + '\n• Trabajé con SQL.' for e, r, d, h in ROLES)
            + ('\n\n## TECHNICAL SKILLS\n' if ingles else '\n\n## HABILIDADES TÉCNICAS\n')
            + '• **Infra:** Docker, Cloud AWS.\n\n## EDUCACIÓN\n• Tecnicatura\n\n## IDIOMAS\n• Español: Nativo')


def invariantes(texto, formato):
    # invariante: seis contratos medidos en el plan, literal b76b1f4..., siete roles históricos.
    titular = texto.splitlines()[1]
    assert not problemas_puesto(titular)
    assert titular in NORMALIZADOS or titular == 'ANALISTA FUNCIONAL'
    assert hashlib.sha256(pe.TRANSFERENCIA_DE_CONOCIMIENTOS.encode()).hexdigest() == 'b76b1f4279b7abc22746c27066c155f7f7d5430ec49a0ec7713f0676738953a5'
    assert all(pe.TRANSFERENCIA_DE_CONOCIMIENTOS in p for p in (pe.WRITER_PROMPT, pe.WRITER_PROMPT_COMPLETO))
    habilidades = texto.split('SKILLS' if '## TECHNICAL SKILLS' in texto else 'HABILIDADES TÉCNICAS')[1]
    assert 'Docker' in habilidades and 'Cloud AWS' in habilidades
    assert REGLA_PUESTO.startswith('La línea bajo el nombre')
    assert server._puesto_export({'titulo': 'Python Developer', 'cv_markdown': texto}) == titular
    nombre = generate_cv_filename(target_role=titular, candidate_name='Persona Sintética').lower()
    sin_tildes = lambda t: unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().lower()
    assert all(sin_tildes(p) in sin_tildes(nombre) for p in titular.split())
    corregido, cambios, problemas = corregir_encabezados(texto, profile_parser.parse_roles(EXP), formato)
    assert corregido == texto and cambios == [] and problemas == []
    assert '### Ingeniero DevOps Jr. | Infra Test (2022 - 2024)' in texto
    assert len([l for l in texto.splitlines() if l.startswith('### ')]) == 7


@pytest.fixture
def perfil(tmp_path: Path, monkeypatch):
    (tmp_path / 'experiencia.txt').write_text(EXP, encoding='utf-8')
    (tmp_path / 'personales.txt').write_text(PER, encoding='utf-8')
    monkeypatch.setattr(profile_parser, 'DEFAULT_CANDIDATO_DIR', tmp_path)
    monkeypatch.setattr(perfil_areas, 'anios_por_area_en_cache', lambda *a, **k: [
        {'area': 'Análisis funcional', 'anios': 7}, {'area': 'Desarrollo', 'anios': 2},
        {'area': 'DevOps', 'anios': 1}])
    return {'experiencia': EXP, 'personales': PER}


def respuesta(texto):
    return {'cv_markdown': texto, 'pitch': 'pitch intacto',
            'pitches_multicanal': {'inmail': 'i', 'email': 'e', 'web_ats': 'w'}}


@pytest.mark.asyncio
@pytest.mark.parametrize('formato', [COMPLETO, UNA_PAGINA])
@pytest.mark.parametrize('oferta,titulo', [('Analista Funcional', 'ANALISTA FUNCIONAL'),
                                          ('Product Manager LATAM', 'PRODUCT OWNER ANALÍTICO'),
                                          ('DevOps Engineer', 'INGENIERO DEVOPS')])
async def test_header_exacto_casos_1_y_2(formato, oferta, titulo, perfil):
    invariantes(cv(), formato)
    if os.getenv('INVARIANT_CHECK') == '1':
        return
    bridge = AsyncMock()
    bridge.run_prompt.return_value = respuesta(cv())
    salida = await WriterAgent(bridge).generate_cv_and_pitch({'puesto': oferta, 'formato_cv': formato}, perfil)
    assert bridge.run_prompt.await_count == 1
    prompt = bridge.run_prompt.call_args.args[0]
    assert f'Usá exactamente: «{titulo}»' in prompt
    assert 'El título de la oferta normalizado si coincide' not in prompt
    assert salida['cv_markdown'].splitlines()[1] == titulo
    invariantes(salida['cv_markdown'], formato)


@pytest.mark.asyncio
@pytest.mark.parametrize('formato', [COMPLETO, UNA_PAGINA])
async def test_header_lista_cerrada_caso_3(formato, perfil):
    invariantes(cv(), formato)
    if os.getenv('INVARIANT_CHECK') == '1':
        return
    bridge = AsyncMock()
    bridge.run_prompt.side_effect = [{'puesto': 'PROGRAMADOR JAVA'}, respuesta(cv())]
    salida = await WriterAgent(bridge).generate_cv_and_pitch({'puesto': 'AI Engineer', 'formato_cv': formato}, perfil)
    seleccion, redaccion = bridge.run_prompt.call_args_list
    assert seleccion.kwargs == dict(json_mode=True, timeout_seconds=240, effort='medium', cwd=seleccion.kwargs['cwd'])
    assert seleccion.kwargs['cwd']
    assert all(r in redaccion.args[0] for r in CERCANOS)
    assert 'No uses el título del aviso' in redaccion.args[0]
    assert redaccion.kwargs == dict(json_mode=True, timeout_seconds=600)
    invariantes(salida['cv_markdown'], formato)


@pytest.mark.asyncio
@pytest.mark.parametrize('formato', [COMPLETO, UNA_PAGINA])
async def test_transferencia_e_historial_intactos(formato, perfil):
    invariantes(cv(), formato)
    if os.getenv('INVARIANT_CHECK') == '1':
        return
    bridge = AsyncMock()
    bridge.run_prompt.return_value = respuesta(cv())
    salida = await WriterAgent(bridge).generate_cv_and_pitch({'puesto': 'Programador Java', 'formato_cv': formato}, perfil)
    invariantes(salida['cv_markdown'], formato)
    assert salida['pitch'] == 'pitch intacto'


@pytest.mark.asyncio
@pytest.mark.parametrize('formato', [COMPLETO, UNA_PAGINA])
@pytest.mark.parametrize('falla', [False, True, 'respuesta_invalida'])
async def test_salida_desobediente_y_sin_ia(formato, falla, perfil):
    invariantes(cv(), formato)
    if os.getenv('INVARIANT_CHECK') == '1':
        return
    bridge = AsyncMock()
    redaccion = ({} if falla == 'respuesta_invalida' else
                 RuntimeError('no disponible') if falla else respuesta(cv('PYTHON DEVELOPER')))
    bridge.run_prompt.side_effect = [redaccion]  # desarrollo: elección fija, sin IA de selección
    analisis = {
        'puesto': 'Python Developer', 'formato_cv': formato,
        'requisitos': [{'nombre': n, 'estado': 'TRANSFERIBLE', 'approved': True}
                       for n in ('Docker', 'Cloud AWS')],
    }
    if falla:
        # Sin IA no hay CV: la redacción falla, nunca sale uno determinista
        from core.writer import RedaccionSinIA
        with pytest.raises(RedaccionSinIA):
            await WriterAgent(bridge).generate_cv_and_pitch(analisis, perfil)
        assert bridge.run_prompt.await_count == 1
        return
    salida = await WriterAgent(bridge).generate_cv_and_pitch(analisis, perfil)
    texto = salida['cv_markdown']
    assert texto.splitlines()[1] == 'ANALISTA TÉCNICO FUNCIONAL'
    assert texto.count('Me postulo a la posición de Python Developer.') == 1
    assert 'Especialista en Python Developer' not in texto
    assert bridge.run_prompt.await_count == 1
    assert salida['is_fallback'] is False
    invariantes(texto, formato)


@pytest.mark.asyncio
@pytest.mark.parametrize('formato', [COMPLETO, UNA_PAGINA])
async def test_titulo_exacto_en_ingles(formato, perfil):
    invariantes(cv(), formato)
    if os.getenv('INVARIANT_CHECK') == '1':
        return
    bridge = AsyncMock()
    bridge.run_prompt.return_value = respuesta(cv('JAVA DEVELOPER', ingles=True))
    salida = await WriterAgent(bridge).generate_cv_and_pitch({'puesto': 'Product Manager LATAM', 'idioma_cv': 'en', 'formato_cv': formato}, perfil)
    prompt = bridge.run_prompt.call_args.args[0]
    assert 'Excepción solo para el titular decidido: no lo traduzcas.' in prompt
    assert 'Usá exactamente: «PRODUCT OWNER ANALÍTICO»' in prompt
    assert 'El pitch y los pitches multicanal también van en inglés' in prompt
    assert salida['cv_markdown'].splitlines()[1] == 'PRODUCT OWNER ANALÍTICO'
    summary = salida['cv_markdown'].split('## SUMMARY')[1].split('## EXPERIENCE')[0]
    assert summary.count('Me postulo a la posición de Product Manager LATAM.') == 1
    assert salida['pitch'] == 'pitch intacto'
    invariantes(salida['cv_markdown'], formato)


@pytest.mark.asyncio
async def test_cache_ajeno_no_se_aplica(perfil):
    invariantes(cv(), COMPLETO)
    if os.getenv('INVARIANT_CHECK') == '1':
        return
    bridge = AsyncMock()
    bridge.run_prompt.return_value = respuesta(cv())
    distinto = dict(perfil, experiencia=EXP + '\n• Otra responsabilidad sintética.')
    await WriterAgent(bridge).generate_cv_and_pitch({'puesto': 'Analista Funcional'}, distinto)
    assert 'Usá exactamente: «ANALISTA TÉCNICO FUNCIONAL»' in bridge.run_prompt.call_args.args[0]
