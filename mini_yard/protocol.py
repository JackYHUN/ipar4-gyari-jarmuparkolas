"""A helyi JSON-adatút beolvasási szabályai."""
import json
import re

from .model import _number


VEHICLE_STATES = frozenset({
    "waiting", "requesting", "driving", "pedestrian_braking", "pedestrian_stop",
    "communication_stop", "sensor_stop", "aligning", "maneuvering", "parking_check",
    "parking_pending", "parking_reconciling", "parking_error", "parked",
})


def strict_json(raw):
    def reject_constant(value):
        raise ValueError(f"Nem véges JSON-érték: {value}")

    def finite_float(value):
        number = float(value)
        if not _number(number):
            return reject_constant(value)
        return number

    return json.loads(raw, parse_constant=reject_constant, parse_float=finite_float)


def producer_for(topic):
    if topic in ("coordinator", "service/heartbeat") or topic.startswith("commands/"):
        return "parking"
    if topic in ("events", "world", "requests/parking", "requests/parking_setup") or topic.startswith("telemetry/"):
        return "vehicle"
    return None


def valid_message(topic, p, run_id):
    if not (isinstance(p, dict) and p.get("run_id") == run_id
            and type(p.get("seq")) is int and (0 if topic in ("world", "coordinator") else 1) <= p["seq"] < 2**63
            and _number(p.get("sim_time")) and p["sim_time"] >= 0):
        return False
    # A Python JSON-olvasó a túl nagy exponenseket végtelenné alakíthatja.
    # Az exportba csak szabványos JSON kerülhet, a nem használt mezőkben is.
    json.dumps(p, ensure_ascii=False, allow_nan=False).encode("utf-8")

    def strings(*keys):
        return all(isinstance(p.get(k), str) and 0 < len(p[k]) <= 256 for k in keys)

    def objects(key):
        return isinstance(p.get(key), list) and all(isinstance(x, dict) for x in p[key])

    def points(value):
        return isinstance(value, list) and all(isinstance(point, list) and len(point) == 2
                                               and all(map(_number, point)) for point in value)

    if topic == "events":
        return (strings("type", "message") and (p.get("car_id") is None or strings("car_id"))
                and (p["type"] != "parking_report" or
                     strings("car_id", "request_id", "slot_id", "reservation_id")
                     and all(_number(p.get(k)) for k in ("x", "y", "heading", "speed", "stable_s"))))
    if topic in ("requests/parking", "requests/parking_setup"):
        return strings("car_id", "request_id") and (topic == "requests/parking" or strings("slot_id"))
    if topic.startswith("commands/"):
        return strings("kind", "car_id", "request_id") and (
            topic == "commands/parking_setup" and strings("slot_id", "message")
            or topic == f"commands/{p['car_id']}" and re.fullmatch(r"CAR0[1-6]", p["car_id"]) is not None)
    if topic.startswith("telemetry/"):
        return (strings("car_id", "state", "reason") and topic == f"telemetry/{p['car_id']}"
                and re.fullmatch(r"CAR0[1-6]", p["car_id"]) is not None and p["state"] in VEHICLE_STATES
                and all(_number(p.get(k)) for k in ("x", "y", "speed", "distance_m", "heading"))
                and isinstance(p.get("sensor"), dict) and isinstance(p.get("stop_time_s"), dict))
    if topic == "world":
        return (objects("cars") and objects("slots") and objects("parked_cars")
                and all(isinstance(p.get(k), dict) for k in ("metrics", "map", "crossing"))
                and all(isinstance(c.get("id"), str) and isinstance(c.get("state"), str)
                        and c["state"] in VEHICLE_STATES and points(c.get("route"))
                        for c in p["cars"])
                and points(p["map"].get("road")) and isinstance(p["map"].get("slots"), list)
                and all(isinstance(slot, dict) for slot in p["map"]["slots"]))
    if topic == "coordinator":
        return objects("slots") and objects("reservations")
    return topic == "service/heartbeat"
