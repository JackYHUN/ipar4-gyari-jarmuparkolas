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
        cls.app = Application(copy.deepcopy(DEFAULT_CONFIG), cls.root,
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
        self.wait_for(lambda s: bool(s.get("world")) and s.get("connected"),
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

    def test_01_nominal_three_cars_exports_and_external_repeat(self):
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
            info = auditor.publish(prefix + "requests/parking", json.dumps(request_payload), qos=1)
            info.wait_for_publish(timeout=4)
            reply = responses.get(timeout=4)
            self.assertEqual(reply["kind"], "parking_grant")
            self.assertEqual(reply["request_id"], request_payload["request_id"])
            self.assertEqual(reply["slot_id"], self.car(final)["slot_id"])
            reservations = self.state()["coordinator"]["reservations"]
            self.assertEqual(len(reservations), 3)
            self.check("external_mqtt_duplicate_request_idempotent", {
                "car_id": reply["car_id"], "slot_id": reply["slot_id"],
                "reservation_id": reply["reservation_id"], "reservation_count": len(reservations)},
                "független kliens ismételt kérésére ugyanaz a foglalás; továbbra is 3 foglalás")
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
              "source_sha256_before": before, "source_sha256_after": after,
              "evidence_directory": EVIDENCE.relative_to(ROOT).as_posix(), "raw_logs": raw_logs,
              "limitations": ["AI segítségével készített ellenőrzés; nem önálló hallgatói hozzájárulás igazolása.",
                               "Irányított szívverés/parancs hibainjektálás; nem teljes hálózati kiesés.",
                               "Nem statisztikai kampány és nem valós jármű biztonsági validálása."]}
    (ROOT / "results").mkdir(exist_ok=True)
    target = ROOT / "results" / ("verification.json" if successful else "verification-failed.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nBizonylat: {target}\nMért feltételek: {len(CHECKS)}; forrás változatlan: {unchanged}")
    return 0 if successful else 1


if __name__ == "__main__":
    sys.exit(main())
