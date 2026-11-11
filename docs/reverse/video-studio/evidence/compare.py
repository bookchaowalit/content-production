"""Compare the engine render with the MLT render that followed the guide."""

import json
import re
import subprocess
import sys
from pathlib import Path

FFMPEG, WORK = sys.argv[1], Path(sys.argv[2])
ENGINE, MANUAL = WORK / "render.mp4", WORK / "melt.mp4"


def run(args: list[str]) -> str:
    return subprocess.run([FFMPEG, "-hide_banner", *args], capture_output=True, text=True, check=False).stderr


def frame_count(path: Path) -> int:
    out = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"],
                         capture_output=True, text=True, check=True).stdout
    return sum(1 for line in out.splitlines() if line and not line.startswith("#"))


def psnr(offset: int, crop: str) -> list[float]:
    """Per-frame Y PSNR of MANUAL frame n vs ENGINE frame n+offset on the cropped region."""

    stats = WORK / f"psnr{offset}.log"
    shift = f"trim=start_frame={offset}," if offset > 0 else ""
    pre = f"trim=start_frame={-offset}," if offset < 0 else ""
    run(["-i", str(ENGINE), "-i", str(MANUAL), "-filter_complex",
         (f"[0:v]{shift}setpts=PTS-STARTPTS,setsar=1,crop={crop},format=yuv420p[e];"
         f"[1:v]{pre}setpts=PTS-STARTPTS,setsar=1,crop={crop},format=yuv420p[m];[e][m]psnr=stats_file={stats}"),
         "-f", "null", "-"])
    return [float(m) if m != "inf" else 99.0 for m in re.findall(r"psnr_y:(\S+)", stats.read_text())]


def ink(path: Path) -> list[float]:
    """Mean luma of the bright caption ink in the caption band per frame (white text on colour)."""

    out = run(["-i", str(path), "-vf", "setsar=1,crop=1080:300:0:1560,lutyuv=y='if(gt(val,235),255,0)',signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"])
    return [float(v) for v in re.findall(r"YAVG=(\S+)", out)]


picture = "1080:1500:0:0"  # everything above the caption band
result = {
    "frames": {"engine": frame_count(ENGINE), "manual": frame_count(MANUAL)},
    "durations": {name: re.search(r"Duration: (\S+),", run(["-i", str(path)]))[1] for name, path in (("engine", ENGINE), ("manual", MANUAL))},
    "sar": {name: re.search(r"\[SAR (\S+) DAR", run(["-i", str(path)]))[1] for name, path in (("engine", ENGINE), ("manual", MANUAL))},
}
aligned = psnr(0, picture)
result["picture_psnr_y"] = {"min": round(min(aligned), 2), "mean": round(sum(aligned) / len(aligned), 2), "frames": len(aligned)}
for offset in (-1, 1):
    shifted = psnr(offset, picture)
    result[f"picture_psnr_y_offset_{offset:+d}"] = round(sum(shifted) / len(shifted), 2)
    # frame n of the manual render vs engine frame n+offset; aligned must win on every frame
    if offset > 0:
        pairs = zip(aligned[:-1], shifted[:-1])  # the trimmed stream is one frame shorter
    else:
        pairs = zip(aligned[1:], shifted[:-1])
    result[f"aligned_beats_offset_{offset:+d}_every_frame"] = all(a > b for a, b in pairs)
clips = [(0, 93), (93, 150), (150, 234)]
result["clip_edges_psnr_y"] = {f"{a}-{b - 1}": [round(aligned[a], 2), round(aligned[b - 1], 2)] for a, b in clips}
engine_ink, manual_ink = ink(ENGINE), ink(MANUAL)
on = lambda values: [i for i, v in enumerate(values) if v > 0.05]


def spans(frames: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for frame in frames:
        if out and frame == out[-1][1] + 1:
            out[-1][1] = frame
        else:
            out.append([frame, frame])
    return out


result["caption_on_frames"] = {"engine": spans(on(engine_ink)), "manual": spans(on(manual_ink))}
for name, path in (("engine", ENGINE), ("manual", MANUAL)):
    meter = run(["-i", str(path), "-map", "0:a", "-af", "ebur128", "-f", "null", "-"])
    result.setdefault("integrated_lufs", {})[name] = float(re.findall(r"I:\s+(-?\d+\.\d) LUFS", meter)[-1])
print(json.dumps(result, indent=2))
(WORK / "compare.json").write_text(json.dumps(result, indent=2) + "\n")
