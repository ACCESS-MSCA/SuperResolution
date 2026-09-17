"""NDI sender backchannel receiver (generic infrastructure, no app-specific parsing)."""

from __future__ import annotations

import ctypes
import queue
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    from cyndilib.sender import Sender as CyndilibSender
except Exception:
    CyndilibSender = None

from ndi_native import NDIlib_metadata_frame_t, get_ndi_runtime


NDI_FRAME_TYPE_NONE = 0
NDI_FRAME_TYPE_VIDEO = 1
NDI_FRAME_TYPE_AUDIO = 2
NDI_FRAME_TYPE_METADATA = 3
NDI_FRAME_TYPE_ERROR = 4


class _NdiSendCreate(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_groups", ctypes.c_char_p),
        ("clock_video", ctypes.c_bool),
        ("clock_audio", ctypes.c_bool),
    ]


class _CyndilibSenderLayout(ctypes.Structure):
    # Mirrors cyndilib's generated C struct layout for Sender (cyndilib 0.1.1).
    _fields_ = [
        ("ob_refcnt", ctypes.c_ssize_t),
        ("ob_type", ctypes.c_void_p),
        ("__pyx_vtab", ctypes.c_void_p),
        ("send_create", _NdiSendCreate),
        ("ptr", ctypes.c_void_p),
    ]


class _NdiSendApi:
    def __init__(self):
        self._lib = get_ndi_runtime().lib

    def capture_metadata(self, sender_ptr: int, timeout_ms: int) -> tuple[int, Optional[str], int]:
        frame = NDIlib_metadata_frame_t()
        sender_handle = ctypes.c_void_p(sender_ptr)

        frame_type = int(self._lib.NDIlib_send_capture(sender_handle, ctypes.byref(frame), int(timeout_ms)))

        if frame_type != NDI_FRAME_TYPE_METADATA:
            return frame_type, None, 0

        xml_payload = ""
        try:
            if frame.p_data:
                if frame.length and frame.length > 0:
                    raw = ctypes.string_at(frame.p_data, frame.length)
                    if raw.endswith(b"\x00"):
                        raw = raw[:-1]
                else:
                    raw = ctypes.string_at(frame.p_data)
                xml_payload = raw.decode("utf-8", errors="replace")
        finally:
            self._lib.NDIlib_send_free_metadata(sender_handle, ctypes.byref(frame))

        return frame_type, xml_payload, int(frame.timecode)


@dataclass(frozen=True)
class NdiMetadataMessage:
    raw_xml: str
    tag: str
    attrs: Dict[str, str]
    timecode: int
    received_at: float


def _sender_instance_ptr(sender) -> int:
    native_ptr = getattr(sender, "native_sender_ptr", 0)
    if native_ptr:
        return int(native_ptr)

    if CyndilibSender is not None and isinstance(sender, CyndilibSender):
        obj = _CyndilibSenderLayout.from_address(id(sender))
        ptr = int(obj.ptr) if obj.ptr else 0
        return ptr

    raise TypeError("sender must expose native_sender_ptr or be a cyndilib.sender.Sender")


def _parse_xml_message(xml_payload: str, timecode: int, received_at: float) -> Optional[NdiMetadataMessage]:
    payload = (xml_payload or "").strip()
    if not payload:
        return None

    root = ET.fromstring(payload)
    attrs = {str(k): str(v) for k, v in root.attrib.items()}
    return NdiMetadataMessage(
        raw_xml=payload,
        tag=str(root.tag),
        attrs=attrs,
        timecode=timecode,
        received_at=received_at,
    )


class NdiSenderBackchannelReceiver:
    def __init__(
        self,
        sender,
        timeout_ms: int = 0,
        idle_sleep_seconds: float = 0.001,
        max_queue_size: int = 2048,
        log_warnings: bool = True,
    ):
        self._sender = sender
        self._timeout_ms = max(0, int(timeout_ms))
        self._idle_sleep_seconds = max(0.0, float(idle_sleep_seconds))
        self._log_warnings = bool(log_warnings)
        self._queue: "queue.Queue[NdiMetadataMessage]" = queue.Queue(maxsize=max_queue_size)

        self._api = _NdiSendApi()
        self._sender_ptr = 0

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.received_messages = 0
        self.parse_errors = 0
        self.dropped_messages = 0
        self.last_error: Optional[str] = None

        self.captured_none = 0
        self.captured_video = 0
        self.captured_audio = 0
        self.captured_metadata_frames = 0
        self.captured_error = 0
        self.captured_unknown = 0

    def start(self) -> None:
        if self._thread is not None:
            return

        sender_running = bool(getattr(self._sender, "_running", False))
        sender_ptr = _sender_instance_ptr(self._sender)
        if not sender_running and sender_ptr == 0:
            raise RuntimeError("Sender must be opened before starting backchannel receiver.")

        self._sender_ptr = sender_ptr
        if self._sender_ptr == 0:
            raise RuntimeError("Could not resolve NDI sender pointer.")

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="ndi-backchannel-rx",
            daemon=True,
        )
        self._thread.start()

    def stop(self, join_timeout_seconds: float = 1.0) -> None:
        self._stop_event.set()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(join_timeout_seconds)))
            self._thread = None

    def drain(self, max_messages: int = 64) -> List[NdiMetadataMessage]:
        max_messages = max(1, int(max_messages))
        items: List[NdiMetadataMessage] = []

        for _ in range(max_messages):
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break

        return items

    def stats_snapshot(self) -> Dict[str, int]:
        return {
            "none": self.captured_none,
            "video": self.captured_video,
            "audio": self.captured_audio,
            "metadata_frames": self.captured_metadata_frames,
            "error": self.captured_error,
            "unknown": self.captured_unknown,
            "received_messages": self.received_messages,
            "parse_errors": self.parse_errors,
            "queue_drops": self.dropped_messages,
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame_type, xml_payload, timecode = self._api.capture_metadata(self._sender_ptr, self._timeout_ms)
            except Exception as exc:
                self.last_error = str(exc)
                if self._log_warnings:
                    print(f"[warn] NDI backchannel capture failed: {exc}")
                time.sleep(0.05)
                continue

            if frame_type == NDI_FRAME_TYPE_NONE:
                self.captured_none += 1
            elif frame_type == NDI_FRAME_TYPE_VIDEO:
                self.captured_video += 1
            elif frame_type == NDI_FRAME_TYPE_AUDIO:
                self.captured_audio += 1
            elif frame_type == NDI_FRAME_TYPE_METADATA:
                self.captured_metadata_frames += 1
            elif frame_type == NDI_FRAME_TYPE_ERROR:
                self.captured_error += 1
            else:
                self.captured_unknown += 1

            if frame_type == NDI_FRAME_TYPE_METADATA and xml_payload:
                now = time.monotonic()
                try:
                    message = _parse_xml_message(xml_payload, timecode, now)
                except Exception:
                    self.parse_errors += 1
                    continue

                if message is None:
                    continue

                self.received_messages += 1
                try:
                    self._queue.put_nowait(message)
                except queue.Full:
                    self.dropped_messages += 1
                    try:
                        _ = self._queue.get_nowait()
                        self._queue.put_nowait(message)
                    except (queue.Empty, queue.Full):
                        pass

            elif frame_type == NDI_FRAME_TYPE_ERROR:
                self.last_error = "NDI sender capture returned frame_type_error"
                if self._log_warnings:
                    print("[warn] NDI backchannel returned frame_type_error")
                time.sleep(0.05)

            elif self._timeout_ms == 0 and self._idle_sleep_seconds > 0:
                time.sleep(self._idle_sleep_seconds)
