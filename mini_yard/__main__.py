"""Indítás: python -m mini_yard --paused"""
import argparse
import json
import logging
from pathlib import Path

from .runtime import Application
from .server import make_server


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Kis gyári parkolómodell valódi MQTT-adatúttal")
    parser.add_argument("--http-port", type=int, default=8766)
    parser.add_argument("--mqtt-port", type=int, default=19085)
    parser.add_argument("--config", type=Path, default=root / "config.json")
    parser.add_argument("--paused", action="store_true", help="Indulás megállított szimulációval")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    app = Application(json.loads(args.config.read_text(encoding="utf-8")), root, args.mqtt_port, args.paused)
    server = None
    try:
        app.start()
        server = make_server(app, args.http_port)
        print(f"Kis változat: http://127.0.0.1:{args.http_port}/ | MQTT: 127.0.0.1:{args.mqtt_port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        app.close()


if __name__ == "__main__":
    main()
