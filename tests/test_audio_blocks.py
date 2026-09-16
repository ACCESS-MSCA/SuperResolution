from types import SimpleNamespace
from unittest.mock import patch
import threading

import numpy as np

from media_reader import MediaAudioEvent
from stream_video import (
    _AUDIO_BLOCK_SAMPLES,
    _DEFAULT_AUDIO_PREROLL_MILLISECONDS,
    _FixedAudioBlockSource,
    _calculate_av_sync_metrics,
    _preload_audio_pcm_pyav,
    _wait_for_audio_lead,
)


def test_cached_blocks_cross_loop_without_short_packet():
    pcm = np.vstack(
        (
            np.arange(5, dtype=np.float32),
            np.arange(5, dtype=np.float32) + 100,
        )
    )
    source = _FixedAudioBlockSource(cached_pcm=pcm, sample_rate=48000, channels=2)

    for block_index in range(4):
        cursor, block = source.read()
        assert cursor == block_index * _AUDIO_BLOCK_SAMPLES
        assert block.shape == (2, _AUDIO_BLOCK_SAMPLES)
        expected = (
            np.arange(cursor, cursor + _AUDIO_BLOCK_SAMPLES, dtype=np.int64) % 5
        ).astype(np.float32)
        np.testing.assert_array_equal(block[0], expected)
        np.testing.assert_array_equal(block[1], expected + 100)


def test_universal_source_has_no_receiver_specific_preroll():
    assert _DEFAULT_AUDIO_PREROLL_MILLISECONDS == 0.0


class _ChunkReader:
    def __init__(self):
        self.cursor = 0
        self.lengths = (17, 300, 1, 777, 63)
        self.index = 0
        self.restart_count = 1

    def read_next(self):
        length = self.lengths[self.index % len(self.lengths)]
        self.index += 1
        values = np.arange(self.cursor, self.cursor + length, dtype=np.float32)
        self.cursor += length
        return SimpleNamespace(samples=np.vstack((values, values + 1_000_000)))


def test_streamed_chunks_are_coalesced_sample_exactly():
    reader = _ChunkReader()
    source = _FixedAudioBlockSource(reader=reader, sample_rate=48000, channels=2)

    for block_index in range(10):
        cursor, block = source.read()
        expected = np.arange(
            cursor,
            cursor + _AUDIO_BLOCK_SAMPLES,
            dtype=np.float32,
        )
        np.testing.assert_array_equal(block[0], expected)
        np.testing.assert_array_equal(block[1], expected + 1_000_000)


def test_pyav_preload_preserves_timeline_gaps_and_stops_before_second_pass():
    class _PreloadReader:
        instance = None

        def __init__(self, *_args, **_kwargs):
            self.restart_count = 1
            self.closed = False
            self._events = iter(
                (
                    MediaAudioEvent(0.0, np.full((2, 3), 1.0, dtype=np.float32)),
                    MediaAudioEvent(5.0 / 48000.0, np.full((2, 2), 2.0, dtype=np.float32)),
                )
            )
            _PreloadReader.instance = self

        def read_next(self):
            try:
                return next(self._events)
            except StopIteration:
                self.restart_count = 2
                return MediaAudioEvent(0.0, np.full((2, 8), 9.0, dtype=np.float32))

        def close(self):
            self.closed = True

    with patch("media_reader.LoopingMediaReader", _PreloadReader):
        pcm = _preload_audio_pcm_pyav("unused", 8.0 / 48000.0)

    expected = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 2.0, 2.0, 0.0], dtype=np.float32)
    np.testing.assert_array_equal(pcm[0], expected)
    np.testing.assert_array_equal(pcm[1], expected)
    assert _PreloadReader.instance.closed


def test_video_waits_until_native_audio_preserves_requested_lead():
    class _AdvancingStopEvent:
        def __init__(self, stats):
            self.stats = stats
            self.waits = 0

        def wait(self, _timeout):
            self.waits += 1
            self.stats["last_sent_media_end"] += 0.5
            return False

    stats = {"last_sent_media_end": 11.0, "error": None}
    stop = _AdvancingStopEvent(stats)
    waited = _wait_for_audio_lead(10.0, 2.5, stop, stats)

    assert stop.waits == 3
    assert stats["last_sent_media_end"] == 12.5
    assert waited >= 0.0


def test_video_audio_lead_gate_raises_worker_failure():
    stats = {"last_sent_media_end": None, "error": ValueError("send failed")}
    try:
        _wait_for_audio_lead(10.0, 2.5, threading.Event(), stats)
    except RuntimeError as exc:
        assert "send failed" in str(exc)
    else:
        raise AssertionError("audio worker failure was not propagated")


def test_av_sync_metrics_distinguish_content_lead_from_submission_drift():
    metrics = _calculate_av_sync_metrics(
        video_send_completed_monotonic=112.5,
        audio_send_completed_monotonic=112.5,
        video_clock_origin_monotonic=12.5,
        audio_clock_origin_monotonic=10.0,
        first_media_time_seconds=0.007,
        video_media_time_seconds=100.007,
        audio_media_end_seconds=102.507,
        target_audio_lead_seconds=2.5,
    )

    assert abs(metrics["av_content_lead_ms"] - 2500.0) < 0.001
    assert abs(metrics["av_content_lead_error_ms"]) < 0.001
    assert abs(metrics["video_clock_lag_ms"]) < 0.001
    assert abs(metrics["audio_clock_lag_ms"]) < 0.001
    assert abs(metrics["av_submission_drift_ms"]) < 0.001


def test_av_sync_metrics_report_video_submission_falling_behind_audio():
    metrics = _calculate_av_sync_metrics(
        video_send_completed_monotonic=112.8,
        audio_send_completed_monotonic=112.7,
        video_clock_origin_monotonic=12.5,
        audio_clock_origin_monotonic=10.0,
        first_media_time_seconds=0.007,
        video_media_time_seconds=100.007,
        audio_media_end_seconds=102.507,
        target_audio_lead_seconds=2.5,
    )

    assert abs(metrics["video_clock_lag_ms"] - 300.0) < 0.001
    assert abs(metrics["audio_clock_lag_ms"] - 200.0) < 0.001
    assert abs(metrics["av_submission_drift_ms"] - 100.0) < 0.001


def test_av_sync_metrics_are_unavailable_before_first_audio_send():
    metrics = _calculate_av_sync_metrics(
        video_send_completed_monotonic=12.5,
        audio_send_completed_monotonic=None,
        video_clock_origin_monotonic=12.5,
        audio_clock_origin_monotonic=10.0,
        first_media_time_seconds=0.0,
        video_media_time_seconds=0.0,
        audio_media_end_seconds=None,
        target_audio_lead_seconds=2.5,
    )

    assert all(value is None for value in metrics.values())


if __name__ == "__main__":
    test_cached_blocks_cross_loop_without_short_packet()
    test_universal_source_has_no_receiver_specific_preroll()
    test_streamed_chunks_are_coalesced_sample_exactly()
    test_pyav_preload_preserves_timeline_gaps_and_stops_before_second_pass()
    test_video_waits_until_native_audio_preserves_requested_lead()
    test_video_audio_lead_gate_raises_worker_failure()
    test_av_sync_metrics_distinguish_content_lead_from_submission_drift()
    test_av_sync_metrics_report_video_submission_falling_behind_audio()
    test_av_sync_metrics_are_unavailable_before_first_audio_send()
    print("fixed audio block tests passed")
