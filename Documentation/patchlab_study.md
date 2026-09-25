# Patchlab study adapter — development branch

2026-09-09 final-frame fix: coordinator now waits for Unity finish before sending
Python release. EOF keeps the final pixel buffer available, resending at nominal
cadence with unchanged frame ID/PTS and terminal_hold="1". Unity excludes these
transport copies from samples; there is no clip loop or new decode. Release/abort
stops the hold, with a hard 10-second timeout if no release arrives. Update all
three peers together. 21 Python tests pass; real three-clip retry remains pending.

2026-09-09 runtime fix: PyAV's direct `to_ndarray(format='uyvy422')` is unsupported.
The adapter now uses reformat + plane extraction + stride-padding removal, matching
media_reader. Two actual-PyAV regression tests added (18 unittest tests passing);
first-frame conversion of all three Prod clips verified without NDI emission.
Full real-peer replay after this fix remains pending.

This is an isolated **silent planar one-pass** adapter, not a replacement for
`stream_video.py` or the established 8K/audio launchers. The three current
`Videos/Prod` files are 3840×2160 and contain no audio. The user confirmed on
2026-09-09 that they must play once; assignment to conditions is not blocking.
The catalog in ACCESS `STUDY_CONTRACTS/catalog.json` contains original SHA-256s.

```sh
.venv/bin/python -m pip install --only-binary=:all: -r requirements-study.txt
.venv/bin/python study_sender.py --coordinator ws://127.0.0.1:8090/ws
```

Start ACCESS's Remote coordinator first. Provision `PATCHLAB_TOKEN` in the
environment when paired; no token in CLI arguments/logs. The sender registers
as `python`, then stays idle until coordinator commands. It advertises
`ACCESS_PATCHLAB`, uses UYVY, and sends `access_study_frame` XML with
trial/epoch/video/frame ID and relative media PTS attached to each video frame.
The reusable async sender now retains both pixel and metadata references until
the next async call returns; its existing audio implementation is unchanged.

Prepare verifies the file against the requested catalog. Play ACK follows first
send. Pause preserves the media timeline. EOF emits once and never loops.
Finish/abort closes the sender. Control has a bounded queue; disconnect does
not auto-restart playback. Files with audio are rejected explicitly until this
adapter is integrated with the established continuous PCM pipeline.

The sender publishes lightweight progress telemetry twice per second (state,
content frame, PTS and sent-frame count) for the operator monitor. This does not
change NDI cadence, frame metadata, media clocks or the one-pass lifecycle.

The WebSocket adapter uses the official
[websocket-client API](https://websocket-client.readthedocs.io/en/latest/examples.html),
pinned separately to 1.9.0. Installed in the local virtual environment without
changing NumPy/PyAV. No live NDI transmission was started during initial tests.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Remaining: real receiver/EOF drain validation, archive/checksum upload service,
clock calibration, source lifecycle under native stalls, quality records and
integration with the complete study scene. Current tests use fake media and
tiny arrays; they do not establish AVP playback quality or audio continuity.
