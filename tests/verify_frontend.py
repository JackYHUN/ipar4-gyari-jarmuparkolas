"""Run production UI contract checks. Does not start or control a simulation.

The receipt includes hashes for the exact HTML, CSS and JavaScript checked.
Browser layout and visual inspection remain separate checks.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def source_hashes():
    paths = [ROOT / "web" / name for name in ("app.js", "index.html", "style.css")]
    paths += [Path(__file__), ROOT / "tests" / "frontend.cjs"]
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def main():
    before, started = source_hashes(), utc_now()
    node = shutil.which("node")
    if not node:
        print("Node.js is required for tests/frontend.cjs", file=sys.stderr)
        return 1
    completed = subprocess.run([node, str(ROOT / "tests" / "frontend.cjs")], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
    try:
        checks = json.loads(completed.stdout)
    except json.JSONDecodeError:
        checks = {"passed": False, "checks_run": 0, "stdout": completed.stdout,
                  "stderr": completed.stderr}
    after = source_hashes()
    passed = completed.returncode == 0 and checks.get("passed") is True and before == after
    report = {"kind": "production_frontend_contract_checks", "started_utc": started,
              "finished_utc": utc_now(), "passed": passed, **checks,
              "exit_code": completed.returncode, "source_unchanged_during_checks": before == after,
              "source_sha256_before": before, "source_sha256_after": after,
              "limitations": ["Actual production JavaScript with a minimal DOM; not a full browser.",
                              "No color, pixel-perfect layout, real MQTT or statistical validation.",
                              "AI-assisted check; not evidence of independent student execution."]}
    report["passed"] = passed
    target = ROOT / "results" / "ui" / "design-v2" / "frontend-check.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "checks_run": checks.get("checks_run", 0),
                      "receipt": str(target)}, ensure_ascii=False))
    for check in checks.get("checks", []):
        if not check["passed"]:
            print(f"FAIL: {check['name']}\n{check['error']}")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
