"""Unity integration handlers built on top of generic backchannel metadata."""

from __future__ import annotations

from extensions.backchannel.receiver import NdiMetadataMessage

from .parsers import try_parse_unity_transform


class UnityTransformLogHandler:
    """Consumes Unity transform metadata and logs a concise trace line."""

    def handle(self, message: NdiMetadataMessage) -> bool:
        transform = try_parse_unity_transform(message)
        if transform is None:
            return False

        px, py, pz = transform.position
        print(
            "[RX Transform] "
            f"seq={transform.sequence} "
            f"scene={transform.scene} "
            f"name={transform.name} "
            f"space={transform.space} "
            f"pos=({px:.3f}, {py:.3f}, {pz:.3f})"
        )
        return True
