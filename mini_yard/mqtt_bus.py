"""A három szerepkör valódi TCP MQTT-kapcsolata. A callback csak sorba tesz."""
import hmac
import json
import queue
import re
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from .protocol import producer_for, strict_json, valid_message


def _signature(key, topic, payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hmac.digest(key, topic.encode("utf-8") + b"\0" + canonical, "sha256").hex()


class Bus:
    def __init__(self, port, run_id, role, subscriptions, key):
        self.port, self.run_id, self.role = port, run_id, role
        self.prefix = f"mini-yard/{run_id}/"
        self.inbox = queue.Queue(maxsize=2048)
        self.ready = threading.Event()
        self.published = self.received = self.invalid = self.sequence = 0
        self._key = key
        self._latest = {}
        self._recent = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"mini-{secrets.token_hex(12)}", protocol=mqtt.MQTTv311)
        # A megfigyelő nagy gyakoriságú mintái QoS 0-val, a vezérlési
        # üzenetek QoS 1-gyel érkeznek. A régi str lista továbbra is QoS 1.
        self.subscriptions = [(item, 1) if isinstance(item, str) else tuple(item)
                              for item in subscriptions]
        self.client.on_connect = self._connect
        self.client.on_subscribe = self._subscribed
        self.client.on_disconnect = lambda *_: self.ready.clear()
        self.client.on_message = self._message
        self.client.reconnect_delay_set(1, 2)

    def _connect(self, client, userdata, flags, reason, properties):
        if reason == 0:
            client.subscribe([(self.prefix + item, qos) for item, qos in self.subscriptions])

    def _subscribed(self, client, userdata, mid, reasons, properties):
        if len(reasons) == len(self.subscriptions) and all(not reason.is_failure for reason in reasons):
            self.ready.set()

    def _message(self, client, userdata, message):
        try:
            topic = message.topic[len(self.prefix):]
            payload = strict_json(message.payload) if len(message.payload) <= 65536 else None
            if not message.topic.startswith(self.prefix) or not isinstance(payload, dict):
                self.invalid += 1
                return
            received_ns = time.perf_counter_ns()
            signature = payload.pop("_auth", None)
            producer = producer_for(topic)
            sent_ns = payload.get("sent_ns")
            message_id = payload.get("message_id")
            serial = (re.fullmatch(r"(vehicle|parking):([1-9][0-9]{0,18})", message_id)
                      if isinstance(message_id, str) else None)
            if (producer is None or producer != payload.get("producer") or not serial or serial[1] != producer
                    or type(sent_ns) is not int or not 0 <= sent_ns <= received_ns
                    or not isinstance(signature, str) or not re.fullmatch("[0-9a-f]{64}", signature)
                    or not hmac.compare_digest(signature, _signature(self._key, message.topic, payload))
                    or not valid_message(topic, payload, self.run_id)):
                self.invalid += 1
                return
            is_snapshot = topic in ("world", "coordinator", "service/heartbeat")
            sequence = int(serial[2])
            latest = self._latest.get(topic, 0)
            recent = None
            if not is_snapshot:
                if topic not in self._recent:
                    self._recent[topic] = OrderedDict()
                recent = self._recent[topic]
            if is_snapshot and sequence <= latest:
                return
            if recent is not None and (sequence <= latest - 2048 or sequence in recent):
                return
            record = {
                "received_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "topic": message.topic, "relative_topic": topic,
                "qos": message.qos, "payload": payload,
            }
            record["latency_ms"] = (received_ns - sent_ns) / 1e6
            self.inbox.put_nowait((record, received_ns))
            self._latest[topic] = max(latest, sequence)
            if recent is not None:
                recent[sequence] = None
                if len(recent) > 2048:
                    recent.popitem(last=False)
            self.received += 1
        except (ValueError, RecursionError, queue.Full):
            self.invalid += 1

    def start(self):
        self.client.connect("127.0.0.1", self.port, keepalive=10)
        self.client.loop_start()
        if not self.ready.wait(5):
            self.close()
            raise RuntimeError(f"Nem indult el az MQTT-feliratkozás: {self.role}")

    def publish(self, topic, payload, qos=1):
        if not self.ready.is_set():
            return False
        if producer_for(topic) != self.role:
            raise ValueError("Az MQTT-szerepkör nem publikálhat ebbe a témába")
        self.sequence += 1
        data = dict(payload, run_id=self.run_id, producer=self.role,
                    sent_ns=time.perf_counter_ns(), message_id=f"{self.role}:{self.sequence}")
        data.setdefault("seq", self.sequence)
        full_topic = self.prefix + topic
        data["_auth"] = _signature(self._key, full_topic, data)
        info = self.client.publish(full_topic, json.dumps(data, ensure_ascii=False, allow_nan=False),
                                   qos=qos, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            self.published += 1
            return True
        return False

    def drain(self):
        for _ in range(256):
            try:
                yield self.inbox.get_nowait()
            except queue.Empty:
                break

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()
