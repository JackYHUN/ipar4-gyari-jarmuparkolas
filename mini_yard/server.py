"""A helyi műszerfal kis HTTP-kiszolgálója, keretrendszer nélkül."""
import csv
import io
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .protocol import strict_json
from .runtime import ParkingSetupConflict, RuntimeUnavailable


HTTP_CONNECTION_LIMIT = 16
HTTP_IO_TIMEOUT_S = 5
HTTP_HEADER_TIMEOUT_S = 5
HTTP_BODY_TIMEOUT_S = 3


class _RequestInput(io.RawIOBase):
    """Keep a single read buffer while bounding the request line and headers."""

    def __init__(self, connection):
        super().__init__()
        self.connection = connection
        self.header_deadline = None

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.header_deadline is not None:
            remaining = self.header_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("HTTP request header deadline expired")
            self.connection.settimeout(remaining)
        return self.connection.recv_into(buffer)


class _LocalHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler):
        self._request_slots = threading.BoundedSemaphore(HTTP_CONNECTION_LIMIT)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            try:
                request.setblocking(False)
                request.send(b"HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def make_server(app, port):
    class Handler(BaseHTTPRequestHandler):
        timeout = HTTP_IO_TIMEOUT_S
        rbufsize = 0

        def setup(self):
            super().setup()
            self.rfile.close()
            self.request_input = _RequestInput(self.connection)
            self.rfile = io.BufferedReader(self.request_input)

        def handle_one_request(self):
            self.request_input.header_deadline = time.monotonic() + HTTP_HEADER_TIMEOUT_S
            super().handle_one_request()

        def log_message(self, *args):
            pass

        def parse_request(self):
            parsed = super().parse_request()
            self.request_input.header_deadline = None
            self.connection.settimeout(self.timeout)
            if not parsed:
                return False
            host = self.headers.get_all("Host", [])
            port = self.server.server_port
            authorities = {f"{name}:{port}" for name in ("127.0.0.1", "localhost")}
            if port == 80:
                authorities.update(("127.0.0.1", "localhost"))
            # Read the raw target because BaseHTTPRequestHandler normalizes "//".
            target = self.requestline.split()[1]
            authority = host[0].strip(" \t").lower() if len(host) == 1 else None
            if (authority not in authorities or not target.startswith("/")
                    or target.startswith("//") or "\\" in target):
                self.send(403, '{"error":"Érvénytelen helyi cím"}')
                return False
            self.authority = authority
            return True

        def headers_for(self, status, kind, filename=None, length=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            if length is not None:
                self.send_header("Content-Length", str(length))
            else:
                self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()

        def send(self, status, content, kind="application/json; charset=utf-8", filename=None):
            body = content.encode("utf-8") if isinstance(content, str) else content
            try:
                self.headers_for(status, kind, filename, len(body))
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def stream(self, chunks, kind, filename):
            self.close_connection = True
            try:
                self.headers_for(200, kind, filename)
                for chunk in chunks:
                    self.wfile.write(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def export(self, kind):
            run_id, path, size = app.export_source()
            if kind == "messages":
                def chunks():
                    with path.open("rb") as handle:
                        remaining = size
                        while remaining and (data := handle.read(min(remaining, 65536))):
                            remaining -= len(data)
                            yield data
                return self.stream(chunks(), "application/x-ndjson; charset=utf-8", f"{run_id}-mqtt.jsonl")
            records = app.export_records(path, size)
            if kind == "events":
                def rows():
                    prefix = "["
                    for row in records:
                        if row["relative_topic"] in ("events", "operator/control", "operator/control_event"):
                            yield prefix + json.dumps(row, ensure_ascii=False, allow_nan=False)
                            prefix = ",\n"
                    yield "[]" if prefix == "[" else "]"
                return self.stream(rows(), "application/json; charset=utf-8", f"{run_id}-events.json")
            return self.stream(telemetry_csv(records), "text/csv; charset=utf-8", f"{run_id}-telemetry.csv")

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/api/state":
                return self.send(200, json.dumps(app.state(), ensure_ascii=False, allow_nan=False))
            if url.path == "/api/export":
                kind = parse_qs(url.query).get("kind", ["messages"])[0]
                if kind not in ("messages", "telemetry", "events"):
                    return self.send(400, '{"error":"Ismeretlen export"}')
                return self.export(kind)
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                     "/style.css": ("style.css", "text/css")}
            if url.path in files:
                name, mime = files[url.path]
                return self.send(200, (app.root / "web" / name).read_bytes(), mime + "; charset=utf-8")
            self.send(404, '{"error":"Nincs ilyen oldal"}')

        def read_control_body(self, length):
            deadline = time.monotonic() + HTTP_BODY_TIMEOUT_S
            body = bytearray()
            try:
                while len(body) < length:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    self.connection.settimeout(remaining)
                    chunk = self.rfile.read1(length - len(body))
                    if not chunk:
                        raise ValueError("Hiányos kéréstörzs")
                    body.extend(chunk)
            finally:
                self.connection.settimeout(self.timeout)
            return body

        def do_POST(self):
            if urlsplit(self.path).path != "/api/control":
                return self.send(404, '{"error":"Nincs ilyen művelet"}')
            # Host has already been checked against the listening socket.
            origins = self.headers.get_all("Origin", [])
            origin = f"http://{self.authority.removesuffix(':80') if self.server.server_port == 80 else self.authority}"
            if origins not in ([], [origin]):
                return self.send(403, '{"error":"Eltérő eredetű kérés"}')
            if self.headers.get_content_type() != "application/json":
                return self.send(415, '{"error":"application/json szükséges"}')
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("Hibás kérésméret")
                data = strict_json(self.read_control_body(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON objektum szükséges")
                result = app.command(data)
                self.send(202, json.dumps({"ok": True, "queued": True, **(result or {})}))
            except RuntimeUnavailable as error:
                self.send(503, json.dumps({"error": str(error)}, ensure_ascii=False))
            except ParkingSetupConflict as error:
                self.send(409, json.dumps({"error": str(error)}, ensure_ascii=False))
            except TimeoutError:
                self.close_connection = True
                self.send(408, '{"error":"Kéréstörzs olvasási időtúllépés"}')
            except (ValueError, TypeError, RecursionError) as error:
                self.send(400, json.dumps({"error": str(error)}, ensure_ascii=False))

    return _LocalHTTPServer(("127.0.0.1", port), Handler)


def safe_csv_cell(value):
    if isinstance(value, str):
        leading = re.sub(r"^[\s\ufeff]+", "", value)
        if value.startswith(("\t", "\r", "\n")) or leading.startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠")):
            return "'" + value
    return value


def telemetry_csv(records):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["received_utc", "run_id", "seq", "sim_time_s", "car_id", "state", "x_m", "y_m",
                     "speed_mps", "distance_m", "slot_id", "sensor_valid", "pedestrian", "reason",
                     "stop_pedestrian_s", "stop_sensor_failure_s", "stop_communication_s"])
    yield "\ufeff" + stream.getvalue()
    for record in records:
        if not record["relative_topic"].startswith("telemetry/"):
            continue
        stream.seek(0)
        stream.truncate()
        p = record["payload"]
        sensor, stops = p.get("sensor", {}), p.get("stop_time_s", {})
        row = [record["received_utc"], *(p.get(k) for k in (
            "run_id", "seq", "sim_time", "car_id", "state", "x", "y", "speed", "distance_m", "slot_id")),
            sensor.get("valid"), sensor.get("pedestrian"), p.get("reason"), stops.get("pedestrian"),
            stops.get("sensor_failure"), stops.get("communication")]
        writer.writerow(map(safe_csv_cell, row))
        yield stream.getvalue()
