import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.patchlab.study_sender import VerifiedClipCache, file_signature


class VerifiedClipCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'test.mp4'
        self.path.write_bytes(b'original')
        self.cache = VerifiedClipCache(self.root)
        self.metadata = dict(width=1920, height=1080, fps_rational='30', has_audio=False)
        self.actual = {**self.metadata, 'sha256':'a'*64, 'video_id':'clip_'+'a'*16}

    def test_unchanged_file_hashes_once_and_results_are_not_mutable(self):
        with patch('apps.patchlab.study_sender.inspect_clip', return_value=self.actual) as inspect:
            first, cached = self.cache.inspect(self.path)
            self.assertFalse(cached)
            first['sha256'] = 'b'*64
            second, cached = self.cache.inspect(self.path)
            self.assertTrue(cached)
            self.assertEqual(second['sha256'], 'a'*64)
            self.assertEqual(inspect.call_count, 1)

    def test_same_size_write_invalidates_even_with_preserved_mtime(self):
        import os
        with patch('apps.patchlab.study_sender.inspect_clip', return_value=self.actual) as inspect:
            self.cache.inspect(self.path)
            info = self.path.stat()
            self.path.write_bytes(b'modified')
            os.utime(self.path, ns=(info.st_atime_ns, info.st_mtime_ns))
            self.assertFalse(self.cache.inspect(self.path)[1])
            self.assertEqual(inspect.call_count, 2)

    def test_replacement_deleted_file_and_metadata_change_never_reuse_stale_entry(self):
        with patch('apps.patchlab.study_sender.inspect_clip', return_value=self.actual) as inspect:
            self.cache.inspect(self.path)
            replacement = self.root/'replacement.mp4'
            replacement.write_bytes(b'original'); replacement.replace(self.path)
            self.assertFalse(self.cache.inspect(self.path)[1])
            self.assertEqual(inspect.call_count, 2)
            self.path.unlink()
            with self.assertRaises(FileNotFoundError): self.cache.inspect(self.path)

    def test_change_during_full_inspection_is_rejected_and_not_cached(self):
        def changed(_path):
            self.path.write_bytes(b'changed during hash')
            return self.actual
        with patch('apps.patchlab.study_sender.inspect_clip', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed during'): self.cache.inspect(self.path)
        self.assertNotIn(self.path, self.cache.entries)

    def test_authenticated_startup_result_seeds_metadata_without_rehashing(self):
        attestation = dict(filename='test.mp4', sha256='a'*64, file_signature=file_signature(self.path))
        with patch('apps.patchlab.study_sender.inspect_clip_metadata', return_value=self.metadata), \
             patch('apps.patchlab.study_sender.inspect_clip', side_effect=AssertionError('No second full read')):
            self.assertEqual(self.cache.seed([attestation]), 1)
            actual, cached = self.cache.inspect(self.path)
            self.assertTrue(cached); self.assertEqual(actual['sha256'], 'a'*64)

    def test_stale_startup_result_requires_full_sha_and_path_escape_is_rejected(self):
        attestation = dict(filename='test.mp4', sha256='a'*64, file_signature=file_signature(self.path))
        self.path.write_bytes(b'changed')
        self.assertEqual(self.cache.seed([attestation]), 0)
        with patch('apps.patchlab.study_sender.inspect_clip', return_value=self.actual) as inspect:
            self.assertFalse(self.cache.inspect(self.path)[1]); inspect.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'outside'): self.cache.path('../private.mp4')

    def test_failed_hash_does_not_cache_and_restart_requires_new_validation(self):
        with patch('apps.patchlab.study_sender.inspect_clip', side_effect=ValueError('bad media')):
            with self.assertRaises(ValueError): self.cache.inspect(self.path)
        self.assertFalse(self.cache.entries)
        with patch('apps.patchlab.study_sender.inspect_clip', return_value=self.actual) as inspect:
            self.cache.inspect(self.path)
            self.assertFalse(VerifiedClipCache(self.root).inspect(self.path)[1])
            self.assertEqual(inspect.call_count, 2)


if __name__ == '__main__': unittest.main()
