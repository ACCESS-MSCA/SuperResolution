# Patchlab study adapter — development branch

## Fast verified preparation · 2026-10-13

The authenticated coordinator hashes the library before opening the station and
sends Python the verified SHA-256 plus device/inode/size/nanosecond mtime/ctime.
`VerifiedClipCache` seeds metadata only if the exact local file signature matches.
Every prepare checks the signature; changes invalidate the entry and trigger full
inspection, with catalog hash/geometry/cadence/audio validation still mandatory.
Files changing during inspection are rejected. The cache is process-local and
does not survive a restart without a new authenticated startup validation.

The coordinator prepares Python first, then Unity after the source holds frame
zero. This avoids spending Unity's 25-second discovery window hashing a 943 MB
file (30.41 seconds measured). Live cached 8K sender prepare took 1.98/1.79 seconds;
receiver discovery and the visual transition add their own time. No codec,
bitrate, resolution, frames, PTS, UYVY transport or automatic playback changed.
28 Python tests cover cache invalidation, stale attestations, actual HEVC decoding
and the existing one-pass/held-frame lifecycle. AVP visual/full-session acceptance
and sustainable network cadence remain separate checks.

## Current delivery profile · 2026-10-13

The active ACCESS study sequence is light, orange flare, duck and the same light
again, with independent instructions for positions one and four. New canonical
files are MP4/hvc1, HEVC Main, 8-bit 4:2:0, CFR and silent: light 7680×4320/30
(1430 frames), flare 3840×2160/30 (899 frames), duck 3840×2160/25 (678 frames).
The light master requests 160 Mb/s and measures 158.324499 Mb/s; flare/duck request
60 Mb/s. 2K 2048×1152 and FHD 1920×1080 deliveries request 16/12 Mb/s respectively.
Encoded bitrate is measured, not assumed equal to the request. NDI transport
bandwidth is separate from the compressed file's bitrate.

`apps.patchlab.prepare_delivery` produces immutable, independently identified
delivery files without replacing scientific originals or dropping audio silently.
`apps.patchlab.prepare_renditions` preserves source cadence and frame count.
`apps.patchlab.verify_delivery` verifies hashes, profile, all decoded frames and
exact CFR PTS. On macOS HEVC delivery decoding uses VideoToolbox without an
unannounced software fallback; the existing packed-UYVY NDI path is unchanged.
These checks confirm files/decoding, not live AVP framerate or Wi-Fi throughput.

ACCESS's active catalog/renditions identify the nine future files. The historical
catalog keeps the previous masters and local compatibility rendition, so archived
G001/G002 records retain their original identity and remain replayable. New
content must not change their raw manifests, sample files or frame identities.
The older dated notes below describe prior iterations, not today's active media.

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
the looping NDI sender or the established 8K/audio launchers. The three current
`media/patchlab/clips` files are 3840×2160 and contain no audio. The user confirmed on
2026-09-09 that they must play once; assignment to conditions is not blocking.
The catalog in ACCESS `STUDY_CONTRACTS/catalog.json` contains original SHA-256s.

```sh
.venv/bin/python -m pip install --only-binary=:all: -r requirements-study.txt
.venv/bin/python -m apps.patchlab.study_sender --coordinator ws://127.0.0.1:8090/ws
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
