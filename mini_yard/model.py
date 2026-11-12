"""Kisméretű, determinisztikus parkolási modell, méter és másodperc egységekkel.

A megközelítés kijelölt úton, a beállás íven és egyenes szakaszon történik.
Az érzékelés és a véghelyzet ellenőrzése virtuális.
Ez a modul nem használ hálózatot: a visszaadott üzeneteket a runtime MQTT-n küldi.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re


DEFAULT_CONFIG = {
    "car_count": 3, "slot_count": 8, "dt": 0.1, "speed_mps": 3.0,
    "acceleration_mps2": 1.5, "braking_mps2": 2.0,
    "sensor_range_m": 15.0, "heartbeat_timeout_s": 1.0,
}
MIN_DT = 0.025
HEARTBEAT_PERIOD = 0.2
MIN_HEARTBEAT_TIMEOUT = 3 * HEARTBEAT_PERIOD
PARK_RADIUS = 3.0
PARK_SPEED = 1.2
PARK_DWELL = 0.5
PARK_FAST_RETRIES = 12
PARK_RECONCILE_S = 5
STOP_X = 25.0
ZEBRA_X = 30.0
ZEBRA_Y = 28.0
ZEBRA_HALF_LENGTH = 2.1
ZEBRA_HALF_WIDTH = 3.1
CAR_HALF_LENGTH = 2.1
CAR_HALF_WIDTH = 0.9

_STOP_REASONS = {"pedestrian_stop": "pedestrian", "sensor_stop": "sensor_failure",
                 "communication_stop": "communication"}


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _config(config):
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("A konfigurációnak JSON-objektumnak kell lennie")
    unknown = config.keys() - DEFAULT_CONFIG.keys()
    if unknown:
        raise ValueError(f"Ismeretlen konfigurációs mező: {', '.join(sorted(map(str, unknown)))}")
    result = DEFAULT_CONFIG | config
    for key, upper in (("car_count", 6), ("slot_count", 8)):
        if type(result[key]) is not int or not 1 <= result[key] <= upper:
            raise ValueError(f"{key}: 1..{upper} egész érték szükséges")
    ranges = {"dt": (MIN_DT, 0.1), "heartbeat_timeout_s": (round(MIN_HEARTBEAT_TIMEOUT, 2), 60),
              "speed_mps": (0.1, 10), "acceleration_mps2": (0.1, 10),
              "braking_mps2": (0.1, 10), "sensor_range_m": (0.1, 64)}
    for key, (minimum, maximum) in ranges.items():
        if not _number(result[key]) or not minimum <= result[key] <= maximum:
            raise ValueError(f"{key}: {minimum} és {maximum} közötti véges szám szükséges")
    speed, braking = result["speed_mps"], result["braking_mps2"]
    # A stopvonal a jármű középpontjára vonatkozik. Egy lépésnyi reakciót
    # is fenntartunk, mert az érzékelési határ két minta közé eshet.
    if result["sensor_range_m"] < (
            ZEBRA_X - STOP_X + speed * result["dt"] + speed * speed / (2 * braking)):
        raise ValueError("Az érzékelőtáv nem fedezi a megállási vonalat, reakcióutat és fékutat")
    return result


def _slots(count):
    return [{"id": f"P{i + 1:02d}", "x": 14 + 5 * i, "y": 7,
             "state": "free", "car_id": None, "reservation_id": None,
             "placement_id": None}
            for i in range(count)]


def _route(slot):
    x = slot["x"]
    return [[8, 28], [56, 28], [56, 14], [x + PARK_RADIUS, 14], *[
        [x + PARK_RADIUS * (1 - math.sin(i * math.pi / 16)),
         11 + PARK_RADIUS * math.cos(i * math.pi / 16)] for i in range(1, 8)],
        [x, 11], [x, 7]]


def valid_parked_pose(pose, slot):
    if not all(_number(pose.get(key)) for key in ("x", "y", "heading", "speed")):
        return False
    dx, dy = pose["x"] - slot["x"], pose["y"] - slot["y"]
    if math.hypot(dx, dy) > 0.25 or not 0 <= pose["speed"] <= 0.05:
        return False
    if abs((pose["heading"] + 270) % 360 - 180) > 5:
        return False
    angle = math.radians(pose["heading"] % 360)
    c, s = abs(math.cos(angle)), abs(math.sin(angle))
    # A forgatott, 4.2 × 1.8 m-es jármű befoglaló mérete a 3.8 × 4.6 m-es helyen.
    return (abs(dx) + CAR_HALF_LENGTH * c + CAR_HALF_WIDTH * s <= 1.9
            and abs(dy) + CAR_HALF_LENGTH * s + CAR_HALF_WIDTH * c <= 2.3)


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
                      "stop_time_s": dict.fromkeys(_STOP_REASONS.values(), 0.0),
                      "timing": {}, "_phase": "approach", "_arc_m": 0.0,
                      "_stable_s": 0.0, "_late": False, "_reports": 0,
                      "_segment": 1, "_command_seq": 0, "_next_request": 0.0}
                     for i in range(self.config["car_count"])]
        self.pedestrian = self.sensor_failure = False
        self.active = None
        self.parked_cars = []
        self._parking_setup = {}
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
        if not self._valid_envelope(payload):
            return False
        if topic == "commands/parking_setup":
            return self._handle_parking_setup(payload)
        if self.active is None:
            return False
        car = self.cars[self.active]
        if (topic == "commands/" + car["id"] and payload.get("kind") == "parking_confirmed"
                and car["_phase"] == "confirm" and car["_reports"] > 0
                and payload["seq"] > car["_command_seq"]
                and all(payload.get(k) == car[k] for k in ("car_id", "request_id", "slot_id", "reservation_id"))):
            slot = next(s for s in self.slots if s["id"] == car["slot_id"])
            slot["state"] = "occupied"
            car["timing"]["confirmed_s"] = self.sim_time
            self._state(car, "parked", "A koordinátor nyugtázta az ellenőrzött beparkolást.")
            self._event("parked", car, "Parkolás befejezve.", **self._parking_result(car),
                        distance_m=round(car["distance_m"], 3), timing=car["timing"].copy())
            self.active = None
            return True
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
        car["timing"]["granted_s"] = self.sim_time
        self._event("parking_granted", car, "A parkolóhely foglalása megérkezett.",
                    slot_id=slot["id"], reservation_id=car["reservation_id"],
                    request_id=car["request_id"])
        return True

    def request_parked_car(self, slot_id, request_id):
        """Kezdeti foglaltság módosításának kérése; a döntést a koordinátor hozza."""
        return self._request_parking_setup(slot_id, request_id, "spawn", request_id)

    def request_parked_removal(self, slot_id, request_id, placement_id):
        """Csak a megnevezett, kézzel elhelyezett járműpéldány eltávolítása."""
        return self._request_parking_setup(slot_id, request_id, "remove", placement_id)

    def _request_parking_setup(self, slot_id, request_id, operation, placement_id):
        if (not isinstance(slot_id, str) or re.fullmatch(r"P[0-9]{2}", slot_id) is None
                or not isinstance(request_id, str) or not request_id or len(request_id) > 160
                or not isinstance(placement_id, str) or not placement_id or len(placement_id) > 160):
            raise ValueError("Hibás parkolóhely- vagy kérésazonosító")
        existing = self._parking_setup.get(request_id)
        identity = {"slot_id": slot_id, "operation": operation, "placement_id": placement_id}
        if existing is not None and any(existing[key] != value for key, value in identity.items()):
            raise ValueError("A kérésazonosító már másik parkolóbeállításhoz tartozik")
        if existing is None:
            if any(item["slot_id"] == slot_id and item["kind"] is None for item in self._parking_setup.values()):
                raise ValueError("A parkolóhely előző művelete még egyeztetésre vár.")
            if len(self._parking_setup) >= 256:
                raise ValueError("A futás parkolóbeállítási kéréskerete betelt; indíts új futást")
            self._parking_setup[request_id] = identity | {"seq": 0, "kind": None}
        return self._emit("requests/parking_setup", identity | {
            "request_id": request_id, "car_id": "PARK-" + slot_id})

    def _handle_parking_setup(self, payload):
        request_id = payload.get("request_id")
        if not isinstance(request_id, str):
            return False
        pending = self._parking_setup.get(request_id)
        kind, slot_id = payload.get("kind"), payload.get("slot_id")
        operation = payload.get("operation", "spawn")
        placement_id = payload.get("placement_id", request_id if operation == "spawn" else None)
        if (pending is None or pending["slot_id"] != slot_id
                or payload["seq"] <= pending["seq"]
                or operation != pending["operation"] or placement_id != pending["placement_id"]
                or kind not in ("parked_spawned", "parked_removed", "parking_setup_rejected")
                or (kind == "parked_spawned" and operation != "spawn")
                or (kind == "parked_removed" and operation != "remove")
                or payload.get("car_id") != "PARK-" + slot_id
                or not isinstance(payload.get("message"), str)
                or (pending["kind"] is not None and kind != pending["kind"])):
            return False
        # A QoS 1 ismétlés ugyanazt az eredményt adhatja új sorszámmal. Egy
        # lezárt kérés mellékhatása későbbi törlés/újraelhelyezés után sem ismétlődik.
        if pending["kind"] is not None:
            pending["seq"] = payload["seq"]
            return True
        if kind == "parked_spawned":
            slot = next((s for s in self.slots if s["id"] == slot_id), None)
            if (slot is None or any(payload.get(key) != value for key, value in
                                    (("x", slot["x"]), ("y", slot["y"]), ("heading", -90)))
                    or (slot["state"] != "free" and not
                        (slot["state"] == "occupied" and slot["car_id"] == payload["car_id"]
                         and slot.get("placement_id") == placement_id))):
                return False
            if not any(car["id"] == payload["car_id"] for car in self.parked_cars):
                self.parked_cars.append({"id": payload["car_id"], "car_id": payload["car_id"],
                                         "slot_id": slot_id, "x": slot["x"], "y": slot["y"],
                                         "heading": -90, "state": "parked", "origin": "preparked",
                                         "placement_id": placement_id})
                slot.update(state="occupied", car_id=payload["car_id"], reservation_id=None,
                            placement_id=placement_id)
                self._event(kind, message=payload["message"], car_id=payload["car_id"],
                            slot_id=slot_id, request_id=request_id, placement_id=placement_id)
        elif kind == "parked_removed":
            slot = next((s for s in self.slots if s["id"] == slot_id), None)
            car = next((c for c in self.parked_cars if c["slot_id"] == slot_id
                        and c["id"] == payload["car_id"]
                        and c.get("placement_id") == placement_id), None)
            if (slot is None or car is None or slot["state"] != "occupied"
                    or slot["car_id"] != payload["car_id"]
                    or slot.get("placement_id") != placement_id):
                return False
            self.parked_cars.remove(car)
            slot.update(state="free", car_id=None, reservation_id=None, placement_id=None)
            self._event(kind, message=payload["message"], car_id=payload["car_id"],
                        slot_id=slot_id, request_id=request_id, placement_id=placement_id)
        else:
            self._event(kind, message=payload["message"], car_id=payload["car_id"],
                        slot_id=slot_id, request_id=request_id, operation=operation,
                        placement_id=placement_id)
        pending.update(seq=payload["seq"], kind=kind)
        return True

    def flush_events(self):
        """A szüneteltetett modell kezelői eseményeit is naplózhatóvá teszi."""
        messages = [self._emit("events", event) for event in self._events]
        self._events.clear()
        return messages

    def _sensor(self, car):
        # Az első útszakasz keletre tart. A zebra akkor szabadul fel, amikor
        # a teljes jármű, a hátsó élt is beleértve, túljutott rajta.
        rear_x = car["x"] - CAR_HALF_LENGTH
        approaching = (car["_segment"] == 1 and car["state"] != "waiting"
                       and rear_x <= ZEBRA_X + ZEBRA_HALF_LENGTH + 1e-9)
        visible = approaching and ZEBRA_X - car["x"] <= self.config["sensor_range_m"]
        return {"valid": not self.sensor_failure, "range_m": self.config["sensor_range_m"],
                "pedestrian": bool(not self.sensor_failure and self.pedestrian and visible),
                "braking": car["state"] in ("pedestrian_braking", "pedestrian_stop"),
                "distance_to_stop_m": round(max(0.0, STOP_X - car["x"]), 3) if approaching else None}

    def _car_view(self, car):
        return deepcopy({key: value for key, value in car.items() if not key.startswith("_")}
                        | {"sensor": self._sensor(car)})

    def snapshot(self):
        slots = deepcopy(self.slots)
        crossing = {"active": self.pedestrian, "x": ZEBRA_X, "y": ZEBRA_Y,
                    "half_length_m": ZEBRA_HALF_LENGTH, "half_width_m": ZEBRA_HALF_WIDTH,
                    "stop_x": STOP_X}
        return {"run_id": self.run_id, "seq": self.seq, "sim_time": round(self.sim_time, 6),
                "cars": [self._car_view(c) for c in self.cars], "slots": slots,
                "parked_cars": deepcopy(self.parked_cars),
                "crossing": crossing,
                "metrics": {"completed": sum(c["state"] == "parked" for c in self.cars),
                            "total": len(self.cars), "preparked": len(self.parked_cars),
                            "pedestrian_stops": self.pedestrian_stops,
                            "distance_m": round(sum(c["distance_m"] for c in self.cars), 3)},
                "map": {"width": 64, "height": 38,
                        "road": [[8, 28], [56, 28], [56, 14], [14, 14]],
                        "slots": slots, "crossing": crossing.copy()}}

    def _advance(self, car, distance):
        """Megközelítés az igazodási pontig."""
        while distance > 1e-10 and car["_segment"] <= 3:
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

    def _travel(self, car, dt, limit, remaining=math.inf):
        """Trapézos lépés; a következő sebesség a maradék fékútba is belefér."""
        v, b = car["speed"], self.config["braking_mps2"]
        if math.isfinite(remaining):
            available = max(0.0, remaining - v * dt / 2)
            half = b * dt / 2
            discriminant = 2 * b * available
            limit = min(limit, discriminant / (math.sqrt(half * half + discriminant) + half))
        if limit < v and v <= b * dt and limit == 0:
            car["speed"] = 0.0
            return v * v / (2 * b)
        car["speed"] = max(v - b * dt, min(limit, v + self.config["acceleration_mps2"] * dt))
        return (v + car["speed"]) * dt / 2

    def _parking_result(self, car):
        return {k: car[k] for k in ("slot_id", "reservation_id", "request_id", "x", "y", "heading", "speed")} | {
            "stable_s": round(car["_stable_s"], 6)}

    def _move(self, car, dt):
        phase = car["_phase"]
        speed = self.config["speed_mps"]
        if phase == "approach":
            points = car["route"][car["_segment"]:4]
            remaining = math.dist((car["x"], car["y"]), points[0]) + sum(
                math.dist(a, b) for a, b in zip(points, points[1:])) if points else 0.0
            if self._sensor(car)["pedestrian"] or (car["_late"] and self.pedestrian):
                d = STOP_X - car["x"]
                braking_distance = car["speed"] ** 2 / (2 * self.config["braking_mps2"])
                if not car["_late"] and (d < -1e-7 or braking_distance > d + 1e-7):
                    car["_late"] = True
                    self._event("late_pedestrian", car, "Késői zebrajel: a megállási vonal már nem tartható.",
                                x=car["x"], speed=car["speed"], braking_distance_m=braking_distance)
                travel = self._travel(car, dt, speed, max(0.0, d))
                if not car["_late"]:
                    travel = min(travel, max(0.0, d))
                self._advance(car, travel)
                self._state(car, "pedestrian_braking" if car["speed"] > 0 else "pedestrian_stop",
                            "Fékezés a zebra miatt." if car["speed"] > 0 else "Gyalogos miatt áll.")
                return
            car["_late"] = False
            self._state(car, "driving", "Haladás a lefoglalt parkolóhely felé.")
            self._advance(car, min(remaining, self._travel(car, dt, speed, remaining)))
            if remaining < 1e-9:
                car["_segment"] = 4
            if car["_segment"] > 3 and car["speed"] <= 1e-8:
                car["_phase"] = "align"
                car["timing"]["maneuver_start_s"] = self.sim_time
            return
        target_x, target_y = car["route"][-1]
        if phase == "align":
            self._state(car, "aligning", "Igazodás a helyhez kis sebességű kanyarban.")
            length = math.pi * PARK_RADIUS / 2
            travel = min(length - car["_arc_m"], self._travel(car, dt, min(speed, PARK_SPEED)))
            car["_arc_m"] += travel
            angle = car["_arc_m"] / PARK_RADIUS
            car.update(x=target_x + PARK_RADIUS * (1 - math.sin(angle)),
                       y=11 + PARK_RADIUS * math.cos(angle),
                       heading=180 + math.degrees(angle), distance_m=car["distance_m"] + travel)
            if car["_arc_m"] >= length - 1e-9:
                car.update(x=target_x, y=11.0, heading=-90.0, _phase="maneuver")
        elif phase == "maneuver":
            self._state(car, "maneuvering", "Lassú beállás a parkolóhelyre.")
            remaining = max(0.0, car["y"] - target_y)
            travel = min(remaining, self._travel(car, dt, min(speed, PARK_SPEED), remaining))
            car.update(y=car["y"] - travel, distance_m=car["distance_m"] + travel)
            if remaining - travel <= 1e-7 and car["speed"] <= 1e-8:
                car["_phase"] = "verify"
        elif phase == "verify":
            slot = next(s for s in self.slots if s["id"] == car["slot_id"])
            self._state(car, "parking_check", "A véghelyzet és a megállás ellenőrzése.")
            if not valid_parked_pose(car, slot):
                car["_stable_s"] = 0
                self._state(car, "parking_error", "A véghelyzet nem felel meg a parkolási feltételeknek.")
                return
            car["_stable_s"] += dt
            if car["_stable_s"] + 1e-9 >= PARK_DWELL:
                car["_phase"] = "confirm"
                car["timing"]["pose_verified_s"] = self.sim_time
                car["_next_request"] = self.sim_time
        elif phase == "confirm":
            reconciling = car["_reports"] >= PARK_FAST_RETRIES
            self._state(car, "parking_reconciling" if reconciling else "parking_pending",
                        "A nyugta késik. A jármű áll, és újra egyezteti a foglalást." if reconciling
                        else "A koordinátor parkolási nyugtájára vár.")
            if self.sim_time >= car["_next_request"]:
                self._event("parking_report", car, "Ellenőrzött parkolási véghelyzet.",
                            **self._parking_result(car))
                car["_next_request"] = self.sim_time + (PARK_RECONCILE_S if reconciling else 0.5)
                if not reconciling:
                    car["_reports"] += 1

    def step(self, dt, connected=True):
        if not _number(dt) or not MIN_DT <= dt <= 0.1:
            raise ValueError(f"A lépésköz {MIN_DT} és 0.1 s között lehet")
        self.sim_time = round(self.sim_time + dt, 9)
        if self.active is None:
            self.active = next((i for i, c in enumerate(self.cars) if c["state"] == "waiting"), None)
            if self.active is not None:
                car = self.cars[self.active]
                # A várakozóhely -> sorvégi kijárat átadás külön nem modellezett,
                # diszkrét esemény; a mért út csak a (8,28) kijárattól indul.
                car.update(x=8.0, y=28.0, request_id=f"{self.run_id}:{car['id']}:parking")
                car["timing"]["dispatched_s"] = self.sim_time
                self._event("dispatched", car, "Átadás a sorból a gyártósori kijárathoz.")
        messages = []
        if self.active is not None:
            car = self.cars[self.active]
            position_before = car["x"], car["y"]
            if not connected or self.sensor_failure:
                car["speed"] = 0.0
                if car["_phase"] == "verify":
                    car["_stable_s"] = 0
                if car["slot_id"] is None:
                    car["timing"]["start_blocked_s"] = round(car["timing"].get("start_blocked_s", 0) + dt, 9)
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
            # Csak a teljesen álló modelllépések számítanak; a fékezés és
            # a stopvonalhoz még elmozduló utolsó lépés nem. Egy lépés egy ok.
            if (car["slot_id"] is not None and car["state"] in _STOP_REASONS and car["speed"] == 0
                    and (car["x"], car["y"]) == position_before):
                reason = _STOP_REASONS[car["state"]]
                car["stop_time_s"][reason] = round(car["stop_time_s"][reason] + dt, 9)
        messages.extend(self.flush_events())
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
        self._parking_setup = {}
        self._last_setup_seq = 0

    def snapshot(self):
        return {"run_id": self.run_id, "seq": self.seq, "sim_time": round(self.sim_time, 6),
                "slots": deepcopy(self.slots), "reservations": deepcopy(list(self.reservations.values()))}

    def handle(self, topic, payload):
        if not self._valid_envelope(payload):
            return []
        if topic == "requests/parking_setup":
            return self._handle_parking_setup(payload)
        if (not isinstance(payload.get("car_id"), str)
                or payload["car_id"] not in self._car_ids):
            return []
        car_id, request_id = payload["car_id"], payload.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 160:
            return []
        reservation = self.reservations.get(car_id)
        if topic == "events" and payload.get("type") == "parking_report":
            if (reservation is None
                    or payload["seq"] <= self._last_request.get(car_id, 0)
                    or any(payload.get(k) != reservation[k] for k in
                           ("request_id", "slot_id", "reservation_id"))):
                return []
            slot = next(s for s in self.slots if s["id"] == reservation["slot_id"])
            if (not valid_parked_pose(payload, slot) or not _number(payload.get("stable_s"))
                    or payload["stable_s"] < PARK_DWELL):
                return []
            reservation["state"] = slot["state"] = "occupied"
            self.sim_time = max(self.sim_time, payload["sim_time"])
            return [self._emit("commands/" + car_id, {
                "kind": "parking_confirmed", **reservation}), self._emit("coordinator", self.snapshot())]
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

    def _handle_parking_setup(self, payload):
        request_id, slot_id = payload.get("request_id"), payload.get("slot_id")
        operation = payload.get("operation", "spawn")
        placement_id = payload.get("placement_id", request_id if operation == "spawn" else None)
        if (not isinstance(request_id, str) or not request_id or len(request_id) > 160
                or not isinstance(slot_id, str) or re.fullmatch(r"P[0-9]{2}", slot_id) is None
                or payload.get("car_id") != "PARK-" + slot_id
                or operation not in ("spawn", "remove")
                or not isinstance(placement_id, str) or not placement_id or len(placement_id) > 160
                or (operation == "spawn" and placement_id != request_id)):
            return []
        self.sim_time = max(self.sim_time, payload["sim_time"])
        result = {"kind": "parking_setup_rejected", "request_id": request_id,
                  "slot_id": slot_id, "car_id": "PARK-" + slot_id,
                  "operation": operation, "placement_id": placement_id}
        existing = self._parking_setup.get(request_id)
        if existing is not None:
            result = (existing if all(existing[key] == result[key] for key in
                                     ("slot_id", "operation", "placement_id")) else
                      result | {"message": "A kérésazonosító már másik parkolóbeállításhoz tartozik."})
        else:
            slot = next((s for s in self.slots if s["id"] == slot_id), None)
            if payload["seq"] <= self._last_setup_seq:
                result["message"] = "Elavult parkolóbeállítási kérés."
            elif len(self._parking_setup) >= 256:
                result["message"] = "A futás kéréskerete betelt; indíts új futást."
            elif slot is None:
                result["message"] = "Ismeretlen parkolóhely."
            elif operation == "remove":
                if (slot["state"] == "occupied" and slot["car_id"] == result["car_id"]
                        and slot.get("placement_id") == placement_id):
                    slot.update(state="free", car_id=None, reservation_id=None, placement_id=None)
                    result.update(kind="parked_removed", message="A kézzel elhelyezett jármű eltávolítva; a hely felszabadult.")
                else:
                    result["message"] = "Csak a megadott, kézzel elhelyezett autó távolítható el."
            elif slot["state"] != "free":
                result["message"] = "A parkolóhely foglalt vagy már le van foglalva."
            else:
                slot.update(state="occupied", car_id=result["car_id"], reservation_id=None,
                            placement_id=placement_id)
                result.update(kind="parked_spawned", x=slot["x"], y=slot["y"], heading=-90,
                              message="A már parkoló jármű bekerült a foglaltsági nyilvántartásba.")
            self._last_setup_seq = max(self._last_setup_seq, payload["seq"])
            if len(self._parking_setup) < 256:
                self._parking_setup[request_id] = deepcopy(result)
        return [self._emit("commands/parking_setup", result),
                self._emit("coordinator", self.snapshot())]
