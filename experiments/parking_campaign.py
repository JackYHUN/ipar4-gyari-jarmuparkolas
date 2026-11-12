"""Páros modellidő-mérés az igazolt automatikus parkolásról."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics

from mini_yard.model import Coordinator, World

from .latency_campaign import ROOT, quantile, utc_now


def make_plan(seed):
    rng = random.Random(seed)
    cases = []
    for slot in (1, 4, 8):
        for sample in range(1, 5):
            dt = rng.choice((0.05, 0.075, 0.1))
            speed = round(rng.uniform(2.4, 3.0), 3)
            braking = round(rng.uniform(1.7, 2.3), 3)
            cases.append({"pair": f"P{slot:02d}-{sample}", "target_slot": slot, "dt": dt,
                          "speed_mps": speed, "braking_mps2": braking,
                          "pedestrian_release_s": round(rng.uniform(11, 15), 3),
                          "sensor_failure_s": round(rng.uniform(0.7, 1.4), 3),
                          "conditions": ["clear", "pedestrian"] + (["sensor_fault"] if sample <= 2 else [])})
    return {"seed": seed, "written_before_run_utc": utc_now(), "timeout_s": 90,
            "criteria": "A pozíció, irány, kontúr és állás ellenőrzése után mindkét fél ugyanazt a foglalást zárta le.",
            "time_base": "modellmásodperc; a koordinátor üzenetei közvetlenül kerülnek átadásra, hálózati idő nélkül",
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in ("mini_yard/model.py", "experiments/parking_campaign.py",
                                           "experiments/latency_campaign.py")},
            "cases": cases, "capacity_case": "P01–P08 előfoglalt; helyhiány külön kimenet."}


def trial(case, condition, timeout):
    config = {"car_count": 1, "slot_count": 8,
              **{key: case[key] for key in ("dt", "speed_mps", "braking_mps2")}}
    run_id = f"{case['pair']}-{condition}"
    world, coordinator = World(config, run_id), Coordinator(config, run_id)

    def deliver(messages):
        for topic, payload in messages:
            for response_topic, response in coordinator.handle(topic, payload):
                world.handle_command(response_topic, response)

    count = 8 if condition == "full" else case["target_slot"] - 1
    for index in range(1, count + 1):
        deliver([world.request_parked_car(f"P{index:02d}", f"setup-{index}")])
    world.control("pedestrian", {"active": condition == "pedestrian"})
    car = world.cars[0]
    sensor_started = None
    outcome = "timeout"
    while world.sim_time + config["dt"] <= timeout + 1e-8:
        if condition == "pedestrian" and world.sim_time >= case["pedestrian_release_s"]:
            world.control("pedestrian", {"active": False})
        if condition == "sensor_fault":
            if sensor_started is None and car["state"] == "aligning":
                sensor_started = world.sim_time
                world.control("sensor_failure", {"active": True})
            if sensor_started is not None and world.sim_time - sensor_started >= case["sensor_failure_s"]:
                world.control("sensor_failure", {"active": False})
        deliver(world.step(config["dt"]))
        if car["state"] == "parked" and coordinator.reservations[car["id"]]["state"] == "occupied":
            outcome = "confirmed"
            break
        if car["state"] == "parking_error":
            outcome = "parking_error"
            break
    if condition == "full" and car["state"] == "requesting" and car["slot_id"] is None:
        outcome = "capacity_wait"
    t = car["timing"]

    def elapsed(start, end):
        return round(t[end] - t[start], 6) if start in t and end in t else None

    return {"pair": case["pair"], "condition": condition, "outcome": outcome, "slot_id": car["slot_id"],
            "start_wait_s": elapsed("dispatched_s", "granted_s"),
            "approach_s": elapsed("granted_s", "maneuver_start_s"),
            "maneuver_and_verification_s": elapsed("maneuver_start_s", "pose_verified_s"),
            "confirmation_s": elapsed("pose_verified_s", "confirmed_s"),
            "total_s": elapsed("dispatched_s", "confirmed_s"),
            "protective_stop_s": car["stop_time_s"],
            "distance_m": round(car["distance_m"], 6), "end_sim_time_s": world.sim_time,
            "pose": {k: car[k] for k in ("x", "y", "heading", "speed")}}


def analyze(rows, seed):
    counts = {}
    for condition in ("clear", "pedestrian", "sensor_fault", "full"):
        selected = [r for r in rows if r["condition"] == condition]
        durations = [r["total_s"] for r in selected if r["outcome"] == "confirmed"]
        counts[condition] = {"trials": len(selected), "confirmed": len(durations),
                             "outcomes": {value: sum(r["outcome"] == value for r in selected)
                                          for value in sorted({r["outcome"] for r in selected})},
                             "median_total_s_if_confirmed": statistics.median(durations) if durations else None}
    pairs = {(r["pair"], r["condition"]): r for r in rows}
    differences = []
    for row in rows:
        if row["condition"] != "pedestrian":
            continue
        baseline = pairs[row["pair"], "clear"]
        if row["outcome"] == baseline["outcome"] == "confirmed":
            if row["slot_id"] != baseline["slot_id"]:
                raise ValueError("A párosított próbák célhelye nem egyezik")
            differences.append(round(row["total_s"] - baseline["total_s"], 6))
    rng = random.Random(seed + 1)
    bootstrap = [statistics.mean(rng.choices(differences, k=len(differences)))
                 for _ in range(10000)] if differences else []
    return {"counts": counts, "complete_time_pairs": len(differences),
            "mean_paired_pedestrian_delay_s": statistics.mean(differences) if differences else None,
            "exploratory_bootstrap_95_s": [quantile(bootstrap, p) for p in (.025, .975)] if bootstrap else None,
            "time_pairs_excluding_failures": counts["pedestrian"]["trials"] - len(differences),
            "scope": "A mintázott virtuális beállításokra érvényes; nem üzemi sikerarány vagy MQTT-késleltetés."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261111)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name, data):
        (args.output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    plan = make_plan(args.seed)
    save("plan.json", plan)
    rows = [trial(case, condition, plan["timeout_s"])
            for case in plan["cases"] for condition in case["conditions"]]
    rows.append(trial(plan["cases"][0], "full", plan["timeout_s"]))
    save("trials.json", rows)
    summary = analyze(rows, args.seed)
    save("summary.json", summary)
    save("manifest.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.iterdir())})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
