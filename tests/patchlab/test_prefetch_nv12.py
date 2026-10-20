import unittest
import time
import av
import numpy as np
from apps.patchlab.study_sender import FramePrefetch, packed_nv12_pixels

class PrefetchTests(unittest.TestCase):
    def test_nv12_preserves_each_visible_sample_and_removes_only_stride_padding(self):
        frame=av.VideoFrame(18,4,'nv12')
        expected=[]
        for index,plane in enumerate(frame.planes):
            rows=4 if index==0 else 2
            values=np.full((rows,plane.line_size),245,dtype=np.uint8)
            for row in range(rows):values[row,:18]=np.arange(18,dtype=np.uint8)+row+index*30
            plane.update(values.tobytes());expected.append(values[:,:18])
        packed=packed_nv12_pixels(frame)
        self.assertEqual(packed.shape,(6,18));self.assertTrue(packed.flags.c_contiguous)
        np.testing.assert_array_equal(packed,np.vstack(expected))

    def test_queue_is_bounded_preserves_order_eof_and_finalizes_decoder(self):
        finalized=[];prefetch=FramePrefetch(iter(range(30)),lambda:finalized.append(True),capacity=3)
        try:
            time.sleep(.03);self.assertLessEqual(prefetch.queue.qsize(),3)
            self.assertEqual([prefetch.next() for _ in range(30)],list(range(30)))
            self.assertIsNone(prefetch.next())
        finally:prefetch.close()
        self.assertFalse(prefetch.thread.is_alive());self.assertEqual(finalized,[True])

    def test_cancel_unblocks_a_full_queue_and_error_is_not_silent_eof(self):
        finalized=[];prefetch=FramePrefetch(iter(range(100)),lambda:finalized.append(True),capacity=1)
        time.sleep(.03);prefetch.close();self.assertFalse(prefetch.thread.is_alive());self.assertEqual(finalized,[True])
        def broken():
            yield 7
            raise ValueError('decoder failed')
        prefetch=FramePrefetch(broken(),lambda:None)
        try:
            self.assertEqual(prefetch.next(),7)
            with self.assertRaisesRegex(ValueError,'decoder failed'):prefetch.next()
        finally:prefetch.close()
