# Tickets de errores: reglas de dominio

Detalle que `AGENTS.md` resume en un renglón. Valen igual que las reglas de `AGENTS.md`.

- Todo error del portal queda como ticket (`core/tickets.py`, vista «Tickets», botón flotante «!»).
- Para registrar un error nuevo usá `reportar_error(componente, tipo, mensaje, excepcion=, datos=, captura=, html=)`: nunca lanza y suma ocurrencias si el error ya está abierto.
- Los `logger.error/exception` del portal ya generan ticket solos; si además llamás a `reportar_error`, pasá `extra={"sin_ticket": True}` al logger para no duplicar.
- Una búsqueda del Radar con cero resultados es un error grave: ya genera el ticket `radar/sin_resultados` (`RadarLoop._leer_consulta`), con captura y HTML.
- **Los tickets no se arreglan solos:** se arreglan cuando el usuario lo pide.
- **Sin duplicados:** un error ya resuelto que vuelve reabre el mismo ticket (cuenta «Reabierto N veces» y toma el commit actual); nunca abre uno nuevo.
