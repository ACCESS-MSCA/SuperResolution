from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from media_reader import MediaAudioEvent
from stream_video import (
    _AUDIO_BLOCK_SAMPLES,
    _DEFAULT_AUDIO_PREROLL_MILLISECONDS,
    _FixedAudioBlockSource,
    _preload_audio_pcm_pyav,
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


def test_default_preroll_matches_access_receiver_reservoir():
    assert _DEFAULT_AUDIO_PREROLL_MILLISECONDS == 2500.0


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


if __name__ == "__main__":
    test_cached_blocks_cross_loop_without_short_packet()
    test_default_preroll_matches_access_receiver_reservoir()
    test_streamed_chunks_are_coalesced_sample_exactly()
    test_pyav_preload_preserves_timeline_gaps_and_stops_before_second_pass()
    print("fixed audio block tests passed")
