"""Integración del titular y ATS: DB, perfil y proveedor aislados."""
import hashlib
import json
import os
import unicodedata
from unittest.mock import AsyncMock, Mock

import pytest

import server
from core import database, perfil_areas, profile_parser, prompts_evaluacion, radar_store, titulo_cv, writer
from core.completitud import REGLA_PUESTO, problemas_puesto
from core.cv_guardas import corregir_encabezados, encabezado_canonico, quitar_requisitos_de_habilidades
from core.docx_generator import generate_cv_filename

ROLES = (
    'Product Owner Analítico', 'Ingeniero DevOps Jr.', 'Analista de Soporte de Aplicaciones',
    'Analista Técnico Funcional', 'Gestor de Incidentes y Guardias ITSM',
    'Técnico de Monitoreo NOC', 'Programador Java',
)
REALES = ('PRODUCT OWNER ANALÍTICO', 'INGENIERO DEVOPS', 'ANALISTA DE SOPORTE DE APLICACIONES',
          'ANALISTA TÉCNICO FUNCIONAL', 'GESTOR DE INCIDENTES',
          'TÉCNICO DE MONITOREO NOC', 'PROGRAMADOR JAVA')
EXP = '\n\n'.join(f'## Empresa {i}\n### {r} | {2025-i} - {2026-i}\n• Python Java SQL.'
                    for i, r in enumerate(ROLES))
PER = 'Alex Prueba\nPuesto: Analista Técnico Funcional\nalex@example.test\n'
HISTORIA = '\n'.join(encabezado_canonico(r, f'Empresa {i}', str(2025-i), str(2026-i))
                     + '\n• Python Java SQL.' for i, r in enumerate(ROLES))


def cv(titulo='ANALISTA TÉCNICO FUNCIONAL'):
    return (f'# Alex Prueba\n{titulo}\nalex@example.test\n\n## PERFIL\nExperiencia comprobable.\n\n'
            f'## EXPERIENCIA\n{HISTORIA}\n\n## HABILIDADES\nDocker, Cloud AWS o GCP, Jenkins, Linux.\n')


def invariantes(texto=None):
    texto = texto or cv()
    # invariante 1: ocho rechazos del plan y titular válido (completitud.problemas_puesto).
    for mal in ('ANALISTA FUNCIONAL / SOPORTE TECNICO', 'ANALISTA (SQL)', 'CONVOCATORIA ABIERTA',
                'BUSQUEDA ANALISTA', 'DESARROLLADOR TRAINEE', 'HEAD DE SISTEMAS', 'PRODUCT MANAGER', 'ENGINEER III'):
        assert problemas_puesto(mal)
    titular = texto.splitlines()[1]
    assert not problemas_puesto(titular)
    # invariante 2: siete roles reales de 3/2/4/3/3/5/2 palabras; sin profesiones inventadas.
    assert [len(r.split()) for r in REALES] == [3, 2, 5, 3, 3, 4, 2]
    assert titular in REALES
    assert tuple(r['rol'] for r in profile_parser.parse_roles(EXP)) == ROLES
    # invariante 3: transferencia literal y habilidades de la base intactas.
    assert hashlib.sha256(prompts_evaluacion.TRANSFERENCIA_DE_CONOCIMIENTOS.encode()).hexdigest() == 'b76b1f4279b7abc22746c27066c155f7f7d5430ec49a0ec7713f0676738953a5'
    assert quitar_requisitos_de_habilidades(texto)[0] == texto
    assert all(h in texto.split('## HABILIDADES')[1] for h in ('Docker', 'Cloud AWS o GCP', 'Jenkins', 'Linux'))
    # invariante 4: revision_store recorta esta regla pública.
    assert REGLA_PUESTO.startswith('La línea bajo el nombre')
    # invariante 5: exportación usa el titular del CV aunque el aviso diga Python.
    assert server._puesto_export({'titulo': 'Python Developer', 'cv_markdown': texto}) == titular
    sin_tildes = ''.join(c for c in unicodedata.normalize('NFD', titular) if not unicodedata.combining(c))
    assert sin_tildes.replace(' ', '-') in generate_cv_filename(titular, 'Alex Prueba')
    # invariante 6: encabezados canónicos, empresas, fechas y Jr. históricos intactos.
    assert corregir_encabezados(texto, profile_parser.parse_roles(EXP)) == (texto, [], [])
    assert [l for l in texto.splitlines() if l.startswith('### ')] == [l for l in HISTORIA.splitlines() if l.startswith('### ')]
    assert 'Ingeniero DevOps Jr.' in texto
    return os.getenv('INVARIANT_CHECK') == '1'


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    (tmp_path / 'experiencia.txt').write_text(EXP, encoding='utf-8')
    (tmp_path / 'personales.txt').write_text(PER, encoding='utf-8')
    huella = hashlib.sha256(b'\0'.join([perfil_areas.VERSION_CRITERIO, EXP.encode(), PER.encode()])).hexdigest()
    (tmp_path / 'anios_por_area.json').write_text(json.dumps({'hash': huella, 'anios_por_area': [
        {'area': 'Desarrollo', 'anios': '2'}, {'area': 'Funcional', 'anios': '7'}]}), encoding='utf-8')
    monkeypatch.setattr(profile_parser, 'DEFAULT_CANDIDATO_DIR', tmp_path)
    monkeypatch.setattr(writer, 'EXP_PATH', tmp_path / 'experiencia.txt')
    monkeypatch.setattr(writer, 'PER_PATH', tmp_path / 'personales.txt')
    monkeypatch.setattr(server, 'load_candidate_profile', lambda: {})
    bridge = Mock()
    bridge.run_prompt = AsyncMock(return_value={'puesto': 'AI ENGINEER'})
    monkeypatch.setattr(titulo_cv, 'AntigravityBridge', lambda **kw: bridge)
    corrector = AsyncMock(side_effect=lambda texto, **kw: (texto, None))
    monkeypatch.setattr(server, 'corregir_puesto', corrector)
    return bridge, corrector


def postulacion(texto, oferta='Python Developer', **analisis):
    pid = database.create_postulacion(titulo=oferta, empresa='Destino', contenido='Oferta sintética', url='https://example.test/1')
    database.update_postulacion(pid, analisis={'puesto': oferta, **analisis}, cv_markdown=texto)
    return database.get_postulacion(pid)


@pytest.mark.asyncio
async def test_corrige_titulo_y_registra(entorno, monkeypatch):
    if invariantes():
        return
    p = postulacion(cv('PYTHON DEVELOPER'))
    registro = Mock()
    monkeypatch.setattr(server, '_registrar_revision', registro)
    resultado, _, _, _ = await server._asegurar_completo(p['id'], p['cv_markdown'], '', {})
    assert resultado.splitlines()[1] == 'ANALISTA TÉCNICO FUNCIONAL'
    assert resultado.count('Me postulo a la posición de Python Developer.') == 1
    cambios = [c for llamada in registro.call_args_list for c in llamada.args[0]]
    assert cambios == [{'campo': 'puesto objetivo', 'valor': 'ANALISTA TÉCNICO FUNCIONAL', 'tipo': 'contradiccion'}]
    assert registro.call_args_list[0].args[1:] == (p['id'], None, 'CV')
    entorno[0].run_prompt.assert_not_awaited()
    entorno[1].assert_not_awaited()
    invariantes(resultado)


@pytest.mark.asyncio
async def test_revalida_despues_de_completar(entorno, monkeypatch):
    if invariantes():
        return
    p = postulacion(cv('PYTHON DEVELOPER').replace('Experiencia comprobable.', 'Hola [Nombre].'))
    async def adverso(texto, **kw):
        assert texto.splitlines()[1] == 'ANALISTA TÉCNICO FUNCIONAL'
        return cv('PYTHON DEVELOPER'), []
    completar = AsyncMock(side_effect=adverso)
    monkeypatch.setattr(server, 'completar', completar)
    registro = Mock()
    monkeypatch.setattr(server, '_registrar_revision', registro)
    resultado, _, _, _ = await server._asegurar_completo(p['id'], p['cv_markdown'], '', {})
    assert resultado.count('Me postulo a la posición de Python Developer.') == 1
    assert resultado.splitlines()[1] == 'ANALISTA TÉCNICO FUNCIONAL'
    assert sum(len(c.args[0]) for c in registro.call_args_list) == 2
    entorno[0].run_prompt.assert_not_awaited()
    invariantes(resultado)


@pytest.mark.asyncio
async def test_heredado_presentable_y_segunda_pasada(entorno, monkeypatch):
    if invariantes():
        return
    p = postulacion(cv('PYTHON DEVELOPER'))
    completar = AsyncMock(side_effect=lambda texto, **kw: (texto, []))
    monkeypatch.setattr(server, 'completar', completar)
    registro = Mock()
    monkeypatch.setattr(server, '_registrar_revision', registro)
    nuevo = await server._cv_al_dia(p)
    assert nuevo['cv_markdown'].splitlines()[1] == 'ANALISTA TÉCNICO FUNCIONAL'
    assert nuevo['cv_markdown'].count('Me postulo a la posición de Python Developer.') == 1
    revisiones = registro.call_count
    assert (await server._cv_al_dia(nuevo))['cv_markdown'] == nuevo['cv_markdown']
    assert registro.call_count == revisiones
    # completar sin hallazgos no llama al modelo: el invariante es el bridge sin llamadas.
    entorno[0].run_prompt.assert_not_awaited()
    invariantes(nuevo['cv_markdown'])


@pytest.mark.asyncio
@pytest.mark.parametrize('respuesta', [{'puesto': 'INGENIERO DEVOPS'}, {'puesto': 'AI ENGINEER'}, None])
async def test_caso_c_lista_y_fallback(entorno, monkeypatch, respuesta):
    # Coordinador 10/11 (misión 04): en CV heredados (_cv_al_dia) el caso 3 no consulta a la IA.
    if invariantes():
        return
    entorno[0].run_prompt.return_value = respuesta
    p = postulacion(cv('PYTHON DEVELOPER'), 'Analista de Soporte', grupo_rol='C')
    nuevo = await server._cv_al_dia(p)
    assert nuevo['cv_markdown'].splitlines()[1] == 'ANALISTA TÉCNICO FUNCIONAL'
    assert (await server._cv_al_dia(nuevo))['cv_markdown'] == nuevo['cv_markdown']
    entorno[0].run_prompt.assert_not_awaited()
    invariantes(nuevo['cv_markdown'])


@pytest.mark.asyncio
async def test_sin_linea_de_titulo_no_llama_ia(entorno, monkeypatch):
    if invariantes():
        return
    sin_titulo = cv('alex@example.test').replace('\nalex@example.test\n', '\n', 1)
    assert sin_titulo.splitlines()[1] == 'alex@example.test'
    p = postulacion(sin_titulo, 'AI Engineer')
    resultado, *_ = await server._asegurar_completo(p['id'], sin_titulo, '', {}, informar_paso=False)
    assert resultado.splitlines()[:2] == ['# Alex Prueba', 'alex@example.test']
    assert 'Me postulo a la posición de AI Engineer.' in resultado
    p = postulacion('# CV', 'AI Engineer')
    assert (await server._cv_al_dia(p))['cv_markdown'] == '# CV'
    entorno[0].run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_cv_al_dia_caso_3_sin_ia_usa_fallback(entorno, monkeypatch):
    if invariantes():
        return
    p = postulacion(cv('AI ENGINEER'), 'AI Engineer')
    nuevo = await server._cv_al_dia(p)
    assert nuevo['cv_markdown'].splitlines()[1] == 'ANALISTA TÉCNICO FUNCIONAL'
    assert nuevo['cv_markdown'].count('Me postulo a la posición de AI Engineer.') == 1
    entorno[0].run_prompt.assert_not_awaited()
    invariantes(nuevo['cv_markdown'])


@pytest.mark.asyncio
async def test_sin_decision_usa_corrector_actual(entorno, monkeypatch):
    if invariantes():
        return
    (profile_parser.DEFAULT_CANDIDATO_DIR / 'experiencia.txt').write_text('', encoding='utf-8')
    (profile_parser.DEFAULT_CANDIDATO_DIR / 'personales.txt').write_text('', encoding='utf-8')
    p = postulacion(cv('CONVOCATORIA ABIERTA'))
    entorno[1].return_value = (cv(), {'campo': 'puesto objetivo', 'valor': 'ANALISTA TÉCNICO FUNCIONAL', 'tipo': 'contradiccion'})
    entorno[1].side_effect = None
    resultado, _, _, _ = await server._asegurar_completo(p['id'], p['cv_markdown'], '', {})
    entorno[1].assert_awaited_once()
    invariantes(resultado)


@pytest.mark.asyncio
async def test_export_y_encabezados(entorno):
    if invariantes():
        return
    p = postulacion(cv('PYTHON DEVELOPER'))
    nuevo = await server._cv_al_dia(p)
    invariantes(nuevo['cv_markdown'])
    assert server._puesto_export(nuevo) == 'ANALISTA TÉCNICO FUNCIONAL'


@pytest.mark.asyncio
@pytest.mark.parametrize('perfil,oferta', [('', 'Python Developer'), ('## PERFIL\n\n', 'Python Developer'),
                                        ('## PERFIL\nExperiencia comprobable.\n\n', 'No especificado')])
async def test_ats_no_aplicable_no_repite_reparacion(entorno, monkeypatch, perfil, oferta):
    if invariantes():
        return
    texto = cv().replace('## PERFIL\nExperiencia comprobable.\n\n', perfil)
    p = postulacion(texto, oferta)
    completar = AsyncMock()
    monkeypatch.setattr(server, 'completar', completar)
    reparar = AsyncMock(wraps=server._asegurar_completo)
    monkeypatch.setattr(server, '_asegurar_completo', reparar)
    nuevo = await server._cv_al_dia(p)
    assert (await server._cv_al_dia(nuevo))['cv_markdown'] == texto
    reparar.assert_not_awaited()
    completar.assert_not_awaited()
    entorno[0].run_prompt.assert_not_awaited()
    invariantes(texto)


@pytest.mark.asyncio
@pytest.mark.parametrize('titular', ['PYTHON DEVELOPER', 'ANALISTA TÉCNICO FUNCIONAL'])
async def test_correccion_cv_invalida_aprobacion(entorno, titular):
    if invariantes():
        return
    texto = cv(titular)
    if titular == 'PYTHON DEVELOPER':  # Solo titular; el otro caso cambia únicamente ATS.
        texto = texto.replace('Experiencia comprobable.',
                              'Experiencia comprobable. Me postulo a la posición de Python Developer.')
    p = postulacion(texto)
    post_id = radar_store.insert_post_manual('Oferta sintética')
    radar_store.update_post(post_id, postulacion_id=p['id'], estado='LISTO', aprobado_at='2026-11-10')
    nuevo = await server._cv_al_dia(p)
    assert nuevo['cv_markdown'] == database.get_postulacion(p['id'])['cv_markdown']
    assert nuevo['cv_markdown'].count('Me postulo a la posición de Python Developer.') == 1
    assert radar_store.get_post(post_id)['aprobado_at'] is None
    radar_store.update_post(post_id, aprobado_at='2026-11-11')
    assert (await server._cv_al_dia(nuevo))['cv_markdown'] == nuevo['cv_markdown']
    assert radar_store.get_post(post_id)['aprobado_at'] == '2026-11-11'
    invariantes(nuevo['cv_markdown'])


@pytest.mark.asyncio
async def test_writer_servidor_caso3_una_seleccion(entorno):
    if invariantes():
        return
    bridge = entorno[0]
    bridge.run_prompt.side_effect = [{'puesto': 'ANALISTA TÉCNICO FUNCIONAL'},
                                   {'cv_markdown': cv('AI ENGINEER'), 'pitch': '', 'pitches_multicanal': {}}]
    salida = await writer.WriterAgent(bridge).generate_cv_and_pitch(
        {'puesto': 'AI Engineer'}, {'experiencia': EXP, 'personales': PER})
    p = postulacion(salida['cv_markdown'], 'AI Engineer')
    resultado, _, _, _ = await server._asegurar_completo(p['id'], p['cv_markdown'], '', {})
    database.update_postulacion(p['id'], cv_markdown=resultado)
    nuevo = await server._cv_al_dia(database.get_postulacion(p['id']))
    assert nuevo['cv_markdown'] == resultado
    assert bridge.run_prompt.await_count == 2  # una selección y una redacción
    assert resultado.count('Me postulo a la posición de AI Engineer.') == 1
    invariantes(resultado)


@pytest.mark.asyncio
@pytest.mark.parametrize('actual,esperado', [('TECHNICAL FUNCTIONAL ANALYST', 'TECHNICAL FUNCTIONAL ANALYST'),
                                             ('JAVA DEVELOPER', 'ANALISTA TÉCNICO FUNCIONAL'),
                                             ('PYTHON DEVELOPER', 'ANALISTA TÉCNICO FUNCIONAL'),
                                             ('AI ENGINEER', 'ANALISTA TÉCNICO FUNCIONAL')])
async def test_cv_en_ingles_conserva_traduccion_salvo_copia_del_aviso(entorno, actual, esperado):
    # Coordinador 10/11: como corregir_encabezados, en inglés se conserva el título traducido del
    # redactor (casos 2 y 3) salvo que sea copia del aviso; entonces va el rol real.
    oferta = 'Python Developer' if actual != 'AI ENGINEER' else 'AI Engineer'
    d = await titulo_cv.decidir_titulo({'titulo': oferta, 'analisis': {'puesto': oferta}},
                                       titulo_actual=actual, sin_ia=True, ingles=True)
    assert d.titulo == esperado
    entorno[0].run_prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('oferta', ['Python Developer', 'Senior Python Developer', 'Programador Java'])
async def test_ofertas_de_desarrollo_van_al_rol_del_perfil_actual(entorno, oferta):
    # Coordinador 10/11: desarrollo no es el perfil actual (Java fue 2004-2007): caso 3, sin roles de
    # desarrollo; va el rol real más cercano al perfil actual.
    d = await titulo_cv.decidir_titulo({'titulo': oferta, 'analisis': {'puesto': oferta}}, sin_ia=True)
    assert (d.caso, d.titulo) == (3, 'ANALISTA TÉCNICO FUNCIONAL')
    assert 'PROGRAMADOR JAVA' not in d.roles_reales
    p = postulacion(cv('PYTHON DEVELOPER'), oferta)
    nuevo = await server._cv_al_dia(p)
    assert nuevo['cv_markdown'].splitlines()[1] == 'ANALISTA TÉCNICO FUNCIONAL'
    entorno[0].run_prompt.assert_not_awaited()
