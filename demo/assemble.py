"""Cut the two takes into one video, place the narration, enforce the limit.

    python demo/assemble.py

Reads `demo/out/page1.webm` (freelancer), `demo/out/page2.webm` (client),
`demo/out/marks.json` and `demo/audio/seg_*.mp3`, and writes
`demo/cleardeal-demo.mp4`.

The three cuts are chosen by the marks, not by eye:

  A  freelancer, start -> the moment the client's page takes over
  B  client,   landing -> APPROVED
  C  freelancer, the scope check -> the verdict

Under three minutes is the submission's rule, so the assembled file is probed
and the script fails if it is not.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
AUDIO = HERE / "audio"
FINAL = HERE / "cleardeal-demo.mp4"

LIMIT = 180.0

# Which narration belongs to which beat, as a key into its own page's marks.
NARRATION = {
    "seg_1": ("page1", "start", 0.4),
    "seg_2": ("page1", "project", 0.2),
    "seg_3": ("page1", "draft", 0.2),
    "seg_4": ("page1", "invite", 0.2),
    "seg_5": ("page1", "scope check", 0.3),
    "seg_7": ("page1", "payment", 0.3),
    "seg_6": ("page1", "end", -3.0),
}


def probe(path: Path) -> float:
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    try:
        return float(out.stdout.strip())
    except ValueError:
        return -1.0


def main() -> int:
    marks = json.loads((OUT / "marks.json").read_text(encoding="utf-8"))
    page1 = marks["page1"]
    page2 = marks["page2"]

    # Bounds of each cut, in the source recordings.
    a_end = page1["scope check"] - 0.4
    b_start = page2["client view"] - 0.3
    b_end = page2["approved"] + 1.4
    c_start = page1["scope check"]

    a_len = a_end
    b_len = b_end - b_start
    c_len = probe(OUT / "page1.webm") - c_start

    graph = [
        f"[0:v]trim=0:{a_end:.2f},setpts=PTS-STARTPTS[v0]",
        f"[1:v]trim={b_start:.2f}:{b_end:.2f},setpts=PTS-STARTPTS[v1]",
        f"[0:v]trim={c_start:.2f}:{probe(OUT / 'page1.webm'):.2f},setpts=PTS-STARTPTS[v2]",
        "[v0][v1][v2]concat=n=3:v=1:a=0[vout]",
    ]

    offsets = {"page1": 0.0, "page2": a_len}

    def wanted_start(page: str, key: str, delta: float) -> float:
        """Where the beat lands on the assembled timeline."""
        second = marks[page][key] + delta
        if page == "page2":
            return max(0.0, second + offsets["page2"])
        return max(0.0, second) if second < c_start else a_len + b_len + (second - c_start)

    # Lay the narration out in one pass, in time order, and never let a line
    # start before the previous one has finished. A beat can arrive early — a
    # fast take, a short wait — and two voices over each other is the fastest
    # way to make a demo sound unedited.
    schedule: list[tuple[str, float, float]] = []
    cursor = 0.0
    for name, (page, key, delta) in sorted(
        NARRATION.items(), key=lambda item: wanted_start(*item[1])
    ):
        clip = AUDIO / f"{name}.mp3"
        if not clip.exists():
            print(f"missing narration: {clip}")
            return 1
        length = probe(clip)
        start = max(wanted_start(page, key, delta), cursor)
        schedule.append((name, start, length))
        cursor = start + length + 0.25

    for (name, start, length), (next_name, next_start, _) in zip(schedule, schedule[1:]):
        if start + length > next_start:
            print(f"FAIL: {name} still overlaps {next_name}")
            return 1

    inputs = ["-i", str(OUT / "page1.webm"), "-i", str(OUT / "page2.webm")]
    for index, (name, start, _) in enumerate(schedule, start=2):
        inputs += ["-i", str(AUDIO / f"{name}.mp3")]
        delay = max(0, int(round(start * 1000)))
        graph.append(f"[{index}:a]adelay={delay}|{delay}[a{index - 1}]")

    print(
        "narration: "
        + ", ".join(f"{n}@{s:.1f}s+{d:.1f}" for n, s, d in schedule)
        + f" - {len(schedule)} clips, no overlap"
    )

    mix = "".join(f"[a{i}]" for i in range(1, len(NARRATION) + 1))
    graph.append(f"{mix}amix=inputs={len(NARRATION)}:normalize=0[aout]")

    command = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "22", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(FINAL),
    ]  # fmt: skip
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    if run.returncode != 0:
        print(run.stderr[-1500:])
        return run.returncode

    seconds = probe(FINAL)
    print(f"VIDEO: {FINAL}  {seconds:.1f}s  (A={a_len:.1f} B={b_len:.1f} C={c_len:.1f})")
    if seconds >= LIMIT:
        print(f"FAIL: {seconds:.1f}s is not under {LIMIT:.0f}s")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
