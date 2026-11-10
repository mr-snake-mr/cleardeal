"""Narration for the demo video, spoken by Gnani TTS (Timbre v2.5, English).

Two jobs, kept separate so a re-take only repeats the second:

    python demo/narrate.py --speak      # synthesise segments into demo/audio/
    python demo/narrate.py --mux marks.json
                                        # place them on the video at the marks

The key is read from `demo/.gnani-key` and never printed. It is gitignored.

`marks.json` is `{"seg_1": 3.4, "seg_2": 21.0, ...}` — seconds from the start of
the recording — produced from the `MARK` lines the recorder prints. Placing
speech by hand is how a demo ends up narrating the wrong step.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
AUDIO = HERE / "audio"
VIDEO = HERE / "cleardeal-demo.webm"
FINAL = HERE / "cleardeal-demo.mp4"
KEY_FILE = HERE / ".gnani-key"

API = "https://api.vachana.ai/api/v1/tts/inference"
VOICE = "Devika"  # en-IN, professional: a demo is a briefing, not a trailer
MODEL = "timbre-v2.5"

# One line per beat: what is happening on screen, and what to expect next. The
# video is a walkthrough, so the narration narrates rather than advertises.
SEGMENTS = {
    "seg_1": "This is ClearDeal. A client asks for six pages when the agreement says five. Watch what happens at each step, and what to expect.",
    "seg_2": "First the brief becomes requirements. Expect a table where every line is marked explicit or inferred, and every explicit one quotes the sentence that justifies it.",
    "seg_3": "Next, the draft. Expect milestones that must equal the budget to the cent. If they miss, the draft is discarded and nothing is written.",
    "seg_4": "Then the signatures. Expect the second one to need a second person: this link carries the client's own session, never yours.",
    "seg_5": "Now the real test, a new request. Expect one of three verdicts, and a citation that resolves to a real section, or an honest cannot.",
    "seg_7": "Finally, payment. The merchant connects once, and the secret is never returned. The amount comes from the milestone row, and PayPal must prove the capture before anything is marked paid.",
    "seg_6": "That is the product. Every claim about scope is checked, not believed.",
}


def speak() -> int:
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    AUDIO.mkdir(exist_ok=True)
    if not key:
        print("no key in demo/.gnani-key")
        return 1

    failures = 0
    for name, text in SEGMENTS.items():
        target = AUDIO / f"{name}.mp3"
        if target.exists() and target.stat().st_size > 1000:
            print(f"skip {name} (exists)")
            continue
        for attempt in (1, 2, 3):
            response = httpx.post(
                API,
                headers={"Content-Type": "application/json", "X-API-Key-ID": key},
                json={
                    "text": text,
                    "voice": VOICE,
                    "model": MODEL,
                    "language": "en-IN",
                    "speed": 1.0,
                    "audio_config": {"container": "mp3", "bitrate": "128k", "sample_rate": 48000},
                },
                timeout=90,
            )
            if response.status_code == 200 and len(response.content) > 1000:
                target.write_bytes(response.content)
                print(f"ok   {name}  {len(response.content) // 1024} KB  {text[:42]}...")
                break
            # 429 = one active request at a time; the docs have no Retry-After.
            print(f"retry {name} ({attempt}): {response.status_code} {response.text[:120]}")
            time.sleep(2 * attempt)
        else:
            failures += 1
    return 1 if failures else 0


def mux(marks_path: Path) -> int:
    marks = json.loads(marks_path.read_text(encoding="utf-8"))
    missing = [name for name in SEGMENTS if name not in marks]
    if missing:
        print(f"marks missing for: {', '.join(missing)}")
        return 1

    inputs: list[str] = ["-i", str(VIDEO)]
    filters: list[str] = []
    labels: list[str] = []
    for index, name in enumerate(SEGMENTS, start=1):
        clip = AUDIO / f"{name}.mp3"
        if not clip.exists():
            print(f"no audio for {name}; run --speak first")
            return 1
        inputs += ["-i", str(clip)]
        delay = int(marks[name] * 1000)
        filters.append(f"[{index}:a]adelay={delay}|{delay}[a{index}]")
        labels.append(f"[a{index}]")

    graph = ";".join(filters) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:normalize=0[aout]"
    command = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", graph,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        str(FINAL),
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stderr[-1200:])
        return result.returncode
    print("VIDEO:", FINAL)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speak", action="store_true")
    parser.add_argument("--mux", type=Path)
    args = parser.parse_args()

    if args.speak:
        return speak()
    if args.mux:
        return mux(args.mux)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
