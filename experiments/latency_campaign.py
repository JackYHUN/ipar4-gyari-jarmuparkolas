"""Valódi MQTT-kezelőút páros, blokkolt válaszidő-mérése.

Futtatás a repository gyökeréből:
  python -B -X utf8 -m experiments.latency_campaign

Csak saját, szabad loopback portokat használ; a 8766-os demót nem vezérli.
Az elemzési egység a frissen indított alkalmazáspár, nem az egyes üzenet.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import logging
import math
from pathlib import Path
import platform
import random
import socket
import statistics
import sys
import time

from mini_yard.runtime import Application


ROOT = Path(__file__).resolve().parents[1]
POLL_S = 0.020
TIMEOUT_S = 5.0


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = list((ROOT / "mini_yard").glob("*.py"))
    paths += [ROOT / "config.json", ROOT / "requirements-lock.txt", Path(__file__)]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths)}


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def quantile(values, fraction):
    """Lineáris interpoláció a rendezett mintán; üres minta nem értelmezett."""
    ordered = sorted(values)
    if not ordered or not 0 <= fraction <= 1:
        raise ValueError("Nem üres minta és 0..1 kvantilis szükséges")
    index = (len(ordered) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def paired_summary(rows, bootstrap_seed, resamples=20000):
    """Blokkmediánok átlagos páros különbsége, páronkénti bootstrap CI."""
    groups = {}
    for row in rows:
        if row["warmup"]:
            continue
        groups.setdefault(row["pair"], {}).setdefault(row["condition"], []).append(
            row["observed_consistency_ms"])
    if len(groups) < 2 or any(set(g) != {"paused", "running_4x"} for g in groups.values()):
        raise ValueError("Legalább két teljes feltételpár szükséges")
    pairs = [{"pair": key,
              "paused_median_ms": statistics.median(value["paused"]),
              "running_4x_median_ms": statistics.median(value["running_4x"])}
             for key, value in sorted(groups.items())]
    differences = [p["running_4x_median_ms"] - p["paused_median_ms"] for p in pairs]
    rng = random.Random(bootstrap_seed)
    n = len(differences)
    boot = sorted(statistics.mean(rng.choices(differences, k=n)) for _ in range(resamples))
    return {"unit": "ms", "analysis_unit": "fresh_application_pair",
            "pair_count": n, "pairs": pairs,
            "mean_paired_median_difference_ms": statistics.mean(differences),
            "bootstrap_percentile_95_ci_ms": [quantile(boot, .025), quantile(boot, .975)],
            "bootstrap_resamples": resamples, "bootstrap_seed": bootstrap_seed,
            "within_pair_difference_sample_sd_ms": statistics.stdev(differences)}


def describe_conditions(rows):
    result = {}
    for condition in ("paused", "running_4x"):
        group = [r for r in rows if r["condition"] == condition and not r["warmup"]]
        vals = [r["observed_consistency_ms"] for r in group]
        result[condition] = {
            "operation_count": len(vals), "median_ms": statistics.median(vals),
            "p95_ms": quantile(vals, .95), "maximum_ms": max(vals),
            "mqtt_request_to_observer_ack_median_ms": statistics.median(
                r["mqtt_request_to_observer_ack_ms"] for r in group),
            "maximum_observed_poll_interval_ms": max(r["max_poll_interval_ms"] for r in group)}
    return result


def consistent(state, request_id, placement_id, operation):
    ack = state.get("parking_setup") or {}
    expected = "parked_spawned" if operation == "spawn" else "parked_removed"
    if not (ack.get("request_id") == request_id and ack.get("placement_id") == placement_id
            and ack.get("operation") == operation and ack.get("kind") == expected):
        return False
    world = state.get("world", {})
    slots = [next((s for s in area.get("slots", []) if s.get("id") == "P08"), {})
             for area in (world, state.get("coordinator", {}))]
    cars = [c for c in world.get("parked_cars", []) if c.get("slot_id") == "P08"]
    if operation == "spawn":
        return (all(s.get("state") == "occupied" and s.get("placement_id") == placement_id
                    for s in slots)
                and len(cars) == 1 and cars[0].get("placement_id") == placement_id)
    return all(s.get("state") == "free" for s in slots) and not cars


def wait_ready(app, running):
    deadline = time.perf_counter() + TIMEOUT_S
    while time.perf_counter() < deadline:
        state = app.state()
        if state.get("error"):
            raise RuntimeError(state["error"])
        if (state.get("ready") and state.get("connected") and state.get("world") and state.get("coordinator")
                and (not running or state.get("sim_time", 0) >= .5)):
            return state
        time.sleep(POLL_S)
    raise TimeoutError("Nem lett kész a saját mérési alkalmazás")


def measure_operation(app, pair, condition, cycle, operation, placement_id, warmup):
    request_id = placement_id if operation == "spawn" else placement_id + "-remove"
    data = {"action": "spawn_parked" if operation == "spawn" else "remove_parked",
            "slot_id": "P08", "request_id": request_id, "placement_id": placement_id,
            "run_id": app.run_id}
    started_ns = time.perf_counter_ns()
    if operation == "spawn":
        del data["placement_id"]
    app.command(data)
    enqueued_ns = time.perf_counter_ns()
    previous_poll_ns = started_ns
    max_poll_interval_ns = 0
    polls = 0
    while time.perf_counter_ns() - started_ns < TIMEOUT_S * 1e9:
        state = app.state()
        observed_ns = time.perf_counter_ns()
        max_poll_interval_ns = max(max_poll_interval_ns, observed_ns - previous_poll_ns)
        previous_poll_ns = observed_ns
        polls += 1
        if state.get("error"):
            raise RuntimeError(state["error"])
        if state.get("ready") and state.get("connected") and consistent(state, request_id, placement_id, operation):
            # The live widget intentionally retains only 120 records. A slow
            # operation may outlive that window under telemetry load. Read the
            # complete, line-buffered evidence AFTER stopping the timing clock.
            evidence = {}
            for record in app.export_records():
                if record["payload"].get("request_id") == request_id:
                    evidence[record["relative_topic"]] = record
            request = evidence.get("requests/parking_setup")
            ack = evidence.get("commands/parking_setup")
            if not request or not ack or ack["latency_ms"] is None:
                raise RuntimeError("A mért válaszhoz nem található a valódi MQTT kérés/ACK")
            ack_received_ns = ack["payload"]["sent_ns"] + ack["latency_ms"] * 1e6
            return {"pair": pair, "condition": condition, "cycle": cycle,
                    "operation": operation, "warmup": warmup, "run_id": app.run_id,
                    "request_id": request_id, "placement_id": placement_id,
                    "start_perf_counter_ns": started_ns,
                    "enqueue_return_perf_counter_ns": enqueued_ns,
                    "first_successful_poll_perf_counter_ns": observed_ns,
                    "observed_consistency_ms": (observed_ns - started_ns) / 1e6,
                    "mqtt_request_to_observer_ack_ms":
                    (ack_received_ns - request["payload"]["sent_ns"]) / 1e6,
                    "poll_count": polls, "max_poll_interval_ms": max_poll_interval_ns / 1e6,
                    "model_time_s": state["sim_time"],
                    "mqtt_received": state["mqtt"]["received"],
                    "mqtt_invalid": state["mqtt"]["invalid"],
                    "mqtt_request_record": request, "mqtt_ack_record": ack,
                    "checked_utc": utc_now()}
        time.sleep(POLL_S)
    raise TimeoutError(f"A mérés nem teljesült; nincs sikeresként beszámítva: {request_id}")


def run_condition(pair, condition, cycles, directory, config, output):
    condition_dir = directory / "raw" / f"pair-{pair:02d}" / condition
    app = Application(config, condition_dir, mqtt_port=free_port(), initially_paused=True)
    metadata = {"pair": pair, "condition": condition, "started_utc": utc_now()}
    rows = []
    try:
        app.start()
        wait_ready(app, False)
        if condition == "running_4x":
            app.command({"action": "speed", "value": 4, "run_id": app.run_id})
            app.command({"action": "resume", "run_id": app.run_id})
        wait_ready(app, condition == "running_4x")
        metadata.update(run_id=app.run_id, mqtt_port=app.mqtt_port)
        for cycle in range(cycles + 1):
            placement = f"campaign-{pair:02d}-{condition}-{cycle:02d}"
            for operation in ("spawn", "remove"):
                row = measure_operation(app, pair, condition, cycle, operation, placement,
                                        warmup=cycle == 0)
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                output.flush()
                rows.append(row)
        state = app.state()
        if not state["paused"] and state["world"]["metrics"]["completed"]:
            raise RuntimeError("A terhelési ablakban az autó már befejezte az útját")
        metadata.update(finished_utc=utc_now(), state_error=state["error"],
                        sim_time_s=state["sim_time"], invalid_messages=state["mqtt"]["invalid"],
                        observed_messages=state["mqtt"]["received"],
                        measured_operations=2 * cycles)
    finally:
        app.close()
        if getattr(app.broker, "thread", None) and app.broker.thread.is_alive():
            raise RuntimeError("A mérés MQTT-közvetítője nem állt le")
    return rows, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 2 <= args.pairs <= 100 or not 1 <= args.cycles <= 30:
        parser.error("2..100 pár és 1..30 mért ciklus szükséges")
    logging.basicConfig(level=logging.ERROR)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = args.output or ROOT / "results" / "campaign" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if config.get("slot_count") != 8:
        parser.error("A rögzített vizsgálat nyolc helyű alapkonfigurációt igényel")
    rng = random.Random(args.seed)
    order = []
    for pair in range(1, args.pairs + 1):
        conditions = ["paused", "running_4x"]
        rng.shuffle(conditions)
        order.append({"pair": pair, "conditions": conditions})
    before = source_hashes()
    plan = {"schema": 1, "written_before_run_utc": utc_now(), "status": "predefined",
            "question": "Változik-e a MQTT-kezelőút megfigyelt konzisztens válaszideje a futó telemetriaterhelés mellett?",
            "primary_metric": "control enqueue attempt to first observer-derived consistent state poll; ms, perf_counter_ns",
            "baseline": "paused model, normal 5 Hz heartbeat and 4 Hz coordinator publication",
            "treatment": "same config, running at 4x model time, normal model telemetry",
            "analysis_unit": "fresh application pair, randomized condition order",
            "estimator": "mean of paired differences of within-condition operation medians; running_4x - paused",
            "interval": "95% percentile bootstrap, resample complete paired differences, 20000 resamples",
            "pair_count": args.pairs, "cycles_per_condition": args.cycles,
            "warmup_cycles_per_condition": 1, "operations_per_cycle": ["spawn_P08", "remove_P08"],
            "count_rationale": "30 independently reinitialized pairs is a bounded exploratory campaign; no guaranteed power or precision is asserted",
            "failure_rule": "5 s timeout, rejected control, invalid or completed-load state aborts entire campaign; do not silently drop failed operations",
            "poll_interval_requested_s": POLL_S, "timeout_s": TIMEOUT_S,
            "method_revision": "20 ms requested polling and short blocks; require fresh connected state at endpoint",
            "startup": "excluded; wait for all observed state and heartbeat; one spawn/remove warmup cycle",
            "evidence_lookup": "full durable MQTT JSONL after timing endpoint; never the 120-record live widget cache",
            "seed": args.seed, "order": order, "config": config,
            "source_sha256": before,
            "environment": {"python": sys.version, "platform": platform.platform(),
                            "amqtt": importlib.metadata.version("amqtt"),
                            "paho-mqtt": importlib.metadata.version("paho-mqtt"),
                            "clock": vars(time.get_clock_info("perf_counter"))},
            "limitations": ["One host and one session; not representative of a factory network",
                            "Applications are reinitialized; temporal dependence between pairs can remain",
                            "Polling and OS scheduling are included; this is not wire-only delay or a worst-case bound",
                            "No HTTP/browser render time is measured",
                            "Model time and actual elapsed time are distinct; no physical stopping safety claim"]}
    (directory / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    rows, runs = [], []
    try:
        with (directory / "operations.jsonl").open("w", encoding="utf-8", buffering=1) as output:
            for item in order:
                for condition in item["conditions"]:
                    measured, metadata = run_condition(item["pair"], condition, args.cycles,
                                                       directory, config, output)
                    rows.extend(measured)
                    runs.append(metadata)
                    print(f"pair {item['pair']:02d}/{args.pairs} {condition}: {metadata['measured_operations']} operations", flush=True)
        if source_hashes() != before:
            raise RuntimeError("A forrás a mérés közben megváltozott")
        summary = paired_summary(rows, args.seed + 1)
        summary.update(status="completed", completed_utc=utc_now(), source_sha256=before,
                       plan_sha256=digest(directory / "plan.json"), runs=runs,
                       measured_operations=sum(not r["warmup"] for r in rows),
                       warmup_operations=sum(r["warmup"] for r in rows))
        summary["descriptive_by_condition"] = describe_conditions(rows)
        (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        (directory / "failure.json").write_text(json.dumps({"status": "failed", "error": repr(error),
            "at_utc": utc_now(), "partial_rows": sum(1 for _ in (directory / "operations.jsonl").open(encoding="utf-8"))},
            ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        files = {p.relative_to(directory).as_posix(): digest(p)
                 for p in sorted(directory.rglob("*")) if p.is_file() and p.name != "manifest.json"}
        (directory / "manifest.json").write_text(json.dumps({"files_sha256": files}, indent=2), encoding="utf-8")
    print(json.dumps({"directory": str(directory), "paired_difference_ms":
                      summary["mean_paired_median_difference_ms"], "ci":
                      summary["bootstrap_percentile_95_ci_ms"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
