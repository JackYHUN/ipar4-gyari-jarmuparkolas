"""MQTT-kampány: fájllenyomatok, rekordazonosság, időrend és statisztika."""
import argparse
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import random
import re

from .latency_campaign import ROOT, consistent, describe_conditions, digest, paired_summary


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def compare(actual, expected, field):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), field)
        for key, value in expected.items():
            compare(actual[key], value, f"{field}.{key}")
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), field)
        for i, value in enumerate(expected):
            compare(actual[i], value, f"{field}[{i}]")
    else:
        require(math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
                if type(expected) is float and type(actual) in (float, int)
                else type(actual) is type(expected) and actual == expected,
                f"Nem reprodukálható mező: {field}")


# The schema-1 measurement producer hashes exactly these source files.
REQUIRED_SOURCE_FILES = frozenset({
    "config.json", "requirements-lock.txt", "experiments/latency_campaign.py",
    "mini_yard/__init__.py", "mini_yard/__main__.py", "mini_yard/broker.py",
    "mini_yard/model.py", "mini_yard/mqtt_bus.py", "mini_yard/protocol.py",
    "mini_yard/runtime.py", "mini_yard/server.py",
})


def validate_source_manifest(source):
    require(isinstance(source, dict) and set(source) == REQUIRED_SOURCE_FILES
            and all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)
                    for h in source.values()), "Hiányos vagy hibás forráslenyomat-jegyzék")


def snapshot_series(records):
    series = {"world": [], "coordinator": []}
    for record in records:
        topic = record["relative_topic"]
        if topic in series:
            p = record["payload"]
            series[topic].append((p["sent_ns"] + record["latency_ms"] * 1e6, p))
    for topic, values in series.items():
        values.sort(key=lambda x: x[0])
        series[topic] = ([x[0] for x in values], [x[1] for x in values])
    return series


def endpoint_matches(series, row):
    state = {"parking_setup": row["mqtt_ack_record"]["payload"]}
    for topic, (times, payloads) in series.items():
        index = bisect_right(times, row["first_successful_poll_perf_counter_ns"] + 1) - 1
        state[topic] = payloads[index] if index >= 0 else {}
    return consistent(state, row["request_id"], row["placement_id"], row["operation"])


def verify(directory, historical=False):
    directory = Path(directory).resolve()
    files = read(directory / "manifest.json")["files_sha256"]
    require({"plan.json", "summary.json", "operations.jsonl"} <= files.keys(), "Hiányos fájljegyzék")
    for name, expected in files.items():
        path = (directory / name).resolve()
        require(path.is_relative_to(directory) and path.is_file() and digest(path) == expected,
                f"Hiányzó vagy megváltozott bizonyíték: {name}")
    require(not (directory / "failure.json").exists(), "Sikertelen kampány")
    plan, summary = read(directory / "plan.json"), read(directory / "summary.json")
    require(plan["schema"] == 1 and plan["status"] == "predefined" and summary["status"] == "completed"
            and summary["plan_sha256"] == digest(directory / "plan.json")
            and summary["source_sha256"] == plan["source_sha256"], "Terv és eredmény nem tartozik össze")
    validate_source_manifest(plan["source_sha256"])
    mismatches = []
    for name, expected in plan["source_sha256"].items():
        path = (ROOT / name).resolve()
        require(path.is_relative_to(ROOT), f"Érvénytelen forráshivatkozás: {name}")
        if not path.is_file() or digest(path) != expected:
            mismatches.append(name)
    require(historical or not mismatches, f"A jelenlegi forrás eltér a mérési verziótól: {mismatches}")
    pairs, cycles = plan["pair_count"], plan["cycles_per_condition"]
    require(2 <= pairs <= 100 and 1 <= cycles <= 30 and plan["warmup_cycles_per_condition"] == 1
            and plan["operations_per_cycle"] == ["spawn_P08", "remove_P08"], "Ismeretlen mérési terv")
    rng = random.Random(plan["seed"])
    order = []
    for pair in range(1, pairs + 1):
        conditions = ["paused", "running_4x"]
        rng.shuffle(conditions)
        order.append({"pair": pair, "conditions": conditions})
    compare(plan["order"], order, "plan.order")
    run_order = [(item["pair"], condition) for item in order for condition in item["conditions"]]
    rows = list(read_rows(directory / "operations.jsonl"))
    require(len(rows) == len(run_order) * (cycles + 1) * 2, "Hibás műveletszám")
    runs = summary["runs"]
    require([(r["pair"], r["condition"]) for r in runs] == run_order
            and len({r["run_id"] for r in runs}) == len(runs), "Hibás futássorrend vagy ismételt futás")
    require(len({r["request_id"] for r in rows}) == len(rows), "Nem egyedi kérésazonosítók")

    def utc(value):
        return datetime.fromisoformat(value)

    previous_utc = utc(plan["written_before_run_utc"])
    previous_ns = 0
    for index, run in enumerate(runs):
        run_id, pair, condition = run["run_id"], run["pair"], run["condition"]
        require(isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{10}", run_id), "Hibás futásazonosító")
        base = f"raw/pair-{pair:02d}/{condition}/results/{run_id}"
        require({f"{base}/run.json", f"{base}/mqtt.jsonl"} <= files.keys(), f"Hiányos futás: {run_id}")
        metadata = read(directory / base / "run.json")
        require(metadata["run_id"] == run_id and metadata["mqtt_port"] == run["mqtt_port"]
                and metadata["config"] == plan["config"] and run["measured_operations"] == cycles * 2
                and run["state_error"] is None and run["invalid_messages"] == 0, f"Hibás futásmetaadat: {run_id}")
        started, finished = utc(run["started_utc"]), utc(run["finished_utc"])
        require(previous_utc <= started <= utc(metadata["created_utc"]) <= finished,
                f"Hibás időrend: {run_id}")
        previous_utc = finished
        evidence = defaultdict(list)
        raw_records = list(read_rows(directory / base / "mqtt.jsonl"))
        series = snapshot_series(raw_records)
        for record in raw_records:
            if record["relative_topic"] in ("requests/parking_setup", "commands/parking_setup"):
                evidence[record["payload"]["message_id"]].append(record)
        size = (cycles + 1) * 2
        sample = rows[index * size:(index + 1) * size]
        require(run["observed_messages"] >= max(row["mqtt_received"] for row in sample)
                and run["sim_time_s"] >= max(row["model_time_s"] for row in sample)
                and (run["sim_time_s"] == 0 if condition == "paused" else run["sim_time_s"] > 0),
                f"Ellentmondó futásmetaadat: {run_id}")
        for offset, row in enumerate(sample):
            cycle, removal = divmod(offset, 2)
            operation = "remove" if removal else "spawn"
            placement = f"campaign-{pair:02d}-{condition}-{cycle:02d}"
            require(row["placement_id"] == placement
                    and row["request_id"] == (placement + "-remove" if removal else placement),
                    "A ciklus elhelyezési és eltávolítási azonosítója eltér")
            require((row["pair"], row["condition"], row["run_id"], row["cycle"], row["operation"])
                    == (pair, condition, run_id, cycle, operation) and row["warmup"] is (cycle == 0)
                    and started <= utc(row["checked_utc"]) <= finished, "Hibás műveletsorrend")
            request, ack = row["mqtt_request_record"], row["mqtt_ack_record"]
            for record, topic, producer in ((request, "requests/parking_setup", "vehicle"),
                                            (ack, "commands/parking_setup", "parking")):
                payload = record["payload"]
                require(record in evidence[payload["message_id"]]
                        and record["relative_topic"] == topic and record["topic"] == f"mini-yard/{run_id}/{topic}"
                        and payload["producer"] == producer and payload["run_id"] == run_id
                        and payload["request_id"] == row["request_id"] and payload["placement_id"] == row["placement_id"]
                        and payload["operation"] == operation and payload["slot_id"] == "P08"
                        and payload["car_id"] == "PARK-P08", f"Hibás MQTT-rekord: {row['request_id']}")
            require(ack["payload"]["kind"] == ("parked_removed" if removal else "parked_spawned")
                    and row["mqtt_invalid"] == 0 and row["poll_count"] > 0, "Sikertelen mért művelet")
            start, enqueued, end = (row[k] for k in ("start_perf_counter_ns", "enqueue_return_perf_counter_ns",
                                                    "first_successful_poll_perf_counter_ns"))
            request_ns, ack_ns = (record["payload"]["sent_ns"] for record in (request, ack))
            ack_received = ack_ns + ack["latency_ms"] * 1e6
            require(previous_ns <= start <= enqueued <= end and start <= request_ns <= ack_ns <= ack_received <= end + 1
                    and 0 <= row["max_poll_interval_ms"] <= (end - start) / 1e6,
                    f"Hibás monoton időrend: {row['request_id']}")
            require(endpoint_matches(series, row),
                    f"A nyers állapotminták nem támasztják alá a végpontot: {row['request_id']}")
            previous_ns = end
            compare(row["observed_consistency_ms"], (end - start) / 1e6, "observed_consistency_ms")
            compare(row["mqtt_request_to_observer_ack_ms"], (ack_received - request_ns) / 1e6,
                    "mqtt_request_to_observer_ack_ms")

    require(previous_utc <= utc(summary["completed_utc"]), "A kampány befejezési ideje nem érvényes")
    reanalysis = paired_summary(rows, plan["seed"] + 1)
    for field, expected in reanalysis.items():
        compare(summary[field], expected, field)
    compare(summary["descriptive_by_condition"], describe_conditions(rows), "descriptive_by_condition")
    measured = sum(not r["warmup"] for r in rows)
    compare(summary["measured_operations"], measured, "measured_operations")
    compare(summary["warmup_operations"], len(rows) - measured, "warmup_operations")
    return {"status": "verified", "hashed_files": len(files), "application_runs": len(runs),
            "operations_including_warmup": len(rows), "measured_operations": measured,
            "paired_analysis_units": pairs, "source_hashes_match": not mismatches,
            "different_current_files": mismatches, "raw_mqtt_records_match": True, "raw_endpoint_states_match": True,
            "statistics_reproduced": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--historical", action="store_true", help="Korábbi forrásverzió adatainak ellenőrzése")
    args = parser.parse_args()
    print(json.dumps(verify(args.directory, args.historical), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
