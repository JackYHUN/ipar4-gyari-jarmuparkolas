"""Brokerleállítási regresszió valódi amqtt életciklussal, hálózati port nélkül."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from amqtt.broker import Broker
from mini_yard.broker import LocalBroker, _ShutdownSafeBroker


class BrokerShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def make_broker(self):
        stop = asyncio.Event()
        # A bind=None a könyvtár listener-kihagyási ága; nem nyit portot.
        broker = _ShutdownSafeBroker({
            "listeners": {"default": {"type": "tcp", "bind": None}},
            "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
        }, normal_stop_requested=stop.is_set)
        await broker.start()
        await asyncio.sleep(0)  # A valódi broadcast loop már a következő üzenetre vár.
        return broker, stop

    async def test_cancelled_publish_still_closes_listener_and_stops_broker(self):
        broker, stop = await self.make_broker()
        listener = AsyncMock()
        broker._servers["test-listener"] = listener
        cancelled_publish = asyncio.get_running_loop().create_future()
        cancelled_publish.cancel()
        broker._tasks_queue.append(cancelled_publish)
        stop.set()
        await asyncio.wait_for(broker.shutdown(), timeout=3)
        self.assertTrue(broker._broadcast_task.cancelled())
        listener.close_instance.assert_awaited_once()
        self.assertEqual(broker.transitions.state, "stopped")
        self.assertTrue(broker._session_monitor_task.cancelling())

    async def test_unrelated_broadcast_failure_is_not_suppressed(self):
        broker, stop = await self.make_broker()
        failed_publish = asyncio.get_running_loop().create_future()
        failed_publish.set_exception(RuntimeError("unexpected-publish-failure"))
        broker._tasks_queue.append(failed_publish)
        stop.set()
        with self.assertRaisesRegex(RuntimeError, "unexpected-publish-failure"):
            await asyncio.wait_for(broker.shutdown(), timeout=3)
        # A teszt szándékosan hibás leállítási ágát explicit takarítjuk.
        broker._session_monitor_task.cancel()
        await asyncio.gather(broker._session_monitor_task, return_exceptions=True)

    async def test_external_shutdown_cancellation_is_not_suppressed(self):
        broker, stop = await self.make_broker()
        entered = asyncio.Event()
        never = asyncio.Event()

        async def interrupted_shutdown(instance):
            entered.set()
            await never.wait()

        # Még normálstop-jel és cancelled broadcast mellett sem nyelhető el
        # a leállítást végző task kívülről kért megszakítása.
        with self.assertLogs("amqtt.broker", level="ERROR") as intentional_cancel:
            broker._broadcast_task.cancel()
            await asyncio.gather(broker._broadcast_task, return_exceptions=True)
        self.assertTrue(any("Broadcast loop stopped by exception" in line
                            for line in intentional_cancel.output))
        stop.set()
        with patch.object(Broker, "_shutdown_broadcast_loop", interrupted_shutdown):
            shutdown = asyncio.create_task(broker._shutdown_broadcast_loop())
            await entered.wait()
            shutdown.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await shutdown
        broker._session_monitor_task.cancel()
        await asyncio.gather(broker._session_monitor_task, return_exceptions=True)


class BrokerThreadTests(unittest.TestCase):
    def test_startup_failure_is_reported_and_thread_can_be_closed(self):
        async def failed_startup(instance):
            raise RuntimeError("test-startup-failure")

        broker = LocalBroker(0)
        with patch.object(LocalBroker, "_serve", failed_startup):
            with self.assertRaisesRegex(RuntimeError, "test-startup-failure"):
                broker.start()
            broker.close()
        self.assertTrue(broker.ready.is_set())
        self.assertEqual(broker.error, "test-startup-failure")
        self.assertFalse(broker.thread.is_alive())
        broker.close()  # Az ismételt lezárás sem hagy háttérszálat.


if __name__ == "__main__":
    unittest.main()
