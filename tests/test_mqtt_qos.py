"""Valódi helyi brokerrel ellenőrzi a megfigyelő két QoS-osztályát."""
import socket
import tempfile
import time
import unittest
from pathlib import Path

from mini_yard.runtime import Application


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ObserverQosTests(unittest.TestCase):
    def test_snapshots_and_control_messages_keep_distinct_qos(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application({"car_count": 1, "slot_count": 1}, Path(tmp),
                              mqtt_port=free_port(), initially_paused=True)
            try:
                app.start()
                deadline = time.monotonic() + 6
                while not app.state().get("ready"):
                    self.assertLess(time.monotonic(), deadline, app.state())
                    time.sleep(0.03)
                app.command({"action": "resume"})
                wanted = {"world": 0, "coordinator": 0, "telemetry/CAR01": 0,
                          "service/heartbeat": 1, "requests/parking": 1,
                          "commands/CAR01": 1, "events": 1}
                observed = {}
                while not wanted.keys() <= observed.keys():
                    state = app.state()
                    self.assertFalse(state.get("error"), state.get("error"))
                    for row in state.get("messages", []):
                        topic = row["relative_topic"]
                        if topic in wanted:
                            self.assertEqual(row["qos"], wanted[topic], topic)
                            observed[topic] = row["qos"]
                    self.assertLess(time.monotonic(), deadline, observed)
                    time.sleep(0.03)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
