"""Export chapter QA stills from the same received snapshots as the video."""
import argparse
import hashlib
import json
from pathlib import Path

from video_render import render, title_image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    args = parser.parse_args()
    frames = [json.loads(line) for line in (args.capture / "observer-frames.jsonl").read_text(encoding="utf-8").splitlines()]
    folder = args.capture / "stills"
    folder.mkdir(exist_ok=True)
    title_image().save(folder / "00-intro.png")
    title_image(True).save(folder / "10-outro.png")
    receipt = {}
    for index in range(1, 10):
        chapter = [f for f in frames if f["phase"].startswith(f"{index}  ")]
        if not chapter:
            raise AssertionError(f"Missing chapter {index}")
        chosen = chapter[len(chapter) * 2 // 3]
        path = folder / f"{index:02d}-scene.png"
        render(chosen, chosen["capture_elapsed_s"], frames[-1]["capture_elapsed_s"]).save(path)
        receipt[path.name] = {"capture_elapsed_s": chosen["capture_elapsed_s"], "run_id": chosen["state"]["run_id"],
                              "world_seq": chosen["state"]["world"]["seq"], "phase": chosen["phase"],
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (folder / "index.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "stills": len(receipt) + 2, "folder": str(folder)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
