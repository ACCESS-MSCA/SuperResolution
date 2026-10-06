"""Backchannel infrastructure for receiver -> sender metadata."""

from .dispatcher import MetadataDispatcher
from .receiver import NdiMetadataMessage, NdiSenderBackchannelReceiver

__all__ = [
    "MetadataDispatcher",
    "NdiMetadataMessage",
    "NdiSenderBackchannelReceiver",
]
