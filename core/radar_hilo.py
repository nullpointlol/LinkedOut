"""El Radar en su propio hilo, con su propio event loop (12/11).

El `RadarLoop` llama a SQLite en forma síncrona. En el event loop del portal, cada espera de la base
frenaba todos los pedidos HTTP (medido: 63 % del loop en SQLite del Radar y 5,5 s frenado detrás de
una escritura). Acá el Radar corre igual que siempre, en un solo loop y sin carreras nuevas, pero en
otro hilo: el portal deja de esperar su trabajo.

`RadarEnHilo` envuelve un `RadarLoop` con la misma interfaz:
- un método async del Radar (start, stop, wake_*, retry…) se ejecuta en el loop del Radar y el
  llamador espera el resultado sin bloquear su propio loop;
- un atributo o método síncrono (is_running, en_vuelo, etapas_generacion, estado_cuentas…) se lee
  directo, como hoy lo leen los endpoints desde el threadpool.
Playwright no cambia: el harvester ya corre en su propio hilo y se espera desde el loop del Radar.
"""
import asyncio
import functools
import inspect
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tope de espera para arrancar o cerrar el hilo (solo contra bloqueos)
GUARDA_HILO_S = 60


class RadarEnHilo:
    """Corre un `RadarLoop` en un hilo propio. Se arranca solo con la primera llamada async."""

    def __init__(self, radar: Any, nombre: str = "radar") -> None:
        self._radar = radar
        self._nombre = nombre
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._hilo: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- hilo

    @property
    def radar(self) -> Any:
        return self._radar

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """El loop del Radar mientras el hilo vive; None si no arrancó o ya se cerró."""
        loop = self._loop
        return loop if loop is not None and loop.is_running() else None

    @property
    def hilo_vivo(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    def _arrancar(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self.loop is not None:
                return self._loop
            listo = threading.Event()
            loop = asyncio.new_event_loop()

            def correr():
                asyncio.set_event_loop(loop)
                loop.call_soon(listo.set)
                try:
                    loop.run_forever()
                finally:
                    try:
                        _cancelar_pendientes(loop)
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    finally:
                        loop.close()

            self._loop = loop
            self._hilo = threading.Thread(target=correr, name=f"{self._nombre}-loop", daemon=True)
            self._hilo.start()
            if not listo.wait(GUARDA_HILO_S):
                raise RuntimeError("El hilo del Radar no arrancó")
            return loop

    async def _en_radar(self, fn, *args: Any, **kwargs: Any) -> Any:
        """Ejecuta la corrutina `fn(*args)` en el loop del Radar y devuelve su resultado (o su excepción).
        Si ya se está en ese loop, la ejecuta directo."""
        try:
            actual = asyncio.get_running_loop()
        except RuntimeError:
            actual = None
        loop = self.loop
        if loop is not None and actual is loop:
            return await fn(*args, **kwargs)
        loop = loop or self._arrancar()
        futuro = asyncio.run_coroutine_threadsafe(fn(*args, **kwargs), loop)
        return await asyncio.wrap_future(futuro)

    async def cerrar(self) -> None:
        """Apagado del portal: corta el Radar (`stop_for_shutdown`) y termina el hilo. Sin hilo, corta el
        Radar en el loop actual (nunca arrancó en otro)."""
        if self.loop is None:
            await self._radar.stop_for_shutdown()
            return
        try:
            await self._en_radar(self._radar.stop_for_shutdown)
        finally:
            await asyncio.to_thread(self._terminar_hilo)

    def cerrar_ya(self) -> None:
        """Como `cerrar()` pero desde código síncrono (fin de un test, salida del proceso)."""
        loop = self.loop
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._radar.stop_for_shutdown(), loop).result(GUARDA_HILO_S)
        finally:
            self._terminar_hilo()

    def _terminar_hilo(self) -> None:
        with self._lock:
            loop, hilo = self._loop, self._hilo
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
            if hilo is not None:
                hilo.join(GUARDA_HILO_S)
                if hilo.is_alive():
                    logger.error("[Radar] El hilo del Radar no terminó a tiempo")
            self._loop = self._hilo = None

    # ---------------------------------------------------------------- interfaz del Radar

    async def stop_for_shutdown(self) -> None:
        await self.cerrar()

    def __getattr__(self, nombre: str) -> Any:
        valor = getattr(self._radar, nombre)
        if inspect.iscoroutinefunction(valor):
            @functools.wraps(valor)
            async def en_radar(*args: Any, **kwargs: Any) -> Any:
                return await self._en_radar(valor, *args, **kwargs)
            return en_radar
        return valor


def _cancelar_pendientes(loop: asyncio.AbstractEventLoop) -> None:
    """Como `asyncio.run` al cerrar: cancela lo que quedó y espera a que termine."""
    pendientes = [t for t in asyncio.all_tasks(loop) if not t.done()]
    for tarea in pendientes:
        tarea.cancel()
    if pendientes:
        loop.run_until_complete(asyncio.gather(*pendientes, return_exceptions=True))
