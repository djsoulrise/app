import threading
import unittest
import numpy as np
from app import sample_match as s
from app.scan_control import ScanCancelled


class SampleTests(unittest.TestCase):
    def signal(self,seed=51):
        rng=np.random.default_rng(seed);sr=11025;t=np.arange(sr*14)/sr
        notes=rng.integers(0,24,56);f=220*2**(notes[(t*4).astype(int)]/12)
        # Harmonics and an irregular transient texture supply spectral landmarks.
        audio=.3*np.sin(2*np.pi*f*t)+.15*np.sin(2*np.pi*2*f*t)+.1*np.sin(2*np.pi*3*f*t)
        audio*=np.minimum(1,(t%.25)*30)
        return audio.astype('float32')
    def test_short_copied_fragment_inside_an_unrelated_recording(self):
        source=self.signal();target=self.signal(77)
        target[7*11025:12*11025]=source[2*11025:7*11025]*.7
        result=s.compare(s.extract(source),s.extract(target))
        self.assertTrue(s.reliable(result),result)
        self.assertAlmostEqual(result['b_start']-result['a_start'],5,delta=.5)
    def test_unrelated_audio_is_not_a_sample(self):
        result=s.compare(s.extract(self.signal()),s.extract(self.signal(77)))
        self.assertFalse(s.reliable(result),result)
    def test_cancellation(self):
        stop=threading.Event();stop.set()
        with self.assertRaises(ScanCancelled):s.extract(self.signal(),stop)


if __name__=='__main__':unittest.main()
