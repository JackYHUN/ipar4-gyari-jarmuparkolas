"""Regressions for the measurement-evidence verifier."""
import unittest

from experiments.verify_campaign import (
    REQUIRED_SOURCE_FILES, endpoint_matches, snapshot_series, validate_source_manifest,
)


class EvidenceVerificationTests(unittest.TestCase):
    def test_source_manifest_requires_all_schema_one_files(self):
        full = {name: "a" * 64 for name in REQUIRED_SOURCE_FILES}
        validate_source_manifest(full)
        for bad in ({}, {k: v for k, v in full.items() if k != "mini_yard/runtime.py"},
                    full | {"mini_yard/runtime.py": "not-a-hash"},
                    full | {"unknown.py": "a" * 64}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_source_manifest(bad)

    def test_endpoint_requires_both_observed_states_before_poll(self):
        row = {"request_id": "req", "placement_id": "req", "operation": "spawn",
               "first_successful_poll_perf_counter_ns": 2000000,
               "mqtt_ack_record": {"payload": {"request_id": "req", "placement_id": "req",
                    "operation": "spawn", "kind": "parked_spawned"}}}
        slot = {"id": "P08", "state": "occupied", "placement_id": "req"}
        world = {"slots": [slot], "parked_cars": [{"slot_id": "P08", "placement_id": "req"}]}
        coord = {"slots": [slot]}

        def record(topic, sent, data):
            return {"relative_topic": topic, "payload": dict(data, sent_ns=sent),
                    "latency_ms": 0.1}

        a = record("world", 1000000, world)
        b = record("coordinator", 1100000, coord)
        self.assertTrue(endpoint_matches(snapshot_series([a, b]), row))
        self.assertFalse(endpoint_matches(snapshot_series([a]), row))
        self.assertFalse(endpoint_matches(snapshot_series(
            [a, record("coordinator", 2100000, coord)]), row))
        self.assertFalse(endpoint_matches(snapshot_series(
            [a, b, record("coordinator", 1500000, {"slots": []})]), row))


if __name__ == "__main__":
    unittest.main()
