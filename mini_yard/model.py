"""Kisméretű, determinisztikus parkolási modell, méter és másodperc egységekkel.

A mozgás előírt töröttvonalon történik; az érzékelés a szimulált állapotból
származik. A stopvonal diszkrét korlát, nem valós jármű biztonsági bizonyítéka.
Ez a modul nem használ hálózatot: a visszaadott üzeneteket a runtime MQTT-n küldi.
"""
from __future__ import annotations

from copy import deepcopy
import math


DEFAULT_CONFIG = {
    "car_count": 3, "slot_count": 8, "dt": 0.1, "speed_mps": 3.0,
    "acceleration_mps2": 1.5, "braking_mps2": 2.0,
    "sensor_range_m": 15.0, "heartbeat_timeout_s": 1.0,
}


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _config(config):
    result = DEFAULT_CONFIG | dict(config or {})
    for key, upper in (("car_count", 6), ("slot_count", 8)):
        if type(result[key]) is not int or not 1 <= result[key] <= upper:
            raise ValueError(f"{key}: 1..{upper} egész érték szükséges")
    for key in DEFAULT_CONFIG.keys() - {"car_count", "slot_count"}:
        if not _number(result[key]) or result[key] <= 0:
            raise ValueError(f"{key}: pozitív véges szám szükséges")
    if result["dt"] > 0.1:
        raise ValueError("A modell legnagyobb lépésköze 0.1 s")
    return result


def _slots(count):
    return [{"id": f"P{i + 1:02d}", "x": 14 + 5 * i, "y": 7,
             "state": "free", "car_id": None, "reservation_id": None}
            for i in range(count)]


def _route(slot):
    return [[8, 28], [56, 28], [56, 14], [slot["x"], 14], [slot["x"], 7]]


class _Producer:
    def __init__(self, config, run_id):
        if not isinstance(run_id, str) or not run_id or len(run_id) > 80:
            raise ValueError("run_id: nem üres, legfeljebb 80 karakteres szöveg szükséges")
        self.config = _config(config)
        self.run_id, self.sim_time, self.seq = run_id, 0.0, 0

    def _emit(self, topic, data):
        self.seq += 1
        return topic, deepcopy(data | {"run_id": self.run_id, "seq": self.seq,
                                      "sim_time": round(self.sim_time, 6)})

    def _valid_envelope(self, payload):
        return (isinstance(payload, dict) and payload.get("run_id") == self.run_id
                and type(payload.get("seq")) is int and payload["seq"] > 0
                and _number(payload.get("sim_time")) and payload["sim_time"] >= 0)


class World(_Producer):
    """A fizikai folyamat és az idealizált fedélzeti állapotgép.

    Egyszerre egy autó kap indulási lehetőséget; a többiek sorban várnak.
    A hálózati és érzékelőhiba azonnal nullázza a sebességet a modellben.
    """

    def __init__(self, config, run_id):
        super().__init__(config, run_id)
        self.slots = _slots(self.config["slot_count"])
        self.cars = [{"id": f"CAR{i + 1:02d}", "car_id": f"CAR{i + 1:02d}",
                      "x": 8 + 6 * i, "y": 35,
                      "heading": 0.0, "speed": 0.0, "state": "waiting",
                      "slot_id": None, "reservation_id": None, "request_id": None,
                      "route": [], "distance_m": 0.0, "reason": "Indulásra vár.",
                      "_segment": 1, "_command_seq": 0, "_next_request": 0.0}
                     for i in range(self.config["car_count"])]
        self.pedestrian = self.sensor_failure = False
        self.active = None
        self.pedestrian_stops = 0
        self._events = []

    def _event(self, kind, car=None, message="", **extra):
        self._events.append({"type": kind, "car_id": car["id"] if car else None,
                             "message": message, **extra})

    def _state(self, car, state, reason):
        previous = car["state"]
        car["state"], car["reason"] = state, reason
        if state != previous:
            if state == "pedestrian_stop":
                self.pedestrian_stops += 1
            self._event("state_changed", car, reason, previous=previous, state=state)

    def control(self, action, data):
        """Csak a két dokumentált, bool kapcsoló fogadható el."""
        if not isinstance(data, dict) or type(data.get("active")) is not bool:
            return False
        if action not in ("pedestrian", "sensor_failure"):
            return False
        value = data["active"]
        if getattr(self, action) != value:
            setattr(self, action, value)
            self._event(action + ("_on" if value else "_off"),
                        message=("Gyalogos" if action == "pedestrian" else "Érzékelőhiba")
                        + (" bekapcsolva." if value else " kikapcsolva."))
        return True

    def handle_command(self, topic, payload):
        """Érvényes, aktuális foglalás fogadása; hibás JSON-adat nem állítja le a modellt."""
        if not self._valid_envelope(payload) or self.active is None:
            return False
        car = self.cars[self.active]
        if (topic != "commands/" + car["id"] or payload.get("car_id") != car["id"]
                or payload.get("request_id") != car["request_id"]
                or payload["seq"] <= car["_command_seq"] or car["slot_id"] is not None):
            return False
        kind = payload.get("kind")
        if kind == "parking_wait":
            car["_command_seq"] = payload["seq"]
            car["reason"] = "Nincs szabad parkolóhely; új kérésre vár."
            return True
        slot = next((s for s in self.slots if s["id"] == payload.get("slot_id")), None)
        if (kind != "parking_grant" or slot is None or slot["state"] != "free"
                or payload.get("reservation_id") != f"{self.run_id}:{slot['id']}:{car['id']}"
                or payload.get("route") != _route(slot)):
            return False
        car.update(slot_id=slot["id"], reservation_id=payload["reservation_id"],
                   route=_route(slot), _command_seq=payload["seq"])
        slot.update(state="reserved", car_id=car["id"], reservation_id=car["reservation_id"])
        self._event("parking_granted", car, "A parkolóhely foglalása megérkezett.",
                    slot_id=slot["id"], reservation_id=car["reservation_id"],
                    request_id=car["request_id"])
        return True

    def _sensor(self, car):
        approaching = car["_segment"] == 1 and car["state"] != "waiting" and car["x"] <= 32
        visible = approaching and 0 <= 30 - car["x"] <= self.config["sensor_range_m"]
        return {"valid": not self.sensor_failure, "range_m": self.config["sensor_range_m"],
                "pedestrian": bool(not self.sensor_failure and self.pedestrian and visible),
                "distance_to_stop_m": round(max(0.0, 25 - car["x"]), 3) if approaching else None}

    def _car_view(self, car):
        return deepcopy({key: value for key, value in car.items() if not key.startswith("_")}
                        | {"sensor": self._sensor(car)})

    def snapshot(self):
        slots = deepcopy(self.slots)
        return {"run_id": self.run_id, "seq": self.seq, "sim_time": round(self.sim_time, 6),
                "cars": [self._car_view(c) for c in self.cars], "slots": slots,
                "crossing": {"active": self.pedestrian, "x": 30, "y": 28},
                "metrics": {"completed": sum(c["state"] == "parked" for c in self.cars),
                            "total": len(self.cars), "pedestrian_stops": self.pedestrian_stops,
                            "distance_m": round(sum(c["distance_m"] for c in self.cars), 3)},
                "map": {"width": 64, "height": 38,
                        "road": [[8, 28], [56, 28], [56, 14], [14, 14]],
                        "slots": slots, "crossing": {"x": 30, "y": 28}}}

    def _advance(self, car, distance):
        """Ívhossz szerint mozgatás az előírt útvonalon, sarkokon át is."""
        while distance > 1e-10 and car["_segment"] < len(car["route"]):
            target = car["route"][car["_segment"]]
            dx, dy = target[0] - car["x"], target[1] - car["y"]
            remaining = math.hypot(dx, dy)
            if remaining < 1e-9:
                car["_segment"] += 1
                continue
            travel = min(distance, remaining)
            car["heading"] = math.degrees(math.atan2(dy, dx))
            car["x"] += travel * dx / remaining
            car["y"] += travel * dy / remaining
            car["distance_m"] += travel
            distance -= travel
            if travel >= remaining - 1e-9:
                car["x"], car["y"] = target
                car["_segment"] += 1
        if car["_segment"] >= len(car["route"]):
            car["speed"] = 0.0
            self._state(car, "parked", "A jármű megérkezett a lefoglalt helyre.")
            slot = next(s for s in self.slots if s["id"] == car["slot_id"])
            slot["state"] = "occupied"
            self._event("parked", car, "Parkolás befejezve.", slot_id=car["slot_id"],
                        reservation_id=car["reservation_id"], request_id=car["request_id"],
                        x=car["x"], y=car["y"], distance_m=round(car["distance_m"], 3))
            self.active = None

    def _move(self, car, dt):
        stop = (self._sensor(car)["pedestrian"] and car["_segment"] == 1)
        remaining = max(0.0, 25 - car["x"]) if stop else math.inf
        target = min(self.config["speed_mps"], math.sqrt(2 * self.config["braking_mps2"] * remaining))
        old_speed = car["speed"]
        rate = self.config["acceleration_mps2"] if target >= old_speed else self.config["braking_mps2"]
        car["speed"] = old_speed + max(-rate * dt, min(rate * dt, target - old_speed))
        travel = min((old_speed + car["speed"]) * dt / 2, remaining)
        if stop and remaining <= travel + 1e-9:
            car["speed"] = 0.0
        self._state(car, "pedestrian_stop" if stop and car["speed"] <= 1e-9 else "driving",
                    "Gyalogos miatt áll." if stop and car["speed"] <= 1e-9
                    else "Fékezés a zebra előtt." if stop else "Haladás a lefoglalt parkolóhely felé.")
        self._advance(car, travel)

    def step(self, dt, connected=True):
        if not _number(dt) or not 0 < dt <= 0.1:
            raise ValueError("A lépésköz 0 < dt <= 0.1 s lehet")
        self.sim_time = round(self.sim_time + dt, 9)
        if self.active is None:
            self.active = next((i for i, c in enumerate(self.cars) if c["state"] == "waiting"), None)
            if self.active is not None:
                car = self.cars[self.active]
                # A várakozóhely -> sorvégi kijárat átadás külön nem modellezett,
                # diszkrét esemény; a mért út csak a (8,28) kijárattól indul.
                car.update(x=8.0, y=28.0, request_id=f"{self.run_id}:{car['id']}:parking")
        messages = []
        if self.active is not None:
            car = self.cars[self.active]
            if not connected or self.sensor_failure:
                car["speed"] = 0.0
                self._state(car, "communication_stop" if not connected else "sensor_stop",
                            "A felügyelt szívverés nem érkezik." if not connected else "Az érzékelő hibás.")
            elif car["slot_id"] is None:
                self._state(car, "requesting", "Parkolóhely kiosztására vár.")
                if self.sim_time >= car["_next_request"]:
                    messages.append(self._emit("requests/parking", {
                        "car_id": car["id"], "request_id": car["request_id"]}))
                    car["_next_request"] = self.sim_time + 1.0
            else:
                self._move(car, dt)
        messages.extend(self._emit("events", event) for event in self._events)
        self._events.clear()
        messages.extend(self._emit("telemetry/" + car["id"], self._car_view(car)) for car in self.cars)
        messages.append(self._emit("world", self.snapshot()))
        return messages


class Coordinator(_Producer):
    """Első szabad hely kiosztása; csak MQTT-ről kapott üzenetekből dönt."""

    def __init__(self, config, run_id):
        super().__init__(config, run_id)
        self.slots = _slots(self.config["slot_count"])
        self.reservations = {}
        self._car_ids = {f"CAR{i + 1:02d}" for i in range(self.config["car_count"])}
        self._last_request = {}

    def snapshot(self):
        return {"run_id": self.run_id, "seq": self.seq, "sim_time": round(self.sim_time, 6),
                "slots": deepcopy(self.slots), "reservations": deepcopy(list(self.reservations.values()))}

    def handle(self, topic, payload):
        if (not self._valid_envelope(payload) or not isinstance(payload.get("car_id"), str)
                or payload["car_id"] not in self._car_ids):
            return []
        car_id, request_id = payload["car_id"], payload.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 160:
            return []
        reservation = self.reservations.get(car_id)
        if topic == "events" and payload.get("type") == "parked":
            if (reservation is None or reservation["state"] != "reserved"
                    or payload["seq"] <= self._last_request.get(car_id, 0)
                    or any(payload.get(k) != reservation[k] for k in
                           ("request_id", "slot_id", "reservation_id"))):
                return []
            slot = next(s for s in self.slots if s["id"] == reservation["slot_id"])
            if not (_number(payload.get("x")) and _number(payload.get("y"))
                    and math.hypot(payload["x"] - slot["x"], payload["y"] - slot["y"]) <= 0.25):
                return []
            reservation["state"] = slot["state"] = "occupied"
            self.sim_time = max(self.sim_time, payload["sim_time"])
            return [self._emit("coordinator", self.snapshot())]
        if topic != "requests/parking":
            return []
        if reservation and reservation["request_id"] != request_id:
            return []
        if payload["seq"] < self._last_request.get(car_id, 0):
            return []
        self._last_request[car_id] = payload["seq"]
        self.sim_time = max(self.sim_time, payload["sim_time"])
        if reservation is None:
            slot = next((s for s in self.slots if s["state"] == "free"), None)
            if slot is None:
                return [self._emit("commands/" + car_id, {
                    "kind": "parking_wait", "car_id": car_id, "request_id": request_id,
                    "slot_id": None, "reservation_id": None, "route": []})]
            reservation = {"car_id": car_id, "request_id": request_id, "slot_id": slot["id"],
                           "reservation_id": f"{self.run_id}:{slot['id']}:{car_id}", "state": "reserved"}
            self.reservations[car_id] = reservation
            slot.update(state="reserved", car_id=car_id, reservation_id=reservation["reservation_id"])
        slot = next(s for s in self.slots if s["id"] == reservation["slot_id"])
        return [self._emit("commands/" + car_id, {
            "kind": "parking_grant", **reservation, "route": _route(slot)}),
            self._emit("coordinator", self.snapshot())]
