"""Engine side: write the job files of the reverse-guide example and render with the engine argv."""

import subprocess
import sys
from pathlib import Path

WT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
FFMPEG = sys.argv[3]
sys.path[:0] = [str(WT / "services/video-postproduction-api"), str(WT / "packages/production-trace")]

from production_trace import render_manual_markdown
from tests.test_reverse_guide import build_example
from video_postproduction.render.ffmpeg import build_ffmpeg_argv

edl, plan, files, guide = build_example(WT)
for name, text in files.items():
    (OUT / name).write_text(text, encoding="utf-8")
(OUT / "MANUAL-STEPS.md").write_text(render_manual_markdown(guide), encoding="utf-8")
argv = build_ffmpeg_argv(
    edl, input_path=OUT / "studio-demo.mp4", output_path=OUT / "render.mp4",
    caption_path=OUT / "captions-burned.ass", executable=FFMPEG, plan=plan,
)
(OUT / "engine-argv.txt").write_text(" ".join(a if " " not in a else repr(a) for a in argv[1:]).replace(str(OUT) + "/", ""), encoding="utf-8")
subprocess.run(argv, check=True)
print("frames", plan.record_frames, [(c.source_in_frame, c.source_out_frame) for c in plan.clips])
