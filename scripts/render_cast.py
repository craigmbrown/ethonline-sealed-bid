#!/usr/bin/env python3
"""Render an asciinema v2 cast to an MP4 with burned-in captions.

    python3 scripts/render_cast.py evidence/demo.cast evidence/demo.mp4 [--captions docs/VIDEO-CAPTIONS.json]

Terminal emulation by `pyte`, frames drawn with Pillow, encoded by ffmpeg. Captions: a JSON list
of {"marker": "t=3", "text": "..."}; a caption becomes active when a line starting with
`### <marker>` is printed by the recording (see scripts/record_demo.sh) and stays until the next.
No screen, no browser — a reproducible artifact anyone can regenerate from the cast.

Narration (optional): `--narration evidence/narration` mixes one clip per marker (`t0.mp3` for
`t=0`, …) into the MP4, and holds each caption on screen at least as long as its clip. The clips
in the repo were generated from docs/VIDEO-CAPTIONS.json with a text-to-speech service; nothing
in them is measured evidence — the terminal is.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pyte
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
BG = (12, 12, 14)
FG = (220, 220, 220)
CAPTION_BG = (24, 32, 44)
CAPTION_FG = (255, 236, 160)
ANSI = {
    "black": (30, 30, 30), "red": (230, 80, 80), "green": (80, 220, 120), "brown": (230, 200, 90),
    "blue": (100, 150, 255), "magenta": (220, 120, 220), "cyan": (90, 210, 220), "white": (230, 230, 230),
}


def load_cast(path: Path):
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    events = [json.loads(l) for l in lines[1:] if l.strip()]
    return header, events


def wrap(text: str, width: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out[:3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cast")
    ap.add_argument("out")
    ap.add_argument("--captions", default=None)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--font-size", type=int, default=18)
    ap.add_argument("--max-idle", type=float, default=2.5, help="clamp pauses longer than this (seconds)")
    ap.add_argument("--caption-min", type=float, default=12.0, help="hold each caption on screen at least this long (seconds)")
    ap.add_argument("--narration", default=None, help="directory of <marker>.mp3 clips (t0.mp3 for t=0); mixed into the MP4")
    ap.add_argument("--narration-pad", type=float, default=1.0, help="seconds of silence after each clip before the next caption may start")
    args = ap.parse_args()
    narration: dict[str, tuple[Path, float]] = {}
    if args.narration:
        for f in sorted(Path(args.narration).glob("t*.mp3")):
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                                       capture_output=True, text=True, check=True).stdout.strip())
            narration[f.stem[0] + "=" + f.stem[1:]] = (f, dur)

    header, events = load_cast(Path(args.cast))
    cols, rows = header.get("width", 120), header.get("height", 36)
    captions = {c["marker"]: c["text"] for c in json.loads(Path(args.captions).read_text())} if args.captions else {}

    font = ImageFont.truetype(FONT, args.font_size)
    font_b = ImageFont.truetype(FONT_BOLD, args.font_size)
    cw = font.getbbox("M")[2]
    ch = args.font_size + 6
    cap_rows = 3 if captions else 0
    W, H = cols * cw + 40, (rows + cap_rows) * ch + 60

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    current_caption = ""

    # clamp long pauses so the video stays tight, then stretch so every caption gets read
    timeline, t_prev, t_out = [], 0.0, 0.0
    for t, kind, data in events:
        dt = min(t - t_prev, args.max_idle)
        t_out += dt
        t_prev = t
        timeline.append((t_out, kind, data))
    if captions:
        is_marker = [kind == "o" and "\n### " in ("\n" + data) for _, kind, data in timeline]
        marks = [i for i, m in enumerate(is_marker) if m]
        def marker_of(data: str) -> str:
            for line in data.split("\n"):
                if line.startswith("### "):
                    return line[4:].split()[0]
            return ""
        for a, b in zip(marks, marks[1:] + [len(timeline)]):
            span = (timeline[b][0] if b < len(timeline) else timeline[-1][0] + 2.0) - timeline[a][0]
            need = args.caption_min
            if marker_of(timeline[a][2]) in narration:
                need = max(need, narration[marker_of(timeline[a][2])][1] + args.narration_pad)
            if span < need:
                shift = need - span
                timeline = timeline[:b] + [(t + shift, k, d) for t, k, d in timeline[b:]]
        last_need = args.caption_min
        if marks and marker_of(timeline[marks[-1]][2]) in narration:
            last_need = max(last_need, narration[marker_of(timeline[marks[-1]][2])][1] + args.narration_pad)
        timeline.append((timeline[-1][0] + max(0.0, last_need - 2.0), "o", ""))
        marker_times = {marker_of(timeline[i][2]): timeline[i][0] for i in marks}
    total = timeline[-1][0] + 2.0 if timeline else 1.0

    tmp = tempfile.mkdtemp(prefix="cast-")
    frame_i, n_frames, ev_i = 0, int(total * args.fps) + 1, 0
    print(f"rendering {n_frames} frames at {args.fps} fps ({total:.1f}s), {cols}x{rows} cells → {W}x{H}px")
    for frame_i in range(n_frames):
        t = frame_i / args.fps
        while ev_i < len(timeline) and timeline[ev_i][0] <= t:
            _, kind, data = timeline[ev_i]
            if kind == "o":
                stream.feed(data)
                for line in data.split("\n"):
                    if line.startswith("### "):
                        marker = line[4:].split()[0]
                        current_caption = captions.get(marker, "")
            ev_i += 1
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        for y in range(rows):
            line = screen.buffer[y]
            for x in range(cols):
                c = line[x]
                if c.data == " " and c.bg == "default":
                    continue
                fg = ANSI.get(c.fg, FG) if c.fg != "default" else FG
                d.text((20 + x * cw, 20 + y * ch), c.data, font=font_b if c.bold else font, fill=fg)
        if captions and current_caption:
            y0 = 20 + rows * ch + 10
            d.rectangle([10, y0, W - 10, H - 10], fill=CAPTION_BG)
            for i, ln in enumerate(wrap(current_caption, cols - 4)):
                d.text((30, y0 + 8 + i * ch), ln, font=font_b, fill=CAPTION_FG)
        img.save(f"{tmp}/f{frame_i:06d}.png")
        if frame_i % (args.fps * 10) == 0:
            print(f"  {t:6.1f}s / {total:.1f}s", file=sys.stderr)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i", f"{tmp}/f%06d.png"]
    if narration and captions:
        clips = [(marker_times[m], narration[m][0]) for m in sorted(narration) if m in marker_times]
        for _, f in clips:
            cmd += ["-i", str(f)]
        chains = [f"[{i + 1}:a]adelay={int(start * 1000)}|{int(start * 1000)}[a{i}]" for i, (start, _) in enumerate(clips)]
        mix = "".join(f"[a{i}]" for i in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0,apad[aout]"
        cmd += ["-filter_complex", ";".join(chains + [mix]), "-map", "0:v", "-map", "[aout]",
                "-c:a", "aac", "-b:a", "128k", "-shortest"]
        print(f"narration: {len(clips)} clips mixed at their caption offsets")
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-crf", "23", args.out]
    subprocess.run(cmd, check=True)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
