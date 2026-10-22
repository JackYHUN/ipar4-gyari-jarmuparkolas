"""Kizárólag loopback interfészre kötött, saját MQTT 3.1.1 közvetítő."""
import asyncio
import threading
from amqtt.broker import Broker


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
        broker = Broker({
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}"}},
            "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
        })
        await broker.start()
        self.ready.set()
        try:
            await self.stop.wait()
        finally:
            await broker.shutdown()

    def close(self):
        if getattr(self, "loop", None) and self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.stop.set)
            self.thread.join(timeout=5)
