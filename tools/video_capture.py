"""Capture a fresh educational demo through the actual local MQTT observer.

No physics/network implementation is replaced. Controls use Application.command;
all rendered world/coordinator snapshots come from Application.state (MQTT
observer cache), and the original MQTT JSONL is retained alongside this index.
This is not a browser screen recording or a physical camera capture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from mini_yard.runtime import Application


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mqtt-port", type=int, default=19180)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.ERROR)
    config = json.loads((SOURCE / "config.json").read_text(encoding="utf-8"))
    app = Application(config, output, mqtt_port=args.mqtt_port, initially_paused=True)
    start = time.perf_counter()
    frames = []
    phase = "Előkészítés: fogadott MQTT-állapot"
    current = {}
    required = set()
    actions = []

    def sample():
        nonlocal current
        current = app.state()
        if current.get("error"):
            raise RuntimeError(current["error"])
        if current.get("world"):
            frames.append({"capture_elapsed_s": round(time.perf_counter() - start, 6),
                           "phase": phase, "state": {key: current.get(key) for key in
                           ("run_id", "sim_time", "speed", "paused", "connected", "world", "coordinator", "controls", "mqtt", "events", "parking_setup")}})
            required.update(car["state"] for car in current["world"].get("cars", []))
        return current

    def wait(predicate, timeout=20):
        limit = time.perf_counter() + timeout
        while time.perf_counter() < limit:
            if predicate(sample()):
                return current
            time.sleep(0.1)
        raise TimeoutError(phase)

    def hold(seconds):
        until = time.perf_counter() + seconds
        while time.perf_counter() < until:
            sample()
            time.sleep(0.1)

    def command(action, **values):
        app.command(dict(action=action, run_id=app.run_id, **values))
        actions.append({"elapsed_s": round(time.perf_counter() - start, 6), "action": action, **values})

    def has_state(state):
        return lambda s: any(c["state"] == state for c in s.get("world", {}).get("cars", []))

    def setup(slot, index):
        request = f"video-spawn-{index}"
        command("spawn_parked", slot_id=slot, request_id=request)
        wait(lambda s: (s.get("parking_setup") or {}).get("request_id") == request and
             any(c["slot_id"] == slot and c["placement_id"] == request for c in s.get("world", {}).get("parked_cars", [])), 8)
        return request

    run_ids = []
    try:
        app.start()
        wait(lambda s: s.get("ready") and s.get("connected"))
        run_ids.append(app.run_id)
        phase = "1  Már parkoló autók: a központ foglaltnak látja a helyet"
        setup("P01", "scene1-a")
        setup("P04", "scene1-b")
        hold(3)
        command("speed", value=4)
        command("pedestrian", active=True)
        command("resume")
        phase = "2  Gyalogos a zebrán: helyi érzékelés és megállás"
        wait(has_state("pedestrian_stop"), 10)
        hold(3)
        command("pedestrian", active=False)
        phase = "3  Továbbhaladás a szabad zebra után"
        wait(lambda s: any(c["id"] == "CAR01" and c["x"] > 38 and c["state"] == "driving" for c in s.get("world", {}).get("cars", [])), 10)
        command("sensor_failure", active=True)
        phase = "4  Hibás érzékelő: megállás és okonkénti időmérés"
        wait(has_state("sensor_stop"), 5)
        hold(3)
        command("sensor_failure", active=False)
        hold(1)
        command("link_failure", active=True)
        phase = "5  Életjelkimaradás: watchdog és kommunikációs megállás"
        wait(has_state("communication_stop"), 5)
        hold(3)
        command("link_failure", active=False)
        phase = "6  Helyreállás: CAR01 a lefoglalt hely felé halad"
        first = lambda s, states: any(c["id"] == "CAR01" and c["state"] in states
                                     for c in s.get("world", {}).get("cars", []))
        wait(lambda s: first(s, {"aligning"}), 18)
        command("speed", value=1)
        phase = "6a  CAR01: kis sebességű igazodás és beállás"
        wait(lambda s: first(s, {"parking_check", "parking_pending", "parked"}), 18)
        phase = "6b  CAR01: a véghelyzet ellenőrzése és a parkolás nyugtázása"
        wait(lambda s: first(s, {"parked"}), 5)
        hold(3)
        command("speed", value=4)
        phase = "6c  A további érkező autók is befejezik a parkolást"
        wait(lambda s: s.get("world", {}).get("metrics", {}).get("completed") == 3, 45)
        hold(3)
        command("reset")
        wait(lambda s: s.get("ready") and s.get("run_id") != run_ids[-1] and
             s.get("world", {}).get("metrics", {}).get("completed") == 0, 8)
        run_ids.append(app.run_id)
        phase = "7  Telített parkoló: engedély nélkül nincs indulás"
        placements = {}
        for i in range(1, 9):
            slot = f"P{i:02d}"
            placements[slot] = setup(slot, f"scene2-{i}")
        command("speed", value=4)
        command("resume")
        wait(lambda s: any(c["state"] == "requesting" and c["slot_id"] is None for c in s.get("world", {}).get("cars", [])), 8)
        hold(3)
        phase = "8  Kézi autó eltávolítása: MQTT-visszaigazolás, majd új foglalás"
        command("remove_parked", slot_id="P01", placement_id=placements["P01"], request_id="video-remove-scene2")
        wait(lambda s: any(c["slot_id"] == "P01" and c["state"] == "driving" for c in s.get("world", {}).get("cars", [])), 8)
        wait(lambda s: s.get("world", {}).get("metrics", {}).get("completed") == 1, 20)
        command("pause")
        phase = "9  Eredmény: a felszabadított helyen az érkező jármű parkol"
        hold(4)
        expected = {"waiting", "requesting", "driving", "pedestrian_stop", "sensor_stop", "communication_stop",
                    "aligning", "maneuvering", "parking_check", "parked"}
        if not expected <= required:
            raise AssertionError(f"Missing states: {expected-required}")
    finally:
        app.close()
    frame_path = output / "observer-frames.jsonl"
    frame_path.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in frames), encoding="utf-8")
    raw = {str(p.relative_to(output)): sha(p) for p in (output / "results").glob("*/*") if p.is_file()}
    receipt = {"status": "PASS", "created_utc": datetime.now(timezone.utc).isoformat(),
               "scope": "Fresh live local TCP/MQTT run; snapshots rendered later from observer cache. Not a browser or camera recording.",
               "config": config, "mqtt_port": args.mqtt_port, "run_ids": run_ids,
               "observed_states": sorted(required), "capture_duration_s": round(frames[-1]["capture_elapsed_s"], 6),
               "frame_count": len(frames), "actions": actions,
               "source_sha256": {p.relative_to(SOURCE).as_posix(): sha(p) for p in
                                 [*sorted((SOURCE / "mini_yard").glob("*.py")), SOURCE / "config.json", Path(__file__)]},
               "evidence_sha256": {"observer-frames.jsonl": sha(frame_path), **raw}}
    (output / "capture.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("status", "run_ids", "capture_duration_s", "frame_count", "observed_states")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
