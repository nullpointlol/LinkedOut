"""Vigía del event loop del portal.

El 12/11 y el 13/11 el portal real dejó de responder con la CPU en 0, y sin un volcado de pilas no se pudo saber
qué lo trababa. Un latido corre en el event loop del portal; un hilo aparte mira que avance. Si no avanza en
`UMBRAL_CUELGUE_S`, escribe con `faulthandler` las pilas de todos los hilos en su log (con fecha y cuánto lleva
trabado). Una sola vez por cuelgue; si sigue trabado, de nuevo cada `REPETIR_CADA_S`. No reinicia ni corta nada:
solo deja la evidencia.
"""
import asyncio
import faulthandler
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

UMBRAL_CUELGUE_S = 10      # segundos sin latido para considerar trabado el loop
REPETIR_CADA_S = 60        # si sigue trabado, cada cuánto se vuelve a volcar
LATIDO_S = 1.0             # cada cuánto late el loop
SONDEO_S = 1.0             # cada cuánto mira el hilo vigía


class Vigia:
    def __init__(self, log: Path) -> None:
        self.log = Path(log)
        self._ultimo = time.monotonic()
        self._latido: Optional[asyncio.Task] = None
        self._hilo: Optional[threading.Thread] = None
        self._parar = threading.Event()

    @property
    def vivo(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    async def iniciar(self) -> None:
        self._ultimo = time.monotonic()
        self._parar.clear()
        self._latido = asyncio.create_task(self._latir(), name="vigia-latido")
        self._hilo = threading.Thread(target=self._vigilar, name="vigia", daemon=True)
        self._hilo.start()

    async def detener(self) -> None:
        self._parar.set()
        if self._latido is not None:
            self._latido.cancel()
            await asyncio.gather(self._latido, return_exceptions=True)
            self._latido = None
        if self._hilo is not None:
            await asyncio.to_thread(self._hilo.join, 10)
            self._hilo = None

    async def _latir(self) -> None:
        while True:
            self._ultimo = time.monotonic()
            await asyncio.sleep(min(LATIDO_S, UMBRAL_CUELGUE_S / 4))

    def _vigilar(self) -> None:
        volcado: Optional[float] = None  # último volcado de este cuelgue
        while not self._parar.wait(SONDEO_S):
            trabado = time.monotonic() - self._ultimo
            if trabado < UMBRAL_CUELGUE_S:
                volcado = None
                continue
            ahora = time.monotonic()
            if volcado is not None and ahora - volcado < REPETIR_CADA_S:
                continue
            volcado = ahora
            self._volcar(trabado)

    def _volcar(self, trabado: float) -> None:
        try:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log, "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} El event loop del portal no avanza hace "
                        f"{trabado:.1f} s: pilas de todos los hilos ===\n")
                f.flush()
                faulthandler.dump_traceback(file=f, all_threads=True)
            logger.error(f"[Vigía] El event loop no avanza hace {trabado:.0f} s: pilas en {self.log}",
                         extra={"sin_ticket": True})
        except Exception:
            logger.exception("[Vigía] No se pudo escribir el volcado", extra={"sin_ticket": True})
