"""Valódi loopback MQTT- és HTTP-integráció. Futtatás: python -m tests.integration.

Külön, ideiglenes alkalmazáspéldányt indít szabad portokon. Nem vezérli a felhasználó
8766-os demonstrációját. Siker esetén mért ellenőrzési bizonylatot és nyers naplókat
ment a results mappába; nem végez statisztikai kampányt.
"""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt

from mini_yard.broker import LocalBroker
from mini_yard.model import DEFAULT_CONFIG
from mini_yard.runtime import Application
from mini_yard.server import make_server


ROOT = Path(__file__).resolve().parents[1]
CHECKS = []
RUNS = []
SESSION = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
EVIDENCE = ROOT / "results" / "integration" / SESSION


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = list((ROOT / "mini_yard").glob("*.py"))
    paths += list((ROOT / "web").glob("*"))
    paths += [ROOT / "config.json", ROOT / "tests" / "integration.py"]
    return {p.relative_to(ROOT).as_posix(): digest(p)
            for p in sorted(paths) if p.is_file()}


class RealMqttHttpIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="mini-yard-integration-")
        cls.root = Path(cls.temporary.name)
        cls.test_config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        shutil.copytree(ROOT / "web", cls.root / "web")
        cls.app = Application(cls.test_config, cls.root,
                              mqtt_port=free_port(), initially_paused=True)
        cls.server = None
        try:
            cls.app.start()
            cls.server = make_server(cls.app, 0)
            cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
            cls.server_thread.start()
            cls.base = f"http://127.0.0.1:{cls.server.server_port}"
            cls.used = False
        except Exception:
            cls.app.close()
            cls.temporary.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()
            cls.server_thread.join(timeout=3)
        cls.app.close()
        if cls.app.broker.thread.is_alive() or cls.app.broker.error:
            raise AssertionError("Az MQTT-közvetítő nem állt le hibamentesen")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", cls.app.mqtt_port)) == 0:
                raise AssertionError("A lezárt MQTT-közvetítő portja még fogad kapcsolatot")
        CHECKS.append({"name": "broker_shutdown_finishes_and_closes_listener",
                       "test": "tearDownClass", "checked_utc": utc_now(), "passed": True,
                       "observed": {"broker_thread_alive": False, "broker_error": None,
                                    "mqtt_listener_accepts_connections": False},
                       "expected": "közvetítőszál leállt; nincs hiba és nincs nyitva maradt MQTT-listener"})
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        raw = cls.root / "results"
        if raw.exists():
            shutil.copytree(raw, EVIDENCE / "runs", dirs_exist_ok=True)
        cls.temporary.cleanup()

    def setUp(self):
        if self.__class__.used:
            old = self.state().get("run_id")
            self.control("reset")
            self.wait_for(lambda s: s.get("run_id") != old and bool(s.get("world")),
                          "új, MQTT-n megfigyelt futás", timeout=8)
        self.__class__.used = True
        self.wait_for(lambda s: s.get("ready") and s.get("connected"),
                      "MQTT-kapcsolat és kezdeti világállapot", timeout=6)
        RUNS.append({"test": self._testMethodName, "run_id": self.state()["run_id"]})

    def request(self, path, data=None, headers=None):
        body = None if data is None else json.dumps(data).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = Request(self.base + path, data=body, headers=request_headers)
        with urlopen(request, timeout=4) as response:
            return response.status, dict(response.headers), response.read()

    def state(self):
        return json.loads(self.request("/api/state")[2])

    def control(self, action, **data):
        status, _, body = self.request("/api/control", dict(action=action, **data))
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)["queued"])

    @staticmethod
    def car(state, car_id="CAR01"):
        return next((c for c in state.get("world", {}).get("cars", [])
                     if c["car_id"] == car_id), {})

    def wait_for(self, predicate, description, timeout=5):
        deadline = time.monotonic() + timeout
        last = {}
        while time.monotonic() < deadline:
            last = self.state()
            self.assertFalse(last.get("error"), last.get("error"))
            if predicate(last):
                return last
            time.sleep(0.04)
        summary = {"run_id": last.get("run_id"), "sim_time": last.get("sim_time"),
                   "cars": [(c["car_id"], c["state"], c["x"], c["speed"])
                            for c in last.get("world", {}).get("cars", [])],
                   "mqtt": last.get("mqtt"), "controls": last.get("controls")}
        self.fail(f"Időtúllépés: {description}; {summary}")

    def check(self, name, observed, expected):
        CHECKS.append({"name": name, "test": self._testMethodName,
                       "checked_utc": utc_now(), "observed": observed,
                       "expected": expected, "passed": True})

    def spawn(self, slot_id, request_id=None):
        before = self.state()
        data = {"action": "spawn_parked", "slot_id": slot_id, "run_id": before["run_id"]}
        if request_id is not None:
            data["request_id"] = request_id
        status, _, body = self.request("/api/control", data)
        returned = json.loads(body)
        self.assertEqual(status, 202)
        self.assertTrue(returned["queued"])
        if request_id is not None:
            self.assertEqual(returned["request_id"], request_id)
        old_ack_seq = (before.get("parking_setup") or {}).get("seq", 0)
        return self.wait_for(lambda s: (s.get("parking_setup") or {}).get("request_id") == returned["request_id"]
                             and s["parking_setup"]["seq"] > old_ack_seq,
                             "parkolóbeállítás MQTT-visszaigazolása")

    def remove_parked(self, slot_id, placement_id, request_id):
        before = self.state()
        status, _, body = self.request("/api/control", {
            "action": "remove_parked", "slot_id": slot_id, "placement_id": placement_id,
            "request_id": request_id, "run_id": before["run_id"]})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)["request_id"], request_id)
        old_seq = (before.get("parking_setup") or {}).get("seq", 0)
        return self.wait_for(lambda s: (s.get("parking_setup") or {}).get("request_id") == request_id
                             and s["parking_setup"]["seq"] > old_seq,
                             "eltávolítás MQTT-visszaigazolása")

    @staticmethod
    def slot(state, slot_id):
        return next((slot for slot in state.get("coordinator", {}).get("slots", [])
                     if slot["id"] == slot_id), {})

    def test_01_nominal_three_cars_exports_and_authorized_repeat(self):
        started = time.monotonic()
        self.control("speed", value=4)
        self.control("resume")
        final = self.wait_for(lambda s: s["world"]["metrics"]["completed"] == 3,
                              "három autó tényleges MQTT-futásban leparkol", timeout=38)
        final = self.wait_for(lambda s: len([r for r in s.get("coordinator", {}).get("slots", [])
                                            if r["state"] == "occupied"]) == 3,
                              "három parkolási esemény koordinátori feldolgozása")
        self.control("pause")
        final = self.wait_for(lambda s: s["paused"], "szüneteltetés")
        cars = final["world"]["cars"]
        self.assertEqual(len({c["slot_id"] for c in cars}), 3)
        self.assertTrue(all(c["state"] == "parked" and c["speed"] == 0 for c in cars))
        self.check("nominal_three_parked_unique_slots", {
            "sim_time_s": final["sim_time"], "elapsed_wall_s": round(time.monotonic() - started, 3),
            "assignments": {c["car_id"]: c["slot_id"] for c in cars}}, "3 parkolt autó, 3 külön hely")

        status, headers, raw = self.request("/api/export?kind=messages")
        records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        topics = {r["relative_topic"] for r in records}
        for car in cars:
            requests = [r for r in records if r["relative_topic"] == "requests/parking"
                        and r["payload"].get("car_id") == car["car_id"]]
            grants = [r for r in records if r["relative_topic"] == "commands/" + car["car_id"]
                      and r["payload"].get("kind") == "parking_grant"]
            self.assertTrue(requests)
            self.assertTrue(grants)
            self.assertEqual(requests[-1]["payload"]["request_id"], grants[-1]["payload"]["request_id"])
        self.assertIn("service/heartbeat", topics)
        self.assertIn("world", topics)
        self.check("mqtt_requests_grants_and_observer_log", {"records": len(records), "topics": sorted(topics)},
                   "minden autóhoz fogadott kérés és válasz; szívverés és világállapot")

        request_payload = next(r["payload"] for r in reversed(records)
                               if r["relative_topic"] == "requests/parking"
                               and r["payload"].get("car_id") == "CAR01")
        prefix = f"mini-yard/{final['run_id']}/"
        responses, subscribed = queue.Queue(), threading.Event()
        auditor = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                              client_id="mini-integration-audit-" + final["run_id"])
        auditor.on_connect = lambda client, *_: client.subscribe(prefix + "commands/CAR01", 1)
        auditor.on_subscribe = lambda *_: subscribed.set()
        auditor.on_message = lambda _c, _u, message: responses.put(json.loads(message.payload))
        try:
            auditor.connect("127.0.0.1", self.app.mqtt_port, keepalive=10)
            auditor.loop_start()
            self.assertTrue(subscribed.wait(4), "független MQTT-feliratkozó nem indult")
            self.assertTrue(self.app.vehicle_bus.publish("requests/parking", request_payload))
            reply = responses.get(timeout=4)
            self.assertEqual(reply["kind"], "parking_grant")
            self.assertEqual(reply["request_id"], request_payload["request_id"])
            self.assertEqual(reply["slot_id"], self.car(final)["slot_id"])
            reservations = self.state()["coordinator"]["reservations"]
            self.assertEqual(len(reservations), 3)
            self.check("authenticated_mqtt_duplicate_request_idempotent", {
                "car_id": reply["car_id"], "slot_id": reply["slot_id"],
                "reservation_id": reply["reservation_id"], "reservation_count": len(reservations)},
                "a hitelesített ismételt kérésre ugyanaz a foglalás; a választ független kliens figyeli")
        finally:
            auditor.disconnect()
            auditor.loop_stop()

        _, _, csv_body = self.request("/api/export?kind=telemetry")
        rows = list(csv.DictReader(io.StringIO(csv_body.decode("utf-8-sig"))))
        self.assertTrue(rows)
        self.assertEqual({r["car_id"] for r in rows}, {"CAR01", "CAR02", "CAR03"})
        self.assertTrue(all(r["run_id"] == final["run_id"] for r in rows))
        self.assertIn("speed_mps", rows[0])
        for column in ("stop_pedestrian_s", "stop_sensor_failure_s", "stop_communication_s"):
            self.assertTrue(all(float(row[column]) >= 0 for row in rows))
        _, _, events_body = self.request("/api/export?kind=events")
        events = json.loads(events_body)
        parked = [r for r in events if r["payload"].get("type") == "parked"]
        self.assertEqual(len(parked), 3)
        self.check("csv_events_exports", {"csv_rows": len(rows), "parked_events": len(parked)},
                   "feldolgozható CSV 3 autóval és 3 parkolási esemény")

    def test_02_pedestrian_stop_and_release(self):
        self.control("pedestrian", active=True)
        self.control("speed", value=4)
        self.control("resume")
        stopped = self.wait_for(lambda s: self.car(s).get("state") == "pedestrian_stop",
                                "zebra előtti megállás", timeout=7)
        car = self.car(stopped)
        self.assertLessEqual(car["x"], 25 + 1e-8)
        self.assertEqual(car["speed"], 0)
        self.assertTrue(car["sensor"]["pedestrian"])
        position, until = (car["x"], car["y"]), stopped["sim_time"] + 1
        held = self.wait_for(lambda s: s["sim_time"] >= until, "zebra előtti tartás")
        self.assertEqual((self.car(held)["x"], self.car(held)["y"]), position)
        duration = held["sim_time"] - stopped["sim_time"]
        counted = self.car(held)["stop_time_s"]["pedestrian"] - car["stop_time_s"]["pedestrian"]
        self.assertAlmostEqual(counted, duration, places=6)
        _, _, csv_body = self.request("/api/export?kind=telemetry")
        rows = list(csv.DictReader(io.StringIO(csv_body.decode("utf-8-sig"))))
        exported = max(float(row["stop_pedestrian_s"]) for row in rows if row["car_id"] == car["id"])
        self.assertGreaterEqual(exported, self.car(held)["stop_time_s"]["pedestrian"])
        self.check("pedestrian_stop_time_reaches_mqtt_state_and_csv", {
            "held_model_s": round(duration, 6), "counted_s": round(counted, 6),
            "csv_max_pedestrian_s": exported},
            "állva töltött modellidő egyezik a fogadott számláló növekményével; CSV-ben is szerepel")
        self.check("pedestrian_stop_before_line_and_hold", {"x_m": car["x"], "speed_mps": car["speed"],
                   "held_for_sim_s": round(held["sim_time"] - stopped["sim_time"], 3)},
                   "x <= 25 m, sebesség 0; gyalogos mellett helyben marad")
        self.control("pedestrian", active=False)
        moving = self.wait_for(lambda s: self.car(s).get("state") == "driving"
                               and self.car(s).get("speed", 0) > 0, "gyalogos távozása után indul")
        self.check("pedestrian_release_resumes", {"state": self.car(moving)["state"],
                   "speed_mps": self.car(moving)["speed"]}, "driving, pozitív sebesség")

    def test_03_sensor_failure_hold_and_release(self):
        self.control("resume")
        self.wait_for(lambda s: self.car(s).get("speed", 0) > 0.3, "induló jármű")
        self.control("sensor_failure", active=True)
        stopped = self.wait_for(lambda s: self.car(s).get("state") == "sensor_stop",
                                "érzékelőhiba miatti megállás")
        car = self.car(stopped)
        self.assertFalse(car["sensor"]["valid"])
        self.assertEqual(car["speed"], 0)
        position, until = (car["x"], car["y"]), stopped["sim_time"] + 0.5
        held = self.wait_for(lambda s: s["sim_time"] >= until, "érzékelőhibás tartás")
        self.assertEqual((self.car(held)["x"], self.car(held)["y"]), position)
        duration = held["sim_time"] - stopped["sim_time"]
        counted = self.car(held)["stop_time_s"]["sensor_failure"] - car["stop_time_s"]["sensor_failure"]
        self.assertAlmostEqual(counted, duration, places=6)
        self.check("sensor_stop_time_uses_model_seconds", {
            "held_model_s": round(duration, 6), "counted_s": round(counted, 6)},
            "érzékelőhiba miatti álló modellidő egyezik a számláló növekményével")
        self.check("invalid_sensor_stops_and_holds", {"sensor_valid": car["sensor"]["valid"],
                   "speed_mps": car["speed"], "held_for_sim_s": round(held["sim_time"] - stopped["sim_time"], 3)},
                   "érvénytelen jel, 0 m/s, nincs elmozdulás")
        self.control("sensor_failure", active=False)
        moving = self.wait_for(lambda s: self.car(s).get("state") == "driving"
                               and self.car(s).get("speed", 0) > 0, "érzékelő helyreállítása utáni indulás")
        self.assertTrue(self.car(moving)["sensor"]["valid"])
        self.check("sensor_recovery_resumes", {"state": self.car(moving)["state"], "valid": True},
                   "érvényes jel mellett újra halad")

    def test_04_real_clock_heartbeat_failure(self):
        self.control("resume")
        self.wait_for(lambda s: self.car(s).get("speed", 0) > 0.3, "induló jármű")
        started = time.monotonic()
        self.control("link_failure", active=True)
        stopped = self.wait_for(lambda s: self.car(s).get("state") == "communication_stop",
                                "valós órás szívverés-felügyelet", timeout=4)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(stopped["mqtt"]["heartbeat_age_s"], DEFAULT_CONFIG["heartbeat_timeout_s"])
        self.assertEqual(self.car(stopped)["speed"], 0)
        position = (self.car(stopped)["x"], self.car(stopped)["y"])
        count = stopped["mqtt"]["received"]
        held = self.wait_for(lambda s: s["sim_time"] >= stopped["sim_time"] + 0.5,
                             "kommunikációs hibás tartás")
        self.assertEqual((self.car(held)["x"], self.car(held)["y"]), position)
        self.assertGreater(held["mqtt"]["received"], count)
        duration = held["sim_time"] - stopped["sim_time"]
        counted = (self.car(held)["stop_time_s"]["communication"]
                   - self.car(stopped)["stop_time_s"]["communication"])
        self.assertAlmostEqual(counted, duration, places=6)
        self.check("communication_stop_time_uses_model_seconds", {
            "held_model_s": round(duration, 6), "counted_s": round(counted, 6)},
            "szívveréshiány miatti álló modellidő egyezik a számláló növekményével")
        self.check("heartbeat_watchdog_stops_with_telemetry_retained", {
            "elapsed_wall_s": round(elapsed, 3), "heartbeat_age_s": stopped["mqtt"]["heartbeat_age_s"],
            "state": self.car(stopped)["state"], "observer_received_growth": held["mqtt"]["received"] - count},
            "szívverés kora >= 1,0 valós s; autó áll; megfigyelési forgalom megmarad")
        self.control("link_failure", active=False)
        restored = self.wait_for(lambda s: s["connected"] and self.car(s).get("state") == "driving"
                                 and self.car(s).get("speed", 0) > 0, "szívverés utáni újraindulás")
        self.check("heartbeat_recovery_resumes", {"connected": restored["connected"],
                   "state": self.car(restored)["state"]}, "kapcsolat helyreáll; autó halad")

    def test_05_reset_retains_previous_log_and_rejects_bad_controls(self):
        before = self.state()
        old_path = self.root / before["log_file"]
        self.assertTrue(old_path.is_file())
        self.control("reset")
        reset = self.wait_for(lambda s: s.get("run_id") != before["run_id"] and bool(s.get("world")),
                              "eltérő azonosítójú új futás", timeout=8)
        self.assertTrue(reset["paused"])
        self.assertEqual(reset["sim_time"], 0)
        self.assertTrue(all(sum(c["stop_time_s"].values()) == 0 for c in reset["world"]["cars"]))
        self.assertTrue(old_path.is_file())
        self.assertGreater(old_path.stat().st_size, 0)
        self.assertNotEqual(reset["log_file"], before["log_file"])
        self.check("reset_new_id_preserves_old_log", {"old_run_id": before["run_id"],
                   "new_run_id": reset["run_id"], "old_log_bytes": old_path.stat().st_size,
                   "new_sim_time_s": reset["sim_time"], "paused": reset["paused"]},
                   "új futásazonosító, szünet, 0 modellidő; régi napló megmarad")
        invalid = [{"action": "unknown"}, {"action": "speed", "value": 3},
                   {"action": "speed", "value": True}, {"action": "pedestrian", "active": "yes"}]
        for data in invalid:
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/control", data)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/control", {"action": "resume"}, {"Origin": "http://foreign.invalid"})
        self.assertEqual(caught.exception.code, 403)
        self.check("illegal_controls_and_foreign_origin_rejected", {
            "invalid_control_count": len(invalid), "invalid_status": 400, "foreign_origin_status": 403},
            "hibás vezérlések 400, eltérő Origin 403")

    def test_06_preparked_setup_paused_duplicate_and_moving_car_skip(self):
        self.assertTrue(self.state()["paused"])
        reply = self.spawn("P01", "paused-placement")
        self.assertEqual(reply["parking_setup"]["kind"], "parked_spawned")
        placed = self.wait_for(lambda s: len(s["world"].get("parked_cars", [])) == 1
                               and s.get("coordinator", {}).get("slots", [{}])[0].get("car_id") == "PARK-P01",
                               "már parkoló autó megjelenik mindkét MQTT-állapotban szünet alatt")
        self.assertEqual(placed["sim_time"], 0)
        self.assertEqual(placed["world"]["metrics"]["completed"], 0)
        self.assertEqual(placed["world"]["metrics"]["total"], 3)
        self.assertEqual(placed["world"]["metrics"]["preparked"], 1)
        self.assertEqual(placed["coordinator"]["reservations"], [])
        repeated = self.spawn("P01", "paused-placement")
        self.assertEqual(repeated["parking_setup"]["kind"], "parked_spawned")
        self.assertEqual(len(repeated["world"]["parked_cars"]), 1)
        self.check("preparked_paused_and_idempotent_separate_from_fleet", {
            "sim_time": placed["sim_time"], "car": placed["world"]["parked_cars"][0],
            "completed": placed["world"]["metrics"]["completed"],
            "total": placed["world"]["metrics"]["total"]},
            "szünetben egyetlen PARK-P01; 0/3 teljesítés, külön statikus autó")
        self.wait_for(lambda s: any(e.get("type") == "parked_spawned" for e in s["events"]),
                      "kezelői parkolási esemény naplózása szünetben")
        records = [json.loads(line) for line in self.request("/api/export?kind=messages")[2].decode().splitlines()]
        for topic in ("operator/control", "requests/parking_setup", "commands/parking_setup", "events"):
            self.assertTrue(any(r["relative_topic"] == topic
                                and r["payload"].get("request_id") == "paused-placement" for r in records))
        self.check("preparked_request_ack_event_and_control_in_mqtt_export", {
            "request_id": "paused-placement", "matching_records": sum(
                r["payload"].get("request_id") == "paused-placement" for r in records)},
            "HTTP-kezelés, valódi MQTT-kérés, koordinátori válasz és szemantikus esemény exportálva")
        self.control("resume")
        moving = self.wait_for(lambda s: self.car(s).get("slot_id") == "P02"
                               and self.car(s).get("speed", 0) > 0, "CAR01 átugorja a foglalt P01 helyet")
        self.control("pause")
        self.wait_for(lambda s: s["paused"], "szüneteltetés a foglalás után")
        for slot_id in ("P01", "P02", "P99"):
            rejected = self.spawn(slot_id, "reject-" + slot_id)
            self.assertEqual(rejected["parking_setup"]["kind"], "parking_setup_rejected")
        self.assertEqual(self.car(self.state())["slot_id"], "P02")
        self.assertEqual(len(self.state()["world"]["parked_cars"]), 1)
        self.check("preparked_slot_skipped_and_nonfree_unknown_setup_rejected", {
            "moving_car_slot": self.car(moving)["slot_id"], "rejected_slots": ["P01", "P02", "P99"]},
            "CAR01 P02 helyet kap; foglalt, lefoglalt és ismeretlen helyre nincs elhelyezés")

    def test_07_preparked_reset_and_invalid_identity_controls(self):
        self.control("link_failure", active=True)
        self.wait_for(lambda s: s["controls"]["link_failure"] and not s["connected"],
                      "életjel/parancs hibainjektálás szüneteltetett modellben")
        placed = self.spawn("P08")
        self.assertEqual(placed["parking_setup"]["kind"], "parked_spawned")
        self.assertTrue(placed["parking_setup"]["request_id"])
        placed = self.wait_for(lambda s: bool(s["world"].get("parked_cars"))
                               and any(slot["id"] == "P08" and slot["car_id"] == "PARK-P08"
                                       for slot in s["coordinator"]["slots"]),
                               "P08 kezdeti foglaltság mindkét megfigyelt állapotban hibainjektálás alatt")
        self.check("operator_setup_remains_consistent_during_vehicle_link_fault", {
            "connected": placed["connected"], "paused": placed["paused"],
            "car_id": placed["world"]["parked_cars"][0]["car_id"]},
            "a menedzsmentút hibainjektálás mellett is végigviszi a parkolóbeállítást")
        invalid = [{"action": "spawn_parked", "slot_id": "P1"},
                   {"action": "spawn_parked", "slot_id": []},
                   {"action": "spawn_parked", "slot_id": "P01", "request_id": ""},
                   {"action": "spawn_parked", "slot_id": "P01", "request_id": []},
                   {"action": "spawn_parked", "slot_id": "P01", "run_id": "old-run"},
                   {"action": "spawn_parked", "slot_id": "P01", "request_id": placed["parking_setup"]["request_id"]}]
        for data in invalid:
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/control", data)
            self.assertEqual(caught.exception.code, 400)
        old_run = self.state()["run_id"]
        self.control("reset")
        fresh = self.wait_for(lambda s: s["run_id"] != old_run and bool(s.get("world"))
                              and bool(s.get("coordinator")), "reset utáni tiszta MQTT-állapot", timeout=8)
        self.assertEqual(fresh["world"]["parked_cars"], [])
        self.assertIsNone(fresh["parking_setup"])
        self.assertTrue(all(slot["state"] == "free" for slot in fresh["coordinator"]["slots"]))
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/control", {"action": "spawn_parked", "slot_id": "P01", "run_id": old_run})
        self.assertEqual(caught.exception.code, 400)
        self.check("preparked_reset_clears_setup_and_bad_identity_rejected", {
            "invalid_controls": len(invalid) + 1, "status": 400,
            "new_preparked_count": len(fresh["world"]["parked_cars"]), "new_ack": fresh["parking_setup"]},
            "reset üríti a statikus autókat és a visszaigazolást; hibás vagy előző futáshoz tartozó kérés elutasítva")

    def test_08_remove_paused_replay_and_placement_identity(self):
        self.spawn("P01", "placement-first")
        self.wait_for(lambda s: any(c.get("placement_id") == "placement-first"
                                   for c in s["world"]["parked_cars"]), "elhelyezésazonosító megfigyelése")
        self.control("link_failure", active=True)
        self.wait_for(lambda s: s["controls"]["link_failure"], "kezelői út külön hibamódban")
        removed = self.remove_parked("P01", "placement-first", "remove-first")
        self.assertEqual(removed["parking_setup"]["kind"], "parked_removed")
        empty = self.wait_for(lambda s: not s["world"]["parked_cars"]
                             and self.slot(s, "P01").get("state") == "free",
                             "eltávolítás a világban és a koordinátornál szünetben")
        self.assertTrue(empty["paused"])
        self.assertEqual(empty["sim_time"], 0)
        self.assertEqual(empty["world"]["metrics"]["completed"], 0)
        self.check("manual_removal_paused_works_during_vehicle_link_fault", {
            "paused": empty["paused"], "sim_time": empty["sim_time"],
            "static_cars": len(empty["world"]["parked_cars"]),
            "slot_state": self.slot(empty, "P01")["state"]},
            "MQTT-s eltávolítás szünet és jármű-életjelhiba mellett is konzisztens")

        self.remove_parked("P01", "placement-first", "remove-first")
        self.spawn("P01", "placement-second")
        self.wait_for(lambda s: any(c.get("placement_id") == "placement-second"
                                   for c in s["world"]["parked_cars"])
                      and self.slot(s, "P01").get("placement_id") == "placement-second",
                      "második elhelyezés ugyanazon a helyen")
        self.spawn("P01", "placement-first")
        self.remove_parked("P01", "placement-first", "remove-first")
        rejected = self.remove_parked("P01", "placement-first", "stale-placement-removal")
        self.assertEqual(rejected["parking_setup"]["kind"], "parking_setup_rejected")
        current = self.wait_for(lambda s: len(s["world"]["parked_cars"]) == 1
                                and s["world"]["parked_cars"][0].get("placement_id") == "placement-second"
                                and self.slot(s, "P01").get("placement_id") == "placement-second",
                                "régi elhelyezés és eltávolítás nem érinti az új autót")
        self.assertEqual(current["world"]["metrics"]["completed"], 0)
        self.check("replayed_setup_requests_cannot_resurrect_or_remove_new_placement", {
            "current_placement_id": current["world"]["parked_cars"][0]["placement_id"],
            "stale_removal_kind": rejected["parking_setup"]["kind"]},
            "azonos hely új autóját sem régi kérés, sem régi elhelyezésazonosító nem módosítja")
        removed = self.remove_parked("P01", "placement-second", "remove-second")
        self.assertEqual(removed["parking_setup"]["kind"], "parked_removed")
        self.wait_for(lambda s: not s["world"]["parked_cars"], "második autó eltávolítása")
        self.wait_for(lambda s: len([e for e in s["events"] if e.get("type") == "parked_removed"]) == 2,
                      "két tényleges eltávolítás eseménye, ismétlés nélkül")
        records = [json.loads(line) for line in self.request("/api/export?kind=messages")[2].decode().splitlines()]
        topics = {r["relative_topic"] for r in records
                  if r["payload"].get("request_id") == "remove-second"}
        self.assertTrue({"operator/control", "requests/parking_setup", "commands/parking_setup", "events"} <= topics)
        self.check("manual_removal_control_mqtt_ack_and_event_are_exported", {
            "request_id": "remove-second", "topics": sorted(topics)},
            "kezelői kérés, MQTT-kérés, válasz és eltávolítási esemény visszakereshető")

    def test_09_removal_releases_capacity_for_waiting_car_without_reset(self):
        for index in range(1, 9):
            slot_id = f"P{index:02d}"
            self.spawn(slot_id, "full-" + slot_id)
        full = self.wait_for(lambda s: len(s["world"]["parked_cars"]) == 8
                             and all(slot["state"] == "occupied" for slot in s["coordinator"]["slots"]),
                             "teljes kézi foglaltság")
        run_id = full["run_id"]
        self.control("speed", value=4)
        self.control("resume")
        self.wait_for(lambda s: self.car(s).get("state") == "requesting"
                      and self.car(s).get("slot_id") is None, "autó kapacitásra vár")
        self.remove_parked("P03", "full-P03", "release-capacity")
        moving = self.wait_for(lambda s: self.car(s).get("slot_id") == "P03"
                               and self.car(s).get("speed", 0) > 0
                               and len(s["world"]["parked_cars"]) == 7
                               and self.slot(s, "P03").get("car_id") == "CAR01",
                               "várakozó autó a felszabadított helyre indul", timeout=6)
        self.assertEqual(moving["run_id"], run_id)
        self.assertEqual(moving["world"]["metrics"]["completed"], 0)
        self.control("pause")
        self.wait_for(lambda s: s["paused"], "megállított teszt a mozgó autó helyfoglalásával")
        refused = self.remove_parked("P03", "full-P03", "cannot-remove-fleet-car")
        self.assertEqual(refused["parking_setup"]["kind"], "parking_setup_rejected")
        self.assertEqual(self.slot(self.state(), "P03")["car_id"], "CAR01")
        self.check("full_capacity_manual_removal_resumes_waiting_car_without_reset", {
            "run_id": run_id, "moving_car_slot": self.car(moving)["slot_id"],
            "remaining_static_cars": len(moving["world"]["parked_cars"]),
            "central_owner": self.slot(moving, "P03")["car_id"],
            "fleet_removal_kind": refused["parking_setup"]["kind"]},
            "futásazonosító változatlan; CAR01 P03-ra indul; a kiosztott flottajármű nem törölhető")

    def test_10_old_run_controls_and_invalid_removal_are_rejected(self):
        before = self.state()
        controls = [{"action": "pause"}, {"action": "resume"}, {"action": "reset"},
                    {"action": "speed", "value": 4}, {"action": "pedestrian", "active": True},
                    {"action": "sensor_failure", "active": True}, {"action": "link_failure", "active": True},
                    {"action": "spawn_parked", "slot_id": "P01"},
                    {"action": "remove_parked", "slot_id": "P01", "placement_id": "old-placement"}]
        for data in controls:
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/control", dict(data, run_id="previous-run"))
            self.assertEqual(caught.exception.code, 400)
        for placement_id in (None, "", [], "invalid token"):
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/control", {"action": "remove_parked", "slot_id": "P01",
                                             "placement_id": placement_id, "run_id": before["run_id"]})
            self.assertEqual(caught.exception.code, 400)
        after = self.state()
        self.assertEqual(after["run_id"], before["run_id"])
        self.assertEqual(after["sim_time"], before["sim_time"])
        self.assertEqual(after["controls"], before["controls"])
        self.assertTrue(after["paused"])
        self.check("all_operator_controls_reject_old_run_and_bad_removal_identity", {
            "old_run_control_count": len(controls), "invalid_removal_count": 4,
            "status": 400, "run_id_unchanged": True, "model_time_unchanged": True},
            "régi futás vezérlései és hibás eltávolítási azonosítók nem módosítják az aktuális futást")

    def test_11_commands_already_queued_before_reset_do_not_cross_runs(self):
        before = self.state()
        # A runtime ugyanazzal a lockkal vált futásazonosítót. Így determinisztikusan
        # a reset mögé kerülnek a még a régi futásban elfogadott kezelőparancsok.
        # Nincs helyettesített ütemező, modell vagy MQTT-kliens.
        with self.app.lock:
            self.app.command({"action": "reset", "run_id": before["run_id"]})
            queued = [{"action": "resume"},
                      {"action": "speed", "value": 1 if before["speed"] == 4 else 4},
                      {"action": "pedestrian", "active": True},
                      {"action": "sensor_failure", "active": True},
                      {"action": "link_failure", "active": True},
                      {"action": "spawn_parked", "slot_id": "P01", "request_id": "queued-before-reset"}]
            for data in queued:
                self.app.command(dict(data, run_id=before["run_id"]))
        after = self.wait_for(lambda s: s["run_id"] != before["run_id"]
                              and bool(s.get("world")) and bool(s.get("coordinator"))
                              and s["connected"], "reset utáni megfigyelt és kapcsolt új futás", timeout=8)
        self.assertTrue(after["paused"])
        self.assertEqual(after["sim_time"], 0)
        self.assertEqual(after["speed"], before["speed"])
        self.assertTrue(all(not flag for flag in after["controls"].values()))
        self.assertEqual(after["world"]["parked_cars"], [])
        self.assertIsNone(after["parking_setup"])
        self.check("already_accepted_controls_cannot_cross_a_queued_reset", {
            "discarded_control_count": len(queued), "new_run_id": after["run_id"],
            "paused": after["paused"], "sim_time": after["sim_time"],
            "controls": after["controls"], "speed_unchanged": after["speed"] == before["speed"]},
            "a reset előtt elfogadott, mögötte sorban álló régi parancsok kimaradnak az új futásban")

    def test_12_local_http_boundary_and_invalid_mqtt_do_not_interrupt_controls(self):
        port = self.server.server_port
        attacker = f"foreign.invalid:{port}"
        for path, data in (("/api/state", None), ("/api/export?kind=events", None),
                           ("/api/control", {"action": "reset"})):
            with self.assertRaises(HTTPError) as denied:
                self.request(path, data, {"Host": attacker, "Origin": f"http://{attacker}"})
            self.assertEqual(denied.exception.code, 403)
        for path, marker in (("/", b'id="yard"'), ("/app.js", b"function poll"), ("/style.css", b".run-buttons")):
            status, _, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(marker, body)
        before = self.state()
        with self.assertRaises(HTTPError) as denied:
            self.request("/api/control", {"action": "pause", "extra": float("nan")})
        self.assertEqual(denied.exception.code, 400)
        status, _, _ = self.request("/api/control", {"action": "pause"}, {"Origin": self.base})
        self.assertEqual(status, 202)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect("127.0.0.1", self.app.mqtt_port)
        client.loop_start()
        try:
            topic = f"mini-yard/{before['run_id']}/events"
            bad = {"run_id": before["run_id"], "seq": [], "sim_time": 0, "type": "bad", "message": "bad"}
            client.publish(topic, json.dumps(bad), qos=1).wait_for_publish(timeout=4)
            self.wait_for(lambda s: s["mqtt"]["invalid"] > before["mqtt"]["invalid"], "hibás csomag eldobása")
            self.control("resume")
            self.wait_for(lambda s: s["sim_time"] > 0, "a vezérlőszál tovább működik")
            _, _, raw = self.request("/api/export?kind=events")
            self.assertFalse(any(r["payload"].get("type") == "bad" for r in json.loads(raw)))
        finally:
            client.disconnect()
            client.loop_stop()
        self.check("local_http_and_invalid_mqtt", {"foreign_host": 403, "invalid_json": 400,
                                                  "legitimate_control": 202, "static_files": 3},
                   "csak a helyi HTTP-cím és érvényes vezérlés fogadható el")

    def test_13_dropped_reports_and_acks_recover_without_losing_parallel_results(self):
        from unittest.mock import patch
        vehicle_publish, service_publish = self.app.vehicle_bus.publish, self.app.service_bus.publish
        dropped = set()

        def drop_once(publish, topic, payload, qos):
            kind = payload.get("kind") or payload.get("type")
            if kind in ("parking_report", "parking_confirmed", "parked_spawned") and kind not in dropped:
                dropped.add(kind)
                return True
            return publish(topic, payload, qos)

        with patch.object(self.app.vehicle_bus, "publish", lambda t, p, qos=1: drop_once(vehicle_publish, t, p, qos)), \
             patch.object(self.app.service_bus, "publish", lambda t, p, qos=1: drop_once(service_publish, t, p, qos)):
            self.control("spawn_parked", slot_id="P07", request_id="parallel-a")
            self.control("spawn_parked", slot_id="P08", request_id="parallel-b")
            result = self.wait_for(lambda s: len(s["world"]["parked_cars"]) == 2, "két háttérművelet helyreállása")
            for request_id in ("parallel-a", "parallel-b"):
                self.assertEqual(result["parking_operations"][request_id]["kind"], "parked_spawned")
            self.control("speed", value=4)
            self.control("resume")
            result = self.wait_for(lambda s: self.car(s).get("state") == "parked",
                                   "elveszett végjelentés és nyugta ismétlése", timeout=22)
            self.assertEqual(result["coordinator"]["slots"][0]["state"], "occupied")
        self.assertEqual(dropped, {"parking_report", "parking_confirmed", "parked_spawned"})
        self.check("single_mqtt_loss_and_parallel_setup", {"dropped_kinds": sorted(dropped), "parallel_results": 2},
                   "három egyszeri adatvesztés után helyreáll a parkolás és két kezelői nyugta")

    def test_14_parking_reconciles_after_all_fast_acknowledgements_are_lost(self):
        from unittest.mock import patch
        publish = self.app.service_bus.publish
        dropped = []
        recover = threading.Event()

        def lose_ack(topic, payload, qos=1):
            if payload.get("kind") == "parking_confirmed" and not recover.is_set():
                dropped.append(payload["reservation_id"])
                return True
            return publish(topic, payload, qos)

        with patch.object(self.app.service_bus, "publish", lose_ack):
            self.control("speed", value=4)
            self.control("resume")
            waiting = self.wait_for(lambda s: self.car(s).get("state") == "parking_reconciling",
                                    "tartós nyugtavesztés után egyeztetés", timeout=24)
            self.assertGreaterEqual(len(dropped), 12)
            self.assertEqual(waiting["world"]["metrics"]["completed"], 0)
            self.assertEqual(waiting["coordinator"]["slots"][0]["state"], "occupied")
            recover.set()
            done = self.wait_for(lambda s: self.car(s).get("state") == "parked"
                                 and self.car(s, "CAR02").get("state") == "driving",
                                 "a foglalás egyezik és a következő jármű elindul", timeout=8)
        self.assertEqual(set(dropped), {self.car(done)["reservation_id"]})
        self.assertEqual(done["world"]["slots"][0]["state"], "occupied")
        self.check("parking_recovers_after_fast_retry_budget", {"lost_acknowledgements": len(dropped),
                                                                "following_car": "driving"},
                   "ugyanazzal a foglalással folytatódik a flotta")

    def test_15_acknowledgement_in_pause_is_recorded_without_a_model_step(self):
        from unittest.mock import patch
        publish = self.app.service_bus.publish
        held = queue.Queue()

        def delay_ack(topic, payload, qos=1):
            if payload.get("kind") == "parking_confirmed":
                held.put((topic, payload, qos))
                return True
            return publish(topic, payload, qos)

        with patch.object(self.app.service_bus, "publish", delay_ack):
            self.control("speed", value=4)
            self.control("resume")
            topic, payload, qos = held.get(timeout=24)
            self.control("pause")
            frozen = self.wait_for(lambda s: s.get("paused"), "modellóra befagyasztva")
        self.assertTrue(publish(topic, payload, qos))
        done = self.wait_for(lambda s: self.car(s).get("state") == "parked"
                             and any(e.get("type") == "parked" for e in s["events"]),
                             "lezárás és esemény szünetben")
        self.assertEqual(done["sim_time"], frozen["sim_time"])
        self.assertTrue(done["paused"])
        _, _, raw = self.request("/api/export?kind=events")
        self.assertEqual(sum(r["payload"].get("type") == "parked" for r in json.loads(raw)), 1)
        self.check("paused_parking_event_is_exported", {"sim_time": done["sim_time"], "parked_events": 1},
                   "a parkolási esemény folytatás nélkül bekerül az exportba")

    def test_16_unsigned_samples_cannot_change_observed_state(self):
        before = self.state()
        connected = threading.Event()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = lambda *_: connected.set()
        client.connect("127.0.0.1", self.app.mqtt_port)
        client.loop_start()
        try:
            self.assertTrue(connected.wait(4))
            sample = dict(before["world"], seq=before["world"]["seq"] + 10, sim_time=123)
            topic = f"mini-yard/{before['run_id']}/world"
            client.publish(topic, json.dumps(sample), qos=1).wait_for_publish(timeout=4)
            client.publish(topic, json.dumps(dict(sample, producer="vehicle", _auth="0"*64)),
                           qos=1).wait_for_publish(timeout=4)
            self.wait_for(lambda s: s["mqtt"]["invalid"] >= before["mqtt"]["invalid"] + 2,
                          "hitelesítés nélküli és hibás hitelesítésű minta elutasítása")
            self.assertEqual(self.state()["sim_time"], 0)
            self.control("resume")
            self.wait_for(lambda s: 0 < s["sim_time"] < 123 and s["ready"], "az eredeti adatút folytatódik")
        finally:
            client.disconnect()
            client.loop_stop()
        self.check("unsigned_world_does_not_replace_observed_state", {"rejected_samples": 2, "ready": True},
                   "a megfigyelt állapotot csak hitelesített üzenet frissíti")

    def test_17_paused_switch_events_are_exported_and_survive_immediate_reset(self):
        before = self.state()
        self.assertTrue(before["paused"])
        switches = ("pedestrian", "sensor_failure")
        expected = []
        for action in switches:
            for active, suffix in ((True, "_on"), (False, "_off")):
                expected.append(action + suffix)
                self.control(action, active=active, run_id=before["run_id"])
                observed = self.wait_for(lambda s: any(e["type"] == expected[-1] for e in s["events"]),
                                         expected[-1] + " szünetben")
                self.assertTrue(observed["paused"])
                self.assertEqual(observed["sim_time"], before["sim_time"])
        _, _, raw = self.request("/api/export?kind=events")
        exported = [r["payload"] for r in json.loads(raw) if r["relative_topic"] == "events"]
        self.assertEqual([e["type"] for e in exported], expected)
        self.assertTrue(all(e["sim_time"] == before["sim_time"] for e in exported))
        self.check("paused_switch_changes_reach_event_list_and_export", {"event_types": expected[:],
                                                                        "sim_time": before["sim_time"]},
                   "mindkét kapcsoló be- és kikapcsolása modelllépés nélkül megfigyelhető")

        with self.app.lock:
            for action in switches:
                self.app.command({"action": action, "active": True, "run_id": before["run_id"]})
                expected.append(action + "_on")
            self.app.command({"action": "reset", "run_id": before["run_id"]})
        after = self.wait_for(lambda s: s["run_id"] != before["run_id"] and s["ready"],
                              "új futás közvetlenül a kapcsolók után", timeout=8)
        old_path = self.root / before["log_file"]
        records = self.app.export_records(old_path, old_path.stat().st_size)
        old_events = [r["payload"] for r in records if r["relative_topic"] == "events"]
        self.assertEqual([e["type"] for e in old_events], expected)
        self.assertTrue(all(e["run_id"] == before["run_id"] for e in old_events))
        self.assertFalse(after["events"])
        self.assertTrue(all(not after["controls"][action] for action in switches))
        self.check("queued_switch_events_are_retained_by_previous_run", {
            "event_types": expected, "previous_run": before["run_id"], "next_run": after["run_id"]},
            "a reset előtt végrehajtott kapcsolások a régi naplóba kerülnek")

    def test_18_switch_accepted_before_broker_loss_is_observed_after_reconnect(self):
        from unittest.mock import patch

        before = self.state()
        entered, release = threading.Event(), threading.Event()
        apply_controls = self.app._apply_controls

        def gated_controls():
            entered.set()
            if release.wait(6):
                apply_controls()

        with patch.object(self.app, "_apply_controls", gated_controls):
            try:
                self.assertTrue(entered.wait(2))
                self.control("pedestrian", active=True, run_id=before["run_id"])
                self.control("sensor_failure", active=True, run_id=before["run_id"])
                self.app.broker.close()
            finally:
                release.set()
        offline = self.wait_for(lambda s: not s["ready"] and s["controls"]["sensor_failure"],
                                "a kapcsolók feldolgozása leállított broker mellett")
        self.assertTrue(offline["controls"]["pedestrian"])
        self.assertFalse(offline["events"])
        self.app.broker = LocalBroker(self.app.mqtt_port)
        self.app.broker.start()
        kinds = ["pedestrian_on", "sensor_failure_on"]
        recovered = self.wait_for(lambda s: s["ready"] and len(s["events"]) >= len(kinds),
                                  "a két sorban maradt esemény megérkezése", timeout=10)
        self.assertEqual(recovered["run_id"], before["run_id"])
        self.assertTrue(recovered["paused"])
        self.assertEqual(recovered["sim_time"], before["sim_time"])
        self.assertEqual([event["type"] for event in recovered["events"]], kinds)
        _, _, raw = self.request("/api/export?kind=events")
        self.assertEqual([r["payload"]["type"] for r in json.loads(raw) if r["relative_topic"] == "events"], kinds)
        self.check("paused_switch_events_retry_after_broker_restart", {
            "event_types": kinds, "run_id": recovered["run_id"], "sim_time": recovered["sim_time"]},
            "a broker visszatérése után a korábban elfogadott kapcsolások sorrendben és egyszer kerülnek az MQTT-naplóba")

    def test_19_request_headers_have_a_total_read_deadline(self):
        from unittest.mock import patch

        address = ("127.0.0.1", self.server.server_port)
        host = f"Host: {address[0]}:{address[1]}\r\n".encode("ascii")
        before = self.state()
        with patch("mini_yard.server.HTTP_HEADER_TIMEOUT_S", 0.5):
            for prefix in (b"GET /api/state", b"GET /api/state HTTP/1.1\r\n" + host):
                with self.subTest(prefix=prefix), socket.create_connection(address, timeout=2) as connection:
                    connection.sendall(prefix)
                    time.sleep(0.25)
                    connection.sendall(b" ")
                    connection.settimeout(0.4)
                    self.assertEqual(connection.recv(1), b"")

        body = json.dumps({"action": "pedestrian", "active": True, "run_id": before["run_id"]}).encode()
        request = (b"POST /api/control HTTP/1.1\r\n" + host + b"Content-Type: application/json\r\n"
                   + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        with socket.create_connection(address, timeout=2) as connection:
            connection.sendall(request)
            with connection.makefile("rb") as response:
                self.assertIn(b" 202 ", response.readline())
        self.wait_for(lambda state: state["controls"]["pedestrian"], "teljes fejlécű vezérlőkérés feldolgozása")
        self.check("http_header_deadline_and_buffered_control_body", {"incomplete_requests_closed": 2, "post_status": 202},
                   "a kérésvonal és a fejlécek közös határidőt kapnak, az előre beolvasott törzs feldolgozható")

    def test_20_uncertain_placement_blocks_removal_until_reconciled(self):
        from unittest.mock import patch

        publish = self.app.service_bus.publish
        dropped = []
        request_id = "uncertain-placement"

        def drop_placement_ack(topic, payload, qos=1):
            if topic == "commands/parking_setup" and payload["request_id"] == request_id:
                dropped.append(payload["seq"])
                return True
            return publish(topic, payload, qos)

        remove = {"action": "remove_parked", "slot_id": "P01", "request_id": "remove-after-retry",
                  "placement_id": request_id}
        with patch.object(self.app.service_bus, "publish", drop_placement_ack):
            self.control("spawn_parked", slot_id="P01", request_id=request_id)
            uncertain = self.wait_for(lambda s: s["parking_operations"].get(request_id, {}).get("kind")
                                      == "setup_uncertain", "az elhelyezés gyors próbái elfogytak", timeout=10)
            self.assertEqual(len(dropped), 12)
            self.assertEqual(self.slot(uncertain, "P01")["placement_id"], request_id)
            self.assertEqual(uncertain["world"]["parked_cars"], [])
            with self.assertRaises(HTTPError) as blocked:
                self.request("/api/control", remove)
            self.assertEqual(blocked.exception.code, 409)
            self.assertEqual(self.spawn("P02", "independent")["parking_setup"]["kind"], "parked_spawned")
            self.assertNotIn(remove["request_id"], self.state()["parking_operations"])

        self.spawn("P01", request_id)
        self.wait_for(lambda s: s["parking_operations"][request_id]["kind"] == "parked_spawned",
                      "az eredeti elhelyezés nyugtáját a világmodell feldolgozta")
        self.remove_parked("P01", request_id, remove["request_id"])
        self.wait_for(lambda s: self.slot(s, "P01")["state"] == "free"
                      and s["world"]["slots"][0]["state"] == "free", "mindkét oldal felszabadította a helyet")
        self.spawn("P01", request_id)
        self.wait_for(lambda s: all(car["slot_id"] != "P01" for car in s["world"]["parked_cars"]),
                      "régi sikeres válasz nem hozza vissza az eltávolított autót")
        self.control("speed", value=4)
        self.control("resume")
        parked = self.wait_for(lambda s: self.car(s).get("state") == "parked",
                               "az érkező autó beparkolt", timeout=25)
        self.assertEqual(self.car(parked)["slot_id"], "P01")
        self.check("uncertain_manual_operation_blocks_only_the_conflicting_slot", {
            "dropped_acks": len(dropped), "conflict_status": 409, "independent_slot": "P02",
            "parked_slot": self.car(parked)["slot_id"]},
            "egyeztetésig a P01 módosítása blokkolt, majd a következő autó ugyanoda sikeresen beparkol")

    def test_21_actual_broker_loss_marks_cached_state_stale_and_rejects_controls(self):
        self.control("resume")
        self.wait_for(lambda s: self.car(s).get("state") == "driving", "haladó autó")
        self.app.broker.close()
        stale = self.wait_for(lambda s: not s["ready"] and not s["mqtt"]["fresh"], "elavult MQTT-állapot")
        self.assertFalse(stale["mqtt"]["clients"]["observer"])
        with self.assertRaises(HTTPError) as denied:
            self.request("/api/control", {"action": "pause"})
        self.assertEqual(denied.exception.code, 503)
        self.check("actual_broker_loss_disables_controls", {"observer_ready": False, "http": 503},
                   "brokerleálláskor elavult az állapot és a vezérlés átmenetileg elérhetetlen")


def main():
    started_utc, before = utc_now(), source_hashes()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealMqttHttpIntegration)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    after = source_hashes()
    unchanged = before == after
    successful = result.wasSuccessful() and unchanged
    raw_logs = {p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size, "sha256": digest(p)}
                for p in sorted(EVIDENCE.rglob("*.jsonl"))} if EVIDENCE.exists() else {}
    report = {"kind": "real_loopback_mqtt_http_integration", "started_utc": started_utc,
              "finished_utc": utc_now(), "passed": successful, "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "checks": CHECKS, "runs": RUNS, "source_unchanged_during_checks": unchanged,
              "applied_config": RealMqttHttpIntegration.test_config,
              "source_sha256_before": before, "source_sha256_after": after,
              "evidence_directory": EVIDENCE.relative_to(ROOT).as_posix(), "raw_logs": raw_logs,
              "limitations": ["AI segítségével készített ellenőrzés; nem önálló hallgatói hozzájárulás igazolása.",
                               "A helyi brokerleállás és az irányított szívveréskimaradás külön próba. Ipari, többgépes hálózat nem szerepel.",
                               "Nem statisztikai kampány és nem valós jármű biztonsági validálása."]}
    (ROOT / "results").mkdir(exist_ok=True)
    target = ROOT / "results" / ("verification.json" if successful else "verification-failed.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nBizonylat: {target}\nMért feltételek: {len(CHECKS)}; forrás változatlan: {unchanged}")
    return 0 if successful else 1


if __name__ == "__main__":
    sys.exit(main())
