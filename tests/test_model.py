"""Érdemi állapotgép- és foglalási tesztek hálózat nélkül.

A valódi MQTT-adatutat külön integrációs ellenőrzés vizsgálja.
"""
from copy import deepcopy
import unittest

from mini_yard.model import Coordinator, DEFAULT_CONFIG, World


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.world = World(DEFAULT_CONFIG, "test-run")
        self.coordinator = Coordinator(DEFAULT_CONFIG, "test-run")

    def deliver(self, connected=True, dt=0.1):
        messages = self.world.step(dt, connected=connected)
        for topic, data in messages:
            for command_topic, command in self.coordinator.handle(topic, data):
                self.world.handle_command(command_topic, command)
        return messages

    def tick(self, count, **kwargs):
        for _ in range(count):
            self.deliver(**kwargs)

    def test_three_cars_finish_distinct_places_with_exact_path_length(self):
        self.tick(1300)
        snap = self.world.snapshot()
        self.assertEqual(snap["metrics"]["completed"], 3)
        self.assertEqual([c["slot_id"] for c in snap["cars"]], ["P01", "P02", "P03"])
        for car, expected in zip(snap["cars"], (111.0, 106.0, 101.0)):
            self.assertAlmostEqual(car["distance_m"], expected)
        self.assertEqual([s["state"] for s in self.coordinator.slots[:3]], ["occupied"] * 3)

    def test_at_most_one_car_moves_and_all_envelopes_are_ordered(self):
        previous = 0
        for _ in range(450):
            messages = self.deliver()
            self.assertLessEqual(sum(c["speed"] > 0 for c in self.world.cars), 1)
            for _, payload in messages:
                self.assertEqual(payload["run_id"], "test-run")
                self.assertGreater(payload["seq"], previous)
                self.assertEqual(payload["sim_time"], self.world.sim_time)
                previous = payload["seq"]

    def test_duplicate_request_returns_same_reservation_without_double_booking(self):
        request = self.world.step(0.1)[0][1]
        first = self.coordinator.handle("requests/parking", request)
        again = self.coordinator.handle("requests/parking", request)
        self.assertEqual(first[0][1]["reservation_id"], again[0][1]["reservation_id"])
        self.assertEqual(sum(s["state"] == "reserved" for s in self.coordinator.slots), 1)

    def test_commands_reject_wrong_identity_routes_and_out_of_order_sequence(self):
        request = self.world.step(0.1)[0][1]
        topic, grant = self.coordinator.handle("requests/parking", request)[0]
        for key, value in (("run_id", "other"), ("request_id", "stale"), ("car_id", "CAR02"),
                           ("reservation_id", "made-up"), ("route", [[0, 0]])):
            with self.subTest(key=key):
                self.assertFalse(self.world.handle_command(topic, grant | {key: value}))
        wait = grant | {"kind": "parking_wait", "seq": grant["seq"] + 4}
        self.assertTrue(self.world.handle_command(topic, wait))
        self.assertFalse(self.world.handle_command(topic, grant))
        grant["seq"] += 5
        self.assertTrue(self.world.handle_command(topic, grant))
        self.assertFalse(self.world.handle_command(topic, grant))
        self.assertFalse(self.world.handle_command(topic, grant | {"seq": grant["seq"] + 1}))

    def test_capacity_exhaustion_keeps_other_cars_waiting(self):
        self.world = World({"slot_count": 1}, "test-run")
        self.coordinator = Coordinator({"slot_count": 1}, "test-run")
        self.tick(900)
        self.assertEqual(self.world.snapshot()["metrics"]["completed"], 1)
        self.assertEqual(self.world.cars[1]["state"], "requesting")
        self.assertIsNone(self.world.cars[1]["slot_id"])
        self.assertEqual(self.world.cars[2]["state"], "waiting")
        self.assertEqual(len(self.coordinator.reservations), 1)

    def test_pedestrian_stop_clamps_at_stop_line_and_resume_finishes(self):
        self.world.control("pedestrian", {"active": True})
        self.tick(200)
        car = self.world.cars[0]
        self.assertEqual(car["state"], "pedestrian_stop")
        self.assertLessEqual(car["x"], 25)
        self.assertAlmostEqual(car["x"], 25)
        self.assertEqual(car["speed"], 0)
        self.assertEqual(self.world.pedestrian_stops, 1)
        self.world.control("pedestrian", {"active": False})
        self.tick(1300)
        self.assertEqual(self.world.snapshot()["metrics"]["completed"], 3)

    def test_sensor_failure_and_heartbeat_loss_stop_without_drift_then_resume(self):
        self.tick(30)
        car = self.world.cars[0]
        location = car["x"], car["y"]
        self.world.control("sensor_failure", {"active": True})
        self.tick(15)
        self.assertEqual(car["state"], "sensor_stop")
        self.assertFalse(self.world.snapshot()["cars"][0]["sensor"]["valid"])
        self.assertEqual((car["x"], car["y"]), location)
        self.world.control("sensor_failure", {"active": False})
        self.tick(15, connected=False)
        self.assertEqual(car["state"], "communication_stop")
        self.assertEqual((car["x"], car["y"]), location)
        self.tick(15)
        self.assertGreater(car["x"], location[0])

    def test_invalid_parked_report_cannot_occupy_slot(self):
        request = self.world.step(0.1)[0][1]
        grant = self.coordinator.handle("requests/parking", request)[0][1]
        report = grant | {"type": "parked", "seq": 90, "sim_time": 30, "x": 14, "y": 7}
        self.assertEqual(self.coordinator.handle("events", report | {"reservation_id": "wrong"}), [])
        self.assertEqual(self.coordinator.handle("events", report | {"x": 50}), [])
        self.assertEqual(self.coordinator.slots[0]["state"], "reserved")
        self.assertTrue(self.coordinator.handle("events", report))
        self.assertEqual(self.coordinator.slots[0]["state"], "occupied")
        self.assertEqual(self.coordinator.handle("events", report), [])

    def test_malformed_inputs_fail_closed_and_snapshot_is_detached(self):
        for payload in (None, [], {}, {"run_id": "test-run", "seq": True, "sim_time": 0},
                        {"run_id": "test-run", "seq": 1, "sim_time": float("nan")},
                        {"run_id": "test-run", "seq": 1, "sim_time": 10 ** 1000},
                        {"run_id": "test-run", "seq": 1, "sim_time": 0, "car_id": []}):
            self.assertFalse(self.world.handle_command("commands/CAR01", payload))
            self.assertEqual(self.coordinator.handle("requests/parking", payload), [])
        self.assertFalse(self.world.control("pedestrian", {"active": "false"}))
        for bad_dt in (0, -1, 0.2, float("nan"), True):
            with self.assertRaises(ValueError):
                self.world.step(bad_dt)
        for config in ({"car_count": 7}, {"slot_count": 0}, {"speed_mps": 0},
                       {"speed_mps": float("inf")}, {"acceleration_mps2": float("nan")},
                       {"braking_mps2": -1}, {"dt": 0.2}, {"heartbeat_timeout_s": False}):
            with self.assertRaises(ValueError):
                World(config, "x")
        original = deepcopy(self.world.cars)
        self.world.snapshot()["cars"][0]["x"] = 999
        self.assertEqual(self.world.cars, original)

    def test_queue_has_non_overlapping_centers_and_exit_handoff_is_not_distance(self):
        world = World({"car_count": 6}, "queue")
        self.assertEqual([(c["x"], c["y"]) for c in world.cars],
                         [(8 + 6 * i, 35) for i in range(6)])
        world.step(0.1)
        self.assertEqual((world.cars[0]["x"], world.cars[0]["y"]), (8, 28))
        self.assertEqual(world.cars[0]["distance_m"], 0)

    def test_stop_times_use_model_seconds_and_do_not_double_count_faults(self):
        self.tick(30)
        car = self.world.cars[0]
        self.world.control("sensor_failure", {"active": True})
        self.tick(5, dt=0.05)
        self.assertEqual(car["stop_time_s"],
                         {"pedestrian": 0.0, "sensor_failure": 0.25, "communication": 0.0})
        self.tick(4, dt=0.025, connected=False)
        self.assertEqual(car["stop_time_s"],
                         {"pedestrian": 0.0, "sensor_failure": 0.25, "communication": 0.1})
        self.world.control("sensor_failure", {"active": False})
        self.tick(10)
        self.assertEqual(sum(car["stop_time_s"].values()), 0.35)
        self.assertGreater(car["speed"], 0)
        self.assertTrue(all(sum(c["stop_time_s"].values()) == 0 for c in self.world.cars[1:]))

    def test_pedestrian_stop_time_excludes_last_moving_tick_and_freezes_on_release(self):
        self.world.control("pedestrian", {"active": True})
        car = self.world.cars[0]
        for _ in range(300):
            position = car["x"], car["y"]
            self.deliver()
            if car["state"] == "pedestrian_stop":
                self.assertNotEqual((car["x"], car["y"]), position)
                break
        else:
            self.fail("Az autó nem állt meg a zebránál.")
        self.assertEqual(car["stop_time_s"]["pedestrian"], 0)
        self.tick(5)
        self.assertEqual(car["stop_time_s"]["pedestrian"], 0.5)
        self.world.control("pedestrian", {"active": False})
        self.tick(10)
        self.assertEqual(car["stop_time_s"]["pedestrian"], 0.5)
        self.assertGreater(car["speed"], 0)

    def test_waiting_requesting_and_parked_time_is_not_a_protective_stop(self):
        self.deliver()
        self.assertEqual(self.world.cars[0]["state"], "requesting")
        self.assertEqual(self.world.cars[1]["state"], "waiting")
        self.tick(1300)
        self.assertEqual(self.world.snapshot()["metrics"]["completed"], 3)
        self.tick(10, connected=False)
        self.assertTrue(all(sum(c["stop_time_s"].values()) == 0 for c in self.world.cars))

    def test_stop_time_telemetry_is_detached_and_a_new_run_starts_at_zero(self):
        self.tick(2, connected=False)
        messages = self.deliver(connected=False)
        telemetry = next(p for t, p in messages if t == "telemetry/CAR01")
        snapshot = self.world.snapshot()
        self.assertEqual(telemetry["stop_time_s"], snapshot["cars"][0]["stop_time_s"])
        self.assertEqual(telemetry["stop_time_s"]["communication"], 0.3)
        telemetry["stop_time_s"]["communication"] = 999
        snapshot["cars"][0]["stop_time_s"]["communication"] = 888
        self.assertEqual(self.world.cars[0]["stop_time_s"]["communication"], 0.3)
        self.assertEqual(self.world.cars[1]["stop_time_s"]["communication"], 0)
        fresh = World(DEFAULT_CONFIG, "new-run")
        self.assertTrue(all(sum(c["stop_time_s"].values()) == 0 for c in fresh.cars))

    def place_parked(self, slot_id="P01", request_id="setup-one"):
        topic, request = self.world.request_parked_car(slot_id, request_id)
        replies = self.coordinator.handle(topic, request)
        for command_topic, payload in replies:
            self.world.handle_command(command_topic, payload)
        return request, replies[0][1]

    def test_preparked_car_is_separate_from_moving_fleet_and_preserves_counts(self):
        _, ack = self.place_parked()
        self.assertEqual(ack["kind"], "parked_spawned")
        snap = self.world.snapshot()
        self.assertEqual(snap["metrics"]["completed"], 0)
        self.assertEqual(snap["metrics"]["total"], 3)
        self.assertEqual(snap["metrics"]["preparked"], 1)
        self.assertEqual(snap["sim_time"], 0)
        self.assertEqual(snap["parked_cars"], [{"id": "PARK-P01", "car_id": "PARK-P01",
            "slot_id": "P01", "x": 14, "y": 7, "heading": -90, "state": "parked", "origin": "preparked"}])
        self.assertEqual(self.coordinator.slots[0]["state"], "occupied")
        self.assertEqual(self.coordinator.reservations, {})
        snap["parked_cars"][0]["x"] = 999
        self.assertEqual(self.world.snapshot()["parked_cars"][0]["x"], 14)
        events = self.world.flush_events()
        self.assertEqual(events[0][1]["type"], "parked_spawned")
        self.assertEqual(events[0][1]["car_id"], "PARK-P01")

    def test_preparked_repeat_is_idempotent_and_conflicting_reuse_is_rejected(self):
        request, ack = self.place_parked()
        self.assertFalse(self.world.handle_command("commands/parking_setup", ack))
        repeated = self.coordinator.handle("requests/parking_setup", request)[0][1]
        self.assertTrue(self.world.handle_command("commands/parking_setup", repeated))
        self.assertEqual(len(self.world.parked_cars), 1)
        self.assertEqual(len(self.world.flush_events()), 1)
        conflict = request | {"slot_id": "P02", "car_id": "PARK-P02", "seq": request["seq"] + 1}
        rejected = self.coordinator.handle("requests/parking_setup", conflict)[0][1]
        self.assertEqual(rejected["kind"], "parking_setup_rejected")
        self.assertEqual(self.coordinator.slots[1]["state"], "free")
        with self.assertRaises(ValueError):
            self.world.request_parked_car("P02", "setup-one")

    def test_moving_cars_skip_preparked_slot_and_complete_original_work(self):
        self.place_parked()
        self.tick(1300)
        snap = self.world.snapshot()
        self.assertEqual([car["slot_id"] for car in snap["cars"]], ["P02", "P03", "P04"])
        self.assertEqual((snap["metrics"]["completed"], snap["metrics"]["total"]), (3, 3))
        self.assertEqual(len(self.coordinator.reservations), 3)
        self.assertEqual(sum(s["state"] == "occupied" for s in self.coordinator.slots), 4)

    def test_setup_rejects_occupied_reserved_and_unknown_slots_without_reassignment(self):
        self.place_parked()
        self.deliver()
        self.assertEqual(self.coordinator.slots[1]["state"], "reserved")
        for slot in ("P01", "P02", "P99"):
            with self.subTest(slot=slot):
                _, reply = self.place_parked(slot, "reject-" + slot)
                self.assertEqual(reply["kind"], "parking_setup_rejected")
        self.assertEqual(len(self.world.parked_cars), 1)
        self.assertEqual(self.coordinator.slots[1]["car_id"], "CAR01")
        self.assertEqual(len(self.coordinator.reservations), 1)

    def test_setup_commands_require_current_pending_identity_sequence_and_fixed_pose(self):
        request = self.world.request_parked_car("P01", "setup-valid")[1]
        ack = self.coordinator.handle("requests/parking_setup", request)[0][1]
        for key, value in (("run_id", "old-run"), ("request_id", "unknown"), ("request_id", []),
                           ("slot_id", "P02"), ("car_id", "CAR01"), ("seq", 0),
                           ("x", 15), ("y", 28), ("heading", 0)):
            with self.subTest(key=key):
                self.assertFalse(self.world.handle_command("commands/parking_setup", ack | {key: value}))
        self.assertEqual(self.world.parked_cars, [])
        self.assertTrue(self.world.handle_command("commands/parking_setup", ack))
        self.assertFalse(self.world.handle_command("commands/parking_setup", ack))

    def test_setup_requests_reject_stale_envelopes_without_occupying_extra_places(self):
        request, _ = self.place_parked()
        invalid = [request | {"run_id": "old-run"}, request | {"request_id": []},
                   request | {"car_id": "CAR01"}, request | {"slot_id": "P-1"}]
        for payload in invalid:
            self.assertEqual(self.coordinator.handle("requests/parking_setup", payload), [])
        stale = request | {"request_id": "older", "slot_id": "P02", "car_id": "PARK-P02"}
        reply = self.coordinator.handle("requests/parking_setup", stale)[0][1]
        self.assertEqual(reply["kind"], "parking_setup_rejected")
        self.assertEqual(self.coordinator.slots[1]["state"], "free")

    def test_full_preparked_capacity_blocks_requests_and_new_run_clears_setup(self):
        self.world = World({"slot_count": 1}, "test-run")
        self.coordinator = Coordinator({"slot_count": 1}, "test-run")
        self.place_parked()
        self.tick(30)
        self.assertEqual(self.world.cars[0]["state"], "requesting")
        self.assertIsNone(self.world.cars[0]["slot_id"])
        self.assertEqual(self.world.snapshot()["metrics"]["completed"], 0)
        fresh = World({"slot_count": 1}, "next-run")
        self.assertEqual(fresh.snapshot()["parked_cars"], [])
        self.assertEqual(fresh.slots[0]["state"], "free")
        self.assertEqual(Coordinator({"slot_count": 1}, "next-run").reservations, {})


if __name__ == "__main__":
    unittest.main()
