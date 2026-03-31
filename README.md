
# StreamNDI

Stream a video file as an NDI® source over your local network using [cyndilib](https://github.com/nocarryr/cyndilib).

NDI Monitor will discover and display the stream automatically on your local network.

## Requirements

- Python 3.9+
- macOS / Linux (no extra NDI SDK needed — cyndilib ships pre-built wheels)
- Windows: install the [NDI SDK](https://ndi.video/download-ndi-sdk/) first

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python3 stream_video.py                          # streams included Big Buck Bunny
python3 stream_video.py /path/to/video.mp4
python3 stream_video.py --dual                   # two streams: StreamNDI + StreamNDI-Square
python3 stream_video.py /path/to/video.mp4 --dual
```

The video loops automatically. The NDI source name defaults to `StreamNDI`.

`--dual` publishes two NDI sources simultaneously from a single decode pass: `StreamNDI` (plain) and `StreamNDI-Square` (with a bouncing bicubic-processed square overlay).

## Receiving the stream

Once the script is running, open **NDI Monitor** — it will automatically discover and display the `StreamNDI` source.

## Project structure

```
StreamNDI/
├── stream_video.py      # main loop and CLI
├── utils.py             # make_sender and draw_square helpers
├── big_buck_bunny.mp4   # sample video (CC BY 3.0, Blender Foundation)
├── requirements.txt
└── README.md
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `cyndilib` | NDI® send/receive via Cython wrapper |
| `opencv-python` | Decode video frames |
| `numpy` | Frame data manipulation |