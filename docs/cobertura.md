# Cobertura de tests

Se mide en la compuerta (`tools/verificar.py`, `--cov=core --cov=server`, sin navegador ni rendimiento). El piso es
`COBERTURA_MINIMA` en `tools/verificar.py`: nunca se baja, y si la cobertura medida sube, el piso sube con ella.
Piso actual: 96 % (última medida registrada acá: 13351 líneas, 96,07 %).

## Lo que queda afuera y por qué
- **Ciclo del Radar** (`core/radar.py`, ≈150 líneas): carreras entre tareas y cierres a mitad de una operación que solo
  aparecen con tiempos reales. Se prueba por comportamiento en `test_radar_*.py` y en el punta a punta
  (`tests/test_e2e_radar_envio.py`: post nuevo → análisis → CV y mail → aprobar → ENVIADO, con dobles solo en LinkedIn,
  la IA y SMTP).
- **Pipeline del CV en `server.py`** (≈120 líneas): ramas internas que dependen de respuestas de IA concretas, `lifespan`
  al apagar con error del Radar y registros defensivos que no fallan con una base sana.
- **Base dañada** (`core/radar_store.py`, ≈20 líneas): JSON ilegible, fechas que no son ISO y carreras de `tomar_*`;
  solo pasan con una base editada a mano o con el ciclo del Radar.
- **Inalcanzable:** `core/writer.py` (cortes por `## ` dentro de una sección y etiquetas sin `:`) y
  `clean_and_map` dentro de `aplicar_aprendizajes_a_analisis` en `core/aprendizajes.py`.
