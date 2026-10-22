"""A három szerepkör valódi TCP MQTT-kapcsolata. A callback csak sorba tesz."""
import json
import queue
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


class Bus:
    def __init__(self, port, run_id, role, subscriptions):
        self.port, self.run_id, self.role = port, run_id, role
        self.prefix = f"mini-yard/{run_id}/"
        self.inbox = queue.Queue()
        self.ready = threading.Event()
        self.published = self.received = self.invalid = self.sequence = 0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"mini-{role}-{run_id}", protocol=mqtt.MQTTv311)
        self.subscriptions = subscriptions
        self.client.on_connect = self._connect
        self.client.on_subscribe = lambda *_: self.ready.set()
        self.client.on_disconnect = lambda *_: self.ready.clear()
        self.client.on_message = self._message
        self.client.reconnect_delay_set(1, 2)

    def _connect(self, client, userdata, flags, reason, properties):
        if reason == 0:
            client.subscribe([(self.prefix + item, 1) for item in self.subscriptions])

    def _message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload)
            if not isinstance(payload, dict) or payload.get("run_id") != self.run_id:
                self.invalid += 1
                return
            received_ns = time.perf_counter_ns()
            record = {
                "received_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "topic": message.topic, "relative_topic": message.topic[len(self.prefix):],
                "qos": message.qos, "payload": payload,
            }
            sent_ns = payload.get("sent_ns")
            record["latency_ms"] = ((received_ns - sent_ns) / 1e6
                                     if type(sent_ns) is int and 0 <= sent_ns <= received_ns else None)
            self.received += 1
            self.inbox.put((record, received_ns))
        except (ValueError, UnicodeDecodeError):
            self.invalid += 1

    def start(self):
        self.client.connect("127.0.0.1", self.port, keepalive=10)
        self.client.loop_start()
        if not self.ready.wait(5):
            self.close()
            raise RuntimeError(f"Nem indult el az MQTT-feliratkozás: {self.role}")

    def publish(self, topic, payload, qos=1):
        self.sequence += 1
        data = dict(payload, run_id=self.run_id, producer=self.role,
                    sent_ns=time.perf_counter_ns(), message_id=f"{self.role}:{self.sequence}")
        data.setdefault("seq", self.sequence)
        info = self.client.publish(self.prefix + topic, json.dumps(data, ensure_ascii=False),
                                   qos=qos, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            self.published += 1

    def drain(self):
        while True:
            try:
                yield self.inbox.get_nowait()
            except queue.Empty:
                break

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()
