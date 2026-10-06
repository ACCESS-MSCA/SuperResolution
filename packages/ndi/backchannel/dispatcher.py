"""Dispatch metadata messages to optional integration handlers."""

from __future__ import annotations

from typing import Iterable, Protocol

from .receiver import NdiMetadataMessage


class MetadataMessageHandler(Protocol):
    def handle(self, message: NdiMetadataMessage) -> bool:
        """Return True when the message was consumed by this handler."""


class MetadataDispatcher:
    """Generic dispatcher: infrastructure logs + optional integration handlers."""

    def __init__(
        self,
        handlers: Iterable[MetadataMessageHandler] | None = None,
        verbose_raw_xml: bool = False,
        log_unhandled: bool = True,
    ):
        self._handlers = list(handlers or [])
        self._verbose_raw_xml = bool(verbose_raw_xml)
        self._log_unhandled = bool(log_unhandled)

    def dispatch_many(self, messages: Iterable[NdiMetadataMessage]) -> None:
        for message in messages:
            self.dispatch_one(message)

    def dispatch_one(self, message: NdiMetadataMessage) -> None:
        if self._verbose_raw_xml:
            print(f"[RX-META] {message.raw_xml}")

        handled = False
        for handler in self._handlers:
            if handler.handle(message):
                handled = True
                break

        if not handled and self._log_unhandled and not self._verbose_raw_xml:
            print(f"[RX-META] tag={message.tag} attrs={message.attrs}")
