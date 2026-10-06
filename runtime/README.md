# Runtime data — not backed up by Git

| Path | Content |
|---|---|
| `logs/` | Sender and AVP diagnostics |
| `generated_media/` | Disposable low-resolution diagnostic derivatives |
| `patchlab/raw/` | Immutable uploaded trial records and manifests |
| `patchlab/exports/` | Participant packages, PNGs, CSV/JSONL and references |

Do not rename participant/trial identities manually. After a study session,
verify the participant package and copy `runtime/patchlab/` to the approved
off-disk research backup. Git intentionally ignores all payloads in this tree.
