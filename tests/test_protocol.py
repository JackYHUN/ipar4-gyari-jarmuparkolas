import csv
from email.message import Message
from http.server import ThreadingHTTPServer
import io
import threading
import unittest
from unittest.mock import Mock, patch

from mini_yard.protocol import strict_json
from mini_yard.server import HTTP_BODY_TIMEOUT_S, _LocalHTTPServer, make_server, telemetry_csv


class ExportTests(unittest.TestCase):
    def test_csv_keeps_text_literal_and_numbers_numeric(self):
        row = {
            "relative_topic": "telemetry/CAR01",
            "received_utc": "2026-11-11T08:00:00Z",
            "payload": {"run_id": "r1", "car_id": "CAR01", "state": " \u2003=1+1",
                        "reason": "\ufeff @example", "x": -1.25, "slot_id": "\tP01"},
        }
        text = "".join(telemetry_csv(iter([row])))
        parsed = next(csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline="")))
        self.assertEqual(parsed["state"], "' \u2003=1+1")
        self.assertEqual(parsed["reason"], "'\ufeff @example")
        self.assertEqual(parsed["slot_id"], "'\tP01")
        self.assertEqual(parsed["x_m"], "-1.25")
        self.assertEqual(parsed["stop_communication_s"], "")
        self.assertEqual(parsed["car_id"], "CAR01")

    def test_json_rejects_nonfinite_numbers_even_after_float_conversion(self):
        for number in ("NaN", "Infinity", "1e999"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                strict_json('{"nested":[%s]}' % number)
        self.assertEqual(strict_json('{"dt":0.05}')["dt"], 0.05)


class HttpResourceTests(unittest.TestCase):
    def test_control_body_deadline_returns_408_without_enqueuing_command(self):
        app = Mock()
        with patch("mini_yard.server._LocalHTTPServer") as server_factory:
            make_server(app, 0)
        handler_class = server_factory.call_args.args[1]
        handler = object.__new__(handler_class)
        handler.path = "/api/control"
        handler.authority = "localhost:8766"
        handler.server = Mock(server_port=8766)
        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = "20"
        handler.connection = Mock()
        handler.rfile = Mock()
        handler.rfile.read1.return_value = b"{"
        handler.send = Mock()
        with patch("mini_yard.server.time.monotonic", side_effect=[0, 0, HTTP_BODY_TIMEOUT_S]):
            handler.do_POST()
        handler.rfile.read1.assert_called_once_with(20)
        handler.send.assert_called_once()
        self.assertEqual(handler.send.call_args.args[0], 408)
        self.assertTrue(handler.close_connection)
        app.command.assert_not_called()

    def test_http_worker_limit_releases_slot_when_handler_ends(self):
        server = object.__new__(_LocalHTTPServer)
        server._request_slots = threading.BoundedSemaphore(1)
        server.shutdown_request = Mock()
        first, next_request = Mock(), Mock()
        address = ("127.0.0.1", 1)
        with (patch.object(ThreadingHTTPServer, "process_request") as start_worker,
              patch.object(ThreadingHTTPServer, "process_request_thread")):
            server.process_request(first, address)
            server.process_request(next_request, address)
            start_worker.assert_called_once_with(first, address)
            self.assertTrue(next_request.send.call_args.args[0].startswith(b"HTTP/1.0 503"))
            server.shutdown_request.assert_called_once_with(next_request)
            server.process_request_thread(first, address)
            server.process_request(next_request, address)
            self.assertEqual(start_worker.call_count, 2)
