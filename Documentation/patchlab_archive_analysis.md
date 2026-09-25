# Patchlab records and analysis

The ACCESS `REMOTE_CONTROLLER` station owns the archive API and analysis UI.
Python remains the one-pass NDI sender; do not start a second archive server.

Within this repository, runtime data are stored in `PatchlabData` and exports
in `PatchlabExports`. Both directories are excluded from Git. Records follow
study/participant/session/trial; exports add a timestamp/unique-ID folder.
Keep a separate controlled backup; Git is not the participant-data archive.

Open `http://127.0.0.1:8090/analysis/` in Chrome/Safari. Choose a session and trial;
the matching original from `Videos/Prod` is SHA-verified and opened automatically.
Each trial refers to one clip; a full three-video session contains three trials.
Replay, heatmap and point/line trajectory are separate panes. Parameters update
the maps live. Export buttons write directly here and report their exact path.
The background image follows the current replay instant, while maps summarize
the selected interval. The data measure head/camera projection, not ocular gaze.

Existing macOS Editor records are non-destructively imported from Unity's local
storage. New Unity uploader sends closed trials separately from playback ACKs,
validates the archive checksum receipt and retains local originals. Failed uploads
remain in the outbox for reconnect retry. Completed playback does not imply a
server copy; require ARCHIVED or a verified session-list entry. Unclosed partial
records stay local. Three retries per queue item, 16 MiB per trial limit.

Station configuration: `PATCHLAB_SUPERRESOLUTION` overrides this repository's
location; `PATCHLAB_LOCAL_RECORDS` overrides the Editor data root. Paired LAN
stations require their token in the browser. Never commit tokens or research
records. Consent/DMP, AVP scientific geometry and clock quality remain separate
acceptance gates; browser replay is not certified frame-exact.
