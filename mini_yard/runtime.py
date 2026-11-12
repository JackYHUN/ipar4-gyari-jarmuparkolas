"""Egy ütemező, három külön MQTT-kliens; a kijelző kizárólag feliratkozó."""
import copy
import json
import queue
import re
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .broker import LocalBroker
from .model import HEARTBEAT_PERIOD, Coordinator, World
from .mqtt_bus import Bus


CONTROL_EVENT_TYPES = {"pedestrian_on", "pedestrian_off", "sensor_failure_on", "sensor_failure_off"}
CONTROL_EVENT_RETRY_S = 0.5
CONTROL_EVENT_LIMIT = 256


class RuntimeUnavailable(RuntimeError):
    pass


class ParkingSetupConflict(RuntimeError):
    pass


class Application:
    def __init__(self, config, root, mqtt_port=19085, initially_paused=False):
        self.config = World(config, "config-validation").config
        self.root, self.mqtt_port = Path(root), mqtt_port
        self.lock = threading.RLock()
        self.controls = queue.Queue(maxsize=256)
        self.stop = threading.Event()
        self.paused, self.speed = initially_paused, 1
        self.broker = LocalBroker(mqtt_port)
        self.buses = []
        self._control_events = deque()
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
        with self.lock:
            self.state_cache = {"run_id": getattr(self, "run_id", None), "ready": False}
        self._finish_control_events()
        for bus in self.buses:
            bus.close()
        if getattr(self, "log", None):
            self._write_clock()
            self.log.close()
        self.buses = []
        # A futás és a hozzá tartozó kéréskeret együtt váltson. A HTTP-szál
        # az MQTT-kliensek újraindulása közben is fogadhat kezelői kérést.
        with self.lock:
            self.run_id = uuid.uuid4().hex[:10]
            self._setup_controls = {}
            self._setup_by_slot = {}
            self.state_cache = {"run_id": self.run_id, "ready": False}
        self.world = World(self.config, self.run_id)
        self.coordinator = Coordinator(self.config, self.run_id)
        key = secrets.token_bytes(32)
        self.vehicle_bus = Bus(self.mqtt_port, self.run_id, "vehicle", ["commands/+", "service/heartbeat"], key)
        self.service_bus = Bus(self.mqtt_port, self.run_id, "parking", ["requests/parking", "requests/parking_setup", "events"], key)
        self.observer_bus = Bus(self.mqtt_port, self.run_id, "observer", ["#"], key)
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
        self.setup_results = {}
        self.setup_waiting = deque()
        self.setup_current = None
        self.last_heartbeat = None
        self.last_message = None
        self.last_world = self.last_coordinator = None
        self.heartbeat_sent = self.coordinator_sent = self.world_sent = 0
        self.accumulator = 0
        self.event_keys = deque(maxlen=512)
        self._control_event_retry_at = 0
        self._last_logged_control_event = 0
        self.dropped_model_s = self.running_wall_s = 0.0
        self.run_completed = False
        self.vehicle_bus.publish("world", self.world.snapshot(), qos=0)
        self.service_bus.publish("coordinator", self.coordinator.snapshot(), qos=0)

    def command(self, data):
        action = data.get("action")
        if not isinstance(action, str) or action not in {
                "pause", "resume", "reset", "speed", "pedestrian", "sensor_failure",
                "link_failure", "spawn_parked", "remove_parked"}:
            raise ValueError("Ismeretlen művelet")
        if action == "speed" and (type(data.get("value")) is not int or data["value"] not in (1, 2, 4)):
            raise ValueError("A sebesség 1, 2 vagy 4 lehet")
        toggles = ("pedestrian", "sensor_failure", "link_failure")
        if action in toggles and type(data.get("active")) is not bool:
            raise ValueError("Az active mezőnek logikai értéknek kell lennie")
        setup = action in ("spawn_parked", "remove_parked")
        fields = {"action", "run_id"}
        if setup:
            fields |= {"slot_id", "request_id"} | ({"placement_id"} if action == "remove_parked" else set())
        elif action in toggles:
            fields.add("active")
        elif action == "speed":
            fields.add("value")
        if data.keys() - fields:
            raise ValueError("A művelet ismeretlen mezőt tartalmaz")
        if setup:
            slot_id, request_id = data.get("slot_id"), data.get("request_id", uuid.uuid4().hex)
            if not isinstance(slot_id, str) or re.fullmatch(r"P[0-9]{2}", slot_id) is None:
                raise ValueError("A slot_id például P01 alakú parkolóhely-azonosító legyen")
            if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 160
                    or re.fullmatch(r"[A-Za-z0-9_.:-]+", request_id) is None):
                raise ValueError("A request_id legfeljebb 160 betűt, számot vagy _ . : - jelet tartalmazhat")
            operation = "spawn" if action == "spawn_parked" else "remove"
            placement_id = request_id if operation == "spawn" else data.get("placement_id")
            if (not isinstance(placement_id, str) or not 1 <= len(placement_id) <= 160
                    or re.fullmatch(r"[A-Za-z0-9_.:-]+", placement_id) is None):
                raise ValueError("Az eltávolításhoz érvényes placement_id szükséges")
        with self.lock:
            if data.get("run_id", self.run_id) != self.run_id:
                raise ValueError("A futásazonosító megváltozott; frissítsd a nézetet")
            if (not self.state_cache.get("ready") or not getattr(self, "thread", None)
                    or not self.thread.is_alive() or not all(b.ready.is_set() for b in self.buses)):
                raise RuntimeUnavailable("Az alkalmazás vagy az MQTT-adatút még nem áll készen")
            if setup:
                identity = (slot_id, operation, placement_id)
                existing = self._setup_controls.get(request_id)
                if existing is not None and existing != identity:
                    raise ValueError("A kérésazonosító már másik parkolóbeállításhoz tartozik")
                pending = self._setup_by_slot.get(slot_id)
                if pending is not None and pending != request_id:
                    raise ParkingSetupConflict("A parkolóhely előző művelete még egyeztetésre vár. "
                                               "Ismételd meg az eredeti kérést ugyanazzal az azonosítóval.")
                if existing is None and len(self._setup_controls) >= 256:
                    raise ValueError("A futás parkolóbeállítási kéréskerete betelt; indíts új futást")
                queued = dict(data, request_id=request_id, placement_id=placement_id,
                              run_id=self.run_id)
            else:
                queued = dict(data, run_id=self.run_id)
            try:
                self.controls.put_nowait(queued)
            except queue.Full as error:
                raise RuntimeUnavailable("A vezérlési sor megtelt") from error
            if setup:
                self._setup_controls[request_id] = identity
                self._setup_by_slot[slot_id] = request_id
        if setup:
            return {"request_id": request_id}
        return {}

    def _apply_controls(self):
        for _ in range(32):
            if len(self._control_events) >= CONTROL_EVENT_LIMIT:
                return
            try:
                data = self.controls.get_nowait()
            except queue.Empty:
                return
            if data["run_id"] != self.run_id:
                # A sorba állítás után végrehajtott reset sem engedheti a régi
                # lap vagy korábbi kérés beavatkozását az új futásba.
                continue
            action = data["action"]
            if action == "reset":
                self._new_run()
                self.paused = True
            elif action in ("pause", "resume"):
                self.paused = action == "pause"
            elif action == "speed":
                self.speed = data["value"]
            elif action in ("spawn_parked", "remove_parked"):
                request_id = data["request_id"]
                current = self.setup_results.get(request_id)
                already_queued = any(item["request_id"] == request_id for item in self.setup_waiting)
                active = self.setup_current and self.setup_current["data"]["request_id"] == request_id
                if not already_queued and not active:
                    if current is None or current.get("kind") == "setup_uncertain":
                        self.setup_results[request_id] = {"kind": "setup_pending", "slot_id": data["slot_id"]}
                    self.setup_waiting.append(data)
            else:
                self.flags[action] = data["active"]
                if action != "link_failure":
                    self.world.control(action, data)
                    self._publish_world_events()
            self.vehicle_bus.publish("world", self.world.snapshot(), qos=0)
            self._record_control(data)

    def _record_control(self, data):
        record = {"received_utc": datetime.now(timezone.utc).isoformat(), "topic": "operator/control",
                  "relative_topic": "operator/control", "qos": None,
                  "payload": dict(data, run_id=self.run_id, sim_time=self.world.snapshot()["sim_time"]),
                  "latency_ms": None}
        self.log.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    def _retry_setup(self, now):
        if self.setup_current is None:
            if not self.setup_waiting:
                return
            self.setup_current = dict(data=self.setup_waiting.popleft(), attempts=0, next=now)
        item = self.setup_current
        if now < item["next"] or not all(b.ready.is_set() for b in self.buses):
            return
        data = item["data"]
        request_id = data["request_id"]
        if item["attempts"] >= 12:
            self.setup_results[request_id] = {"kind": "setup_uncertain", "slot_id": data["slot_id"],
                                              "message": "Nincs nyugta. Ismételd meg ugyanazt a kérést."}
            self.setup_current = None
            return
        if data["action"] == "spawn_parked":
            topic, payload = self.world.request_parked_car(data["slot_id"], request_id)
        else:
            topic, payload = self.world.request_parked_removal(data["slot_id"], request_id, data["placement_id"])
        if self.vehicle_bus.publish(topic, payload):
            item["attempts"] += 1
        item["next"] = now + 0.5

    def _connected(self, now):
        return (self.vehicle_bus.ready.is_set() and self.last_heartbeat is not None
                and now - self.last_heartbeat <= self.config["heartbeat_timeout_s"])

    def _publish_world_events(self):
        for event_topic, event in self.world.flush_events():
            if event["type"] in CONTROL_EVENT_TYPES:
                self._control_events.append(event)
            else:
                self.vehicle_bus.publish(event_topic, event)

    def _retry_control_events(self):
        if not self._control_events or not (self.vehicle_bus.ready.is_set() and self.observer_bus.ready.is_set()):
            return
        now = time.monotonic()
        if now >= self._control_event_retry_at:
            self.vehicle_bus.publish("events", self._control_events[0])
            self._control_event_retry_at = now + CONTROL_EVENT_RETRY_S

    def _publish_world_changes(self):
        self._publish_world_events()
        self.vehicle_bus.publish("world", self.world.snapshot(), qos=0)
        self.world_sent = time.perf_counter()

    def _drain_network(self):
        for record, _ in self.vehicle_bus.drain():
            topic, payload = record["relative_topic"], record["payload"]
            # A kezelői foglaltságbeállítás külön menedzsmentút; a demonstrált
            # hiba csak a jármű életjelét és normál mozgási engedélyeit ejti el.
            if topic == "commands/parking_setup":
                if self.world.handle_command(topic, payload):
                    request_id, slot_id = payload["request_id"], payload["slot_id"]
                    if self.setup_current and self.setup_current["data"]["request_id"] == request_id:
                        self.setup_current = None
                    self._publish_world_changes()
                    self.setup_results[request_id] = dict(payload, world_seq=self.world.seq)
                    with self.lock:
                        if self._setup_by_slot.get(slot_id) == request_id:
                            del self._setup_by_slot[slot_id]
                continue
            if not self.flags["link_failure"]:
                if topic == "service/heartbeat":
                    self.last_heartbeat = payload["sent_ns"] / 1e9
                elif topic.startswith("commands/"):
                    if self.world.handle_command(topic, payload) and payload["kind"] == "parking_confirmed":
                        # A nyugta feldolgozása szünetben is lezárja az álló
                        # jármű feladatát. Ehhez nem kell modellidőt léptetni.
                        self._publish_world_changes()
                        self.run_completed = all(c["state"] == "parked" for c in self.world.cars)
        for record, _ in self.service_bus.drain():
            for topic, payload in self.coordinator.handle(record["relative_topic"], record["payload"]):
                self.service_bus.publish(topic, payload)
        self._drain_observer()

    def _finish_control_events(self):
        # Lezárás előtt a megfigyelőnek van ideje kiírni a még úton levő eseményt.
        deadline = time.monotonic() + 1
        while self._control_events:
            self._drain_observer()
            if (not self._control_events or not self.observer_bus.ready.is_set()
                    or time.monotonic() >= deadline):
                break
            self._retry_control_events()
            time.sleep(0.01)
        # Tartós kieséskor a régi futás megőrzi az eseményt helyi rekordként.
        while self._control_events:
            event = self._control_events[0]
            record = {"received_utc": datetime.now(timezone.utc).isoformat(),
                      "topic": "operator/control_event", "relative_topic": "operator/control_event",
                      "qos": None, "payload": event, "latency_ms": None}
            self.log.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            self._control_events.popleft()

    def _drain_observer(self):
        for record, received_ns in self.observer_bus.drain():
            topic, payload = record["relative_topic"], record["payload"]
            self.last_message = received_ns / 1e9
            is_control_event = topic == "events" and payload["type"] in CONTROL_EVENT_TYPES
            if is_control_event and payload["seq"] <= self._last_logged_control_event:
                continue
            self.log.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            if is_control_event:
                self._last_logged_control_event = payload["seq"]
                if self._control_events and self._control_events[0]["seq"] == payload["seq"]:
                    self._control_events.popleft()
                    self._control_event_retry_at = 0
            self.messages.append(record)
            if topic == "world":
                if payload["seq"] >= self.observed_world.get("seq", -1):
                    self.observed_world = payload
                    self.last_world = payload["sent_ns"] / 1e9
            elif topic == "coordinator":
                if payload["seq"] >= self.observed_coordinator.get("seq", -1):
                    self.observed_coordinator = payload
                    self.last_coordinator = payload["sent_ns"] / 1e9
            elif topic == "commands/parking_setup":
                if self.parking_setup is None or payload["seq"] > self.parking_setup["seq"]:
                    self.parking_setup = payload
            elif topic == "events":
                key = (payload.get("seq"), payload.get("type"), payload.get("car_id"))
                if key not in self.event_keys:
                    self.event_keys.append(key)
                    self.events.append(dict(payload, received_utc=record["received_utc"]))

    def _clock_snapshot(self):
        return {"dropped_model_s": round(self.dropped_model_s, 6),
                "running_wall_s": round(self.running_wall_s, 6),
                "model_s": self.world.sim_time,
                "model_to_wall": self.world.sim_time / self.running_wall_s if self.running_wall_s else None}

    def _write_clock(self):
        summary = {"run_id": self.log_path.parent.name, **self._clock_snapshot()}
        self.log_path.with_name("clock.json").write_text(
            json.dumps(summary, allow_nan=False, indent=2) + "\n", encoding="utf-8")

    def _cache_state(self, now):
        age = None if self.last_heartbeat is None else max(0, now - self.last_heartbeat)
        world_age = None if self.last_world is None else max(0, now - self.last_world)
        coordinator_age = None if self.last_coordinator is None else max(0, now - self.last_coordinator)
        fresh = (self.observer_bus.ready.is_set() and world_age is not None
                 and coordinator_age is not None and max(world_age, coordinator_age) <= 2)
        clients = {b.role: b.ready.is_set() for b in self.buses}
        cache = {
            "run_id": self.run_id, "sim_time": self.observed_world.get("sim_time", 0),
            "paused": self.paused, "speed": self.speed, "connected": self._connected(now),
            "ready": not self.error and fresh and all(clients.values()), "config": self.config,
            "clock": self._clock_snapshot(),
            "mqtt": {"port": self.mqtt_port, "published": sum(b.published for b in self.buses),
                     "received": self.observer_bus.received, "invalid": sum(b.invalid for b in self.buses),
                     "last_message_age_s": None if self.last_message is None else max(0, now - self.last_message),
                     "heartbeat_age_s": age, "world_age_s": world_age, "coordinator_age_s": coordinator_age,
                     "fresh": fresh, "clients": clients},
            "world": self.observed_world, "coordinator": self.observed_coordinator,
            "parking_setup": self.parking_setup, "parking_operations": self.setup_results,
            "events": list(self.events), "messages": list(self.messages), "controls": dict(self.flags),
            "log_file": str(self.log_path.relative_to(self.root)), "error": self.error,
        }
        with self.lock:
            self.state_cache = copy.deepcopy(cache)

    def _work(self):
        previous = time.perf_counter()
        run_id = self.run_id
        dt = self.config["dt"]
        try:
            while not self.stop.is_set():
                self._apply_controls()
                now = time.perf_counter()
                if self.run_id != run_id:
                    run_id, previous = self.run_id, now
                elapsed, previous = now - previous, now
                if now - self.heartbeat_sent >= HEARTBEAT_PERIOD:
                    self.heartbeat_sent = now
                    if not self.flags["link_failure"]:
                        self.service_bus.publish("service/heartbeat", {"sim_time": self.world.snapshot()["sim_time"]})
                self._drain_network()
                self._retry_control_events()
                self._retry_setup(now)
                if self.paused or self.run_completed:
                    self.accumulator = 0
                else:
                    self.running_wall_s += elapsed
                    self.dropped_model_s += max(0, elapsed - 0.25) * self.speed
                    self.accumulator += min(elapsed, 0.25) * self.speed
                    for _ in range(8):
                        if self.accumulator < dt or self.stop.is_set():
                            break
                        for topic, payload in self.world.step(dt, connected=self._connected(time.perf_counter())):
                            self.vehicle_bus.publish(topic, payload, qos=0 if topic == "world" or topic.startswith("telemetry/") else 1)
                        self.world_sent = now
                        self.accumulator -= dt
                        if all(c["state"] == "parked" for c in self.world.cars):
                            self.run_completed = True
                            break
                    if self.accumulator >= dt and not self.run_completed:
                        drop = self.accumulator // dt * dt
                        self.dropped_model_s += drop
                        self.accumulator -= drop
                if now - self.world_sent >= 1:
                    self.world_sent = now
                    self.vehicle_bus.publish("world", self.world.snapshot(), qos=0)
                if now - self.coordinator_sent >= 0.25:
                    self.coordinator_sent = now
                    self.service_bus.publish("coordinator", self.coordinator.snapshot(), qos=0)
                self._cache_state(now)
                self.stop.wait(0.01)
            self._finish_control_events()
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"
            self.paused = True
            with self.lock:
                self.state_cache.update(ready=False, paused=True, error=self.error)

    def state(self):
        with self.lock:
            return copy.deepcopy(self.state_cache)

    def export_source(self):
        # Capture the file and byte limit together, even if a reset follows.
        with self.lock:
            path = self.log_path
            return path.parent.name, path, path.stat().st_size

    def export_records(self, path=None, size=None):
        if path is None:
            _, path, size = self.export_source()
        with path.open("rb") as handle:
            while handle.tell() < size and (line := handle.readline(size - handle.tell())):
                if line.endswith(b"\n"):
                    yield json.loads(line)

    def close(self):
        self.stop.set()
        if getattr(self, "thread", None):
            self.thread.join(timeout=8)
        for bus in self.buses:
            bus.close()
        if getattr(self, "log", None) and not self.log.closed:
            self._write_clock()
            self.log.close()
        self.broker.close()
