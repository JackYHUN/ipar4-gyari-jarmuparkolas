"""Kizárólag loopback interfészre kötött, saját MQTT 3.1.1 közvetítő."""
import asyncio
import threading
from amqtt.broker import Broker


class _ShutdownSafeBroker(Broker):
    """Az amqtt 0.11.3 megszakított küldését a normál leállításban kezeli."""

    def __init__(self, config, normal_stop_requested):
        super().__init__(config)
        self._normal_stop_requested = normal_stop_requested

    async def _shutdown_broadcast_loop(self):
        try:
            await super()._shutdown_broadcast_loop()
        except asyncio.CancelledError:
            # A könyvtár broadcast loopja a már megszakított kliensküldéseket
            # gather()-eli. Ez nem a shutdown coroutine külső megszakítása.
            # A listener- és monitorlezárást ilyenkor is a Broker.shutdown végzi.
            current = asyncio.current_task()
            if (not self._normal_stop_requested() or self._broadcast_task is None
                    or not self._broadcast_task.cancelled()
                    or current is None or current.cancelling()):
                raise
            self._broadcast_shutdown_waiter = asyncio.get_running_loop().create_future()


class LocalBroker:
    def __init__(self, port):
        self.port = port
        self.ready = threading.Event()
        self.error = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="mini-broker", daemon=True)
        self.thread.start()
        if not self.ready.wait(8):
            raise RuntimeError("Az MQTT-közvetítő nem indult el időben")
        if self.error:
            raise RuntimeError(f"Az MQTT-közvetítő nem indult: {self.error}")

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as error:
            self.error = str(error)
            self.ready.set()

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        broker = _ShutdownSafeBroker({
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}"}},
            "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
        }, normal_stop_requested=self.stop.is_set)
        await broker.start()
        self.ready.set()
        try:
            await self.stop.wait()
        finally:
            await broker.shutdown()

    def close(self):
        thread = getattr(self, "thread", None)
        if thread and thread.is_alive():
            loop = getattr(self, "loop", None)
            if loop and not loop.is_closed():
                loop.call_soon_threadsafe(self.stop.set)
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("Az MQTT-közvetítő szála nem állt le időben")
