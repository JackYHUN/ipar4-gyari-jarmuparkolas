"""A helyi műszerfal kis HTTP-kiszolgálója, keretrendszer nélkül."""
import csv
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def make_server(app, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, content, kind="application/json; charset=utf-8", filename=None):
            body = content.encode("utf-8") if isinstance(content, str) else content
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/api/state":
                return self.send(200, json.dumps(app.state(), ensure_ascii=False))
            if url.path == "/api/export":
                kind = parse_qs(url.query).get("kind", ["messages"])[0]
                if kind not in ("messages", "telemetry", "events"):
                    return self.send(400, '{"error":"Ismeretlen export"}')
                records = app.export_records()
                run_id = records[0]["payload"]["run_id"] if records else app.state()["run_id"]
                if kind == "messages":
                    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
                    return self.send(200, body, "application/x-ndjson; charset=utf-8", f"{run_id}-mqtt.jsonl")
                if kind == "events":
                    rows = [r for r in records if r["relative_topic"] in ("events", "operator/control")]
                    return self.send(200, json.dumps(rows, ensure_ascii=False, indent=2), filename=f"{run_id}-events.json")
                stream = io.StringIO(newline="")
                writer = csv.writer(stream)
                writer.writerow(["received_utc", "run_id", "seq", "sim_time_s", "car_id", "state", "x_m", "y_m", "speed_mps", "distance_m", "slot_id", "sensor_valid", "pedestrian", "reason", "stop_pedestrian_s", "stop_sensor_failure_s", "stop_communication_s"])
                for record in records:
                    if record["relative_topic"].startswith("telemetry/"):
                        p = record["payload"]
                        sensor = p.get("sensor", {})
                        stops = p.get("stop_time_s", {})
                        writer.writerow([record["received_utc"], p.get("run_id"), p.get("seq"), p.get("sim_time"), p.get("car_id"), p.get("state"), p.get("x"), p.get("y"), p.get("speed"), p.get("distance_m"), p.get("slot_id"), sensor.get("valid"), sensor.get("pedestrian"), p.get("reason"), stops.get("pedestrian"), stops.get("sensor_failure"), stops.get("communication")])
                return self.send(200, "\ufeff" + stream.getvalue(), "text/csv; charset=utf-8", f"{run_id}-telemetry.csv")
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                     "/style.css": ("style.css", "text/css")}
            if url.path in files:
                name, mime = files[url.path]
                return self.send(200, (app.root / "web" / name).read_bytes(), mime + "; charset=utf-8")
            self.send(404, '{"error":"Nincs ilyen oldal"}')

        def do_POST(self):
            if urlsplit(self.path).path != "/api/control":
                return self.send(404, '{"error":"Nincs ilyen művelet"}')
            # Controls come from this local page only, not a foreign website.
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self.send(403, '{"error":"Eltérő eredetű kérés"}')
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("Hibás kérésméret")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON objektum szükséges")
                result = app.command(data)
                self.send(202, json.dumps({"ok": True, "queued": True, **(result or {})}))
            except (ValueError, TypeError) as error:
                self.send(400, json.dumps({"error": str(error)}, ensure_ascii=False))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
