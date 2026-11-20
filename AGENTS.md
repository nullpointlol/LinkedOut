# AGENTS.md — LinkedOut

Reglas para cualquier agente que trabaje en este repo. El detalle de cada tema está en `docs/`.

## Proyecto
- Portal local FastAPI: lee ofertas (LinkedIn y portales), las analiza con IA y prepara CV y mail.
- Python, SQLite (WAL), HTML/JS sin framework. Textos en castellano rioplatense.
- Los datos del candidato viven fuera del repo (`<DATA_DIR>/candidato/`). Los tests usan el perfil ficticio de `config/ejemplo/`.

## Comandos
- Instalar: `pip install -r requirements-dev.txt`.
- Portal: `python -m uvicorn server:app --port <puerto>`; de prueba, con `LINKEDOUT_DATA_DIR=<copia>` y `LINKEDOUT_SIN_RADAR=1`.
- Tests en la PC: `python tools/tests_rapidos.py` (los de los archivos tocados). La suite completa corre solo en el CI.
- Deploy y rollback: solo `tools/deploy.ps1` y `tools/rollback.ps1` con `-Produccion` ([docs/despliegue.md](docs/despliegue.md)).

## Estructura
- `core/`: motor (Radar, análisis, CV, mail, tickets, perfil). `server.py`: API.
- `static/`, `templates/`: interfaz. `tests/`: pytest. `tools/`: CI, deploy, utilidades. `tools/agents/`: kit externo, no se edita.

## Reglas
- Nada se envía sin aprobación explícita del contenido actual. Ninguna dirección de mail en el código.
- Ningún test arranca el Radar ni usa `data/` real o el puerto de producción.
- Secretos y datos personales: nunca en código, tests, docs ni commits.
- `<DATA_DIR>` no cambia de formato antes de desplegar el código que lo lee.
- Nada de `innerHTML` con datos. Cobertura mínima 96 %. Tests sin tiempos fijos: se espera por condición.
- Una lectura de la base no escribe. El event loop nunca espera a otro hilo en forma síncrona.
- Reglas con números: constantes con nombre. Búsquedas y reglas del Radar: solo en el perfil.
- Nunca `git stash`.

## Commits y PRs
- Conventional Commits: `tipo(ámbito): resumen`, ≤ 60 caracteres, en español.
- Sin cuerpo, salvo contexto técnico imprescindible (≤ 3 líneas). Sin trailers, fechas ni nombres.
- Un PR por cambio, desde `develop` y hacia `develop`. Se integra con squash: el título del PR es el commit. Descripción ≤ 2 líneas.
- Nada entra a `develop` ni a `main` sin el CI en verde (checks `suite`, `windows`, `navegador`, `cobertura`).
- Comentarios: qué y por qué técnico. Nunca quién lo pidió ni cuándo.
- Planes, estado y notas de proceso: fuera de este repo.

## Dominio
- Radar: [docs/radar.md](docs/radar.md) · Perfil: [docs/perfil.md](docs/perfil.md) · Portales: [docs/portales.md](docs/portales.md)
- Tickets: [docs/tickets.md](docs/tickets.md) · Código y tests: [docs/codigo-y-tests.md](docs/codigo-y-tests.md)
- Sintaxis de LinkedIn: [docs/linkedin-sintaxis.md](docs/linkedin-sintaxis.md)
