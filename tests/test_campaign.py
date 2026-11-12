"""A mérési statisztika és a sikerfeltétel regressziós ellenőrzése."""
import unittest

from experiments.latency_campaign import consistent, paired_summary, quantile


class CampaignAnalysisTests(unittest.TestCase):
    def test_block_pair_difference_does_not_pool_operations(self):
        rows = []
        for pair, baseline, loaded in ((1, [10, 10], [20, 20]),
                                       (2, [100] * 10, [100] * 10)):
            for condition, values in (("paused", baseline), ("running_4x", loaded)):
                rows.extend(dict(pair=pair, condition=condition, warmup=False,
                                 observed_consistency_ms=v) for v in values)
        rows.append(dict(pair=1, condition="paused", warmup=True, observed_consistency_ms=99999))
        result = paired_summary(rows, 4, resamples=1000)
        # Equal weight for the two application pairs, even with unequal operation counts.
        self.assertEqual(result["pair_count"], 2)
        self.assertEqual(result["mean_paired_median_difference_ms"], 5)
        self.assertEqual(result["bootstrap_percentile_95_ci_ms"], [0, 10])

    def test_incomplete_pair_is_not_silently_excluded(self):
        rows = [dict(pair=1, condition="paused", warmup=False, observed_consistency_ms=10),
                dict(pair=2, condition="running_4x", warmup=False, observed_consistency_ms=20)]
        with self.assertRaises(ValueError):
            paired_summary(rows, 4)
        self.assertEqual(quantile([0, 10], .95), 9.5)

    def test_matching_ack_alone_does_not_count_as_consistent_world(self):
        state = {"parking_setup": {"request_id": "req", "placement_id": "req",
                                  "operation": "spawn", "kind": "parked_spawned"},
                 "world": {"slots": [], "parked_cars": []}, "coordinator": {"slots": []}}
        self.assertFalse(consistent(state, "req", "req", "spawn"))
        slot = {"id": "P08", "state": "occupied", "placement_id": "req"}
        state["world"] = {"slots": [slot], "parked_cars": [{"slot_id": "P08", "placement_id": "req"}]}
        state["coordinator"] = {"slots": [slot]}
        self.assertTrue(consistent(state, "req", "req", "spawn"))
        self.assertFalse(consistent(state, "stale", "req", "spawn"))


if __name__ == "__main__":
    unittest.main()
