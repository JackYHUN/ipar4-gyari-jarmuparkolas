"""Egy ütemező, három külön MQTT-kliens; a kijelző kizárólag feliratkozó."""
import copy
import json
import queue
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .broker import LocalBroker
from .model import Coordinator, World
from .mqtt_bus import Bus


class Application:
    def __init__(self, config, root, mqtt_port=19085, initially_paused=False):
        self.config = World(config, "config-validation").config
        self.root, self.mqtt_port = Path(root), mqtt_port
        self.lock = threading.RLock()
        self.controls = queue.Queue()
        self.stop = threading.Event()
        self.paused, self.speed = initially_paused, 1
        self.broker = LocalBroker(mqtt_port)
        self.buses = []
        self.error = None
        self.state_cache = {}

    def start(self):
        self.broker.start()
        try:
            self._new_run()
        except Exception:
            self.close()
            raise
        self.thread = threading.Thread(target=self._work, name="mini-runtime", daemon=True)
        self.thread.start()

    def _new_run(self):
        for bus in self.buses:
            bus.close()
        if getattr(self, "log", None):
            self.log.close()
        self.buses = []
        self.run_id = uuid.uuid4().hex[:10]
        self.world = World(self.config, self.run_id)
        self.coordinator = Coordinator(self.config, self.run_id)
        self.vehicle_bus = Bus(self.mqtt_port, self.run_id, "vehicle", ["commands/+", "service/heartbeat"])
        self.service_bus = Bus(self.mqtt_port, self.run_id, "parking", ["requests/parking", "requests/parking_setup", "events"])
        self.observer_bus = Bus(self.mqtt_port, self.run_id, "observer", ["#"])
        # Observer subscribes first so the first model publication can also be recorded.
        for bus in (self.observer_bus, self.vehicle_bus, self.service_bus):
            self.buses.append(bus)
            bus.start()
        directory = self.root / "results" / self.run_id
        directory.mkdir(parents=True, exist_ok=False)
        new_log_path = directory / "mqtt.jsonl"
        new_log = new_log_path.open("w", encoding="utf-8", buffering=1)
        with self.lock:
            self.log_path, self.log = new_log_path, new_log
        metadata = {"run_id": self.run_id, "created_utc": datetime.now(timezone.utc).isoformat(),
                    "config": self.config, "mqtt_port": self.mqtt_port,
                    "origin": "AI segítséggel készített oktatási demonstrátor"}
        (directory / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        self.flags = dict(pedestrian=False, sensor_failure=False, link_failure=False)
        self.events, self.messages = deque(maxlen=80), deque(maxlen=120)
        self.observed_world, self.observed_coordinator = {}, {}
        self.parking_setup = None
        with self.lock:
            self._setup_controls = {}
        self.last_heartbeat = None
        self.last_message = None
        self.heartbeat_sent = self.coordinator_sent = 0
        self.accumulator = 0
        self.event_keys = set()
        self.vehicle_bus.publish("world", self.world.snapshot(), qos=0)
        self.service_bus.publish("coordinator", self.coordinator.snapshot(), qos=0)

    def command(self, data):
        action = data.get("action")
        if action not in {"pause", "resume", "reset", "speed", "pedestrian", "sensor_failure", "link_failure", "spawn_parked"}:
            raise ValueError("Ismeretlen művelet")
        if action == "speed" and (type(data.get("value")) is not int or data["value"] not in (1, 2, 4)):
            raise ValueError("A sebesség 1, 2 vagy 4 lehet")
        if action in self.flags and type(data.get("active")) is not bool:
            raise ValueError("Az active mezőnek logikai értéknek kell lennie")
        if action == "spawn_parked":
            slot_id, request_id = data.get("slot_id"), data.get("request_id", uuid.uuid4().hex)
            if not isinstance(slot_id, str) or re.fullmatch(r"P[0-9]{2}", slot_id) is None:
                raise ValueError("A slot_id például P01 alakú parkolóhely-azonosító legyen")
            if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 160
                    or re.fullmatch(r"[A-Za-z0-9_.:-]+", request_id) is None):
                raise ValueError("A request_id legfeljebb 160 betűt, számot vagy _ . : - jelet tartalmazhat")
            with self.lock:
                if data.get("run_id", self.run_id) != self.run_id:
                    raise ValueError("A futásazonosító megváltozott; frissítsd a nézetet")
                existing = self._setup_controls.get(request_id)
                if existing is not None and existing != slot_id:
                    raise ValueError("A kérésazonosító már másik parkolóhelyhez tartozik")
                if existing is None and len(self._setup_controls) >= 256:
                    raise ValueError("A futás parkolóbeállítási kéréskerete betelt; indíts új futást")
                self._setup_controls[request_id] = slot_id
                self.controls.put(dict(data, request_id=request_id, run_id=self.run_id))
            return {"request_id": request_id}
        self.controls.put(dict(data))
        return {}

    def _apply_controls(self):
        while True:
            try:
                data = self.controls.get_nowait()
            except queue.Empty:
                return
            action = data["action"]
            if action == "reset":
                self._new_run()
                self.paused = True
            elif action in ("pause", "resume"):
                self.paused = action == "pause"
            elif action == "speed":
                self.speed = data["value"]
            elif action == "spawn_parked":
                if data["run_id"] != self.run_id:
                    # A reset előtti kezelői kérés nem változtathatja az új futást.
                    continue
                topic, payload = self.world.request_parked_car(data["slot_id"], data["request_id"])
                self.vehicle_bus.publish(topic, payload)
            else:
                self.flags[action] = data["active"]
                if action != "link_failure":
                    self.world.control(action, data)
            self.vehicle_bus.publish("world", self.world.snapshot(), qos=0)
            self._record_control(data)

    def _record_control(self, data):
        record = {"received_utc": datetime.now(timezone.utc).isoformat(), "topic": "operator/control",
                  "relative_topic": "operator/control", "qos": None,
                  "payload": dict(data, run_id=self.run_id, sim_time=self.world.snapshot()["sim_time"]),
                  "latency_ms": None}
        self.log.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _connected(self, now):
        return (self.vehicle_bus.ready.is_set() and self.last_heartbeat is not None
                and now - self.last_heartbeat <= self.config["heartbeat_timeout_s"])

    def _drain_network(self):
        for record, received_ns in self.vehicle_bus.drain():
            topic, payload = record["relative_topic"], record["payload"]
            # A kezelői foglaltságbeállítás külön menedzsmentút; a demonstrált
            # hiba csak a jármű életjelét és normál mozgási engedélyeit ejti el.
            if topic == "commands/parking_setup":
                if self.world.handle_command(topic, payload):
                    for event_topic, event in self.world.flush_events():
                        self.vehicle_bus.publish(event_topic, event)
                    self.vehicle_bus.publish("world", self.world.snapshot(), qos=0)
                continue
            if not self.flags["link_failure"]:
                if topic == "service/heartbeat":
                    self.last_heartbeat = received_ns / 1e9
                elif topic.startswith("commands/"):
                    self.world.handle_command(topic, payload)
        for record, _ in self.service_bus.drain():
            for topic, payload in self.coordinator.handle(record["relative_topic"], record["payload"]):
                self.service_bus.publish(topic, payload)
        for record, received_ns in self.observer_bus.drain():
            self.last_message = received_ns / 1e9
            self.log.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.messages.append(record)
            topic, payload = record["relative_topic"], record["payload"]
            if topic == "world":
                self.observed_world = payload
            elif topic == "coordinator":
                self.observed_coordinator = payload
            elif topic == "commands/parking_setup":
                self.parking_setup = payload
            elif topic == "events":
                key = (payload.get("seq"), payload.get("type"), payload.get("car_id"))
                if key not in self.event_keys:
                    self.event_keys.add(key)
                    self.events.append(dict(payload, received_utc=record["received_utc"]))

    def _cache_state(self, now):
        age = None if self.last_heartbeat is None else max(0, now - self.last_heartbeat)
        cache = {
            "run_id": self.run_id, "sim_time": self.observed_world.get("sim_time", 0),
            "paused": self.paused, "speed": self.speed, "connected": self._connected(now),
            "mqtt": {"port": self.mqtt_port, "published": sum(b.published for b in self.buses),
                     "received": self.observer_bus.received, "invalid": sum(b.invalid for b in self.buses),
                     "last_message_age_s": None if self.last_message is None else max(0, now - self.last_message),
                     "heartbeat_age_s": age, "clients": {b.role: b.ready.is_set() for b in self.buses}},
            "world": self.observed_world, "coordinator": self.observed_coordinator,
            "parking_setup": self.parking_setup,
            "events": list(self.events), "messages": list(self.messages), "controls": dict(self.flags),
            "log_file": str(self.log_path.relative_to(self.root)), "error": self.error,
        }
        with self.lock:
            self.state_cache = copy.deepcopy(cache)

    def _work(self):
        previous = time.perf_counter()
        dt = self.config["dt"]
        try:
            while not self.stop.is_set():
                self._apply_controls()
                now = time.perf_counter()
                elapsed, previous = min(now - previous, 0.25), now
                if now - self.heartbeat_sent >= 0.2:
                    self.heartbeat_sent = now
                    if not self.flags["link_failure"]:
                        self.service_bus.publish("service/heartbeat", {"sim_time": self.world.snapshot()["sim_time"]})
                self._drain_network()
                if self.paused:
                    self.accumulator = 0
                else:
                    self.accumulator += elapsed * self.speed
                    while self.accumulator >= dt:
                        for topic, payload in self.world.step(dt, connected=self._connected(now)):
                            self.vehicle_bus.publish(topic, payload, qos=0 if topic == "world" or topic.startswith("telemetry/") else 1)
                        self.accumulator -= dt
                if now - self.coordinator_sent >= 0.25:
                    self.coordinator_sent = now
                    self.service_bus.publish("coordinator", self.coordinator.snapshot(), qos=0)
                self._cache_state(now)
                self.stop.wait(0.01)
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"
            self.paused = True
            self._cache_state(time.perf_counter())

    def state(self):
        with self.lock:
            return copy.deepcopy(self.state_cache)

    def export_records(self):
        # Runtime uses a line-buffered writer; only complete JSONL lines are exported.
        with self.lock:
            path = self.log_path
        records = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def close(self):
        self.stop.set()
        if getattr(self, "thread", None):
            self.thread.join(timeout=8)
        for bus in self.buses:
            bus.close()
        if getattr(self, "log", None):
            self.log.close()
        self.broker.close()
