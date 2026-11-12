"""Nagy MQTT JSONL-napló sorszinten történő szakaszolása és ellenőrzése."""
import argparse
import hashlib
import json
from pathlib import Path


def segment(source, output, maximum):
    if maximum < 1:
        raise ValueError("Pozitív szakaszméret szükséges")
    output.mkdir(parents=True, exist_ok=False)
    original = hashlib.sha256()
    parts = []
    destination = None
    try:
        with source.open("rb") as stream:
            end = stream.seek(0, 2)
            stream.seek(0)
            while (remaining := end - stream.tell()) > 0:
                line = stream.readline(remaining)
                if not line.endswith(b"\n"):
                    break
                if destination is None or (part["bytes"] and part["bytes"] + len(line) > maximum):
                    if destination is not None:
                        destination.close()
                        part["sha256"] = digest.hexdigest()
                    name = f"mqtt-{len(parts) + 1:04d}.jsonl"
                    part = {"name": name, "bytes": 0, "records": 0}
                    parts.append(part)
                    destination = (output / name).open("xb")
                    digest = hashlib.sha256()
                original.update(line)
                digest.update(line)
                destination.write(line)
                part["records"] += 1
                part["bytes"] += len(line)
        if destination is not None:
            part["sha256"] = digest.hexdigest()
    finally:
        if destination is not None:
            destination.close()
    complete_bytes = sum(part["bytes"] for part in parts)
    manifest = {"source": source.name, "snapshot_bytes": end, "complete_bytes": complete_bytes,
                "skipped_incomplete_bytes": end - complete_bytes,
                "complete_records_sha256": original.hexdigest(), "parts": parts}
    rebuilt = hashlib.sha256()
    for part in parts:
        with (output / part["name"]).open("rb") as handle:
            while block := handle.read(65536):
                rebuilt.update(block)
    if rebuilt.hexdigest() != manifest["complete_records_sha256"]:
        raise OSError("A szakaszok visszaolvasása eltér az eredeti naplótól")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-mib", type=int, default=16)
    args = parser.parse_args()
    result = segment(args.log, args.output, args.max_mib * 1024**2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
