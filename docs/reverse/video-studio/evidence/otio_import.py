"""Open the three interchange files with OpenTimelineIO (the library Resolve uses for .otio import)."""

import json
import sys
from pathlib import Path

import opentimelineio as otio

work = Path(sys.argv[1])
expected = [(8, 101), (158, 215), (266, 350)]  # plan frames from the guide ("ลำดับคลิปที่ต้องได้")
result = {"opentimelineio": otio.__version__}
for name, kwargs in (("timeline.otio", {}), ("timeline.edl", {"rate": 30}), ("timeline.fcpxml", {})):
    timeline = otio.adapters.read_from_file(str(work / name), **kwargs)
    if isinstance(timeline, otio.schema.SerializableCollection):
        result.setdefault("collections", []).append(name)
        timeline = next(iter(timeline.find_children(descended_from_type=otio.schema.Timeline, shallow_search=True)), None) or next(iter(timeline))
    track = timeline.video_tracks()[0]
    clips = [item for item in track if isinstance(item, otio.schema.Clip)]
    ranges = []
    for clip in clips:
        source = clip.source_range
        start = round(source.start_time.rescaled_to(30).value)
        ranges.append((start, start + round(source.duration.rescaled_to(30).value)))
    record = [
        (round(track.range_of_child(clip).start_time.rescaled_to(30).value), round(track.range_of_child(clip).end_time_exclusive().rescaled_to(30).value))
        for clip in clips
    ]
    result[name] = {
        "clips": len(clips),
        "source_frames": ranges,
        "record_frames": record,
        "matches_guide": ranges == expected and record == [(0, 93), (93, 150), (150, 234)],
        "duration_frames": round(track.duration().rescaled_to(30).value),
    }
print(json.dumps(result, indent=2))
(work / "otio-import.json").write_text(json.dumps(result, indent=2) + "\n")
