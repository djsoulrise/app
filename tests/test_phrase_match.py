import threading
import unittest
import numpy as np
from app import phrase_match as p,auto_audio as auto
from app.scan_control import ScanCancelled


class PhraseTests(unittest.TestCase):
    def phrase(self):
        rng=np.random.default_rng(771)
        notes=rng.integers(0,12,70)
        chroma=np.eye(12,dtype='float32')[np.repeat(notes,2)]
        chroma=.9*chroma+.1*rng.random(chroma.shape)
        chroma/=np.linalg.norm(chroma,axis=1,keepdims=True)
        return chroma
    def test_shared_fragment_survives_offset_pitch_change_and_unrelated_surroundings(self):
        a=self.phrase();rng=np.random.default_rng(4)
        b=np.vstack([rng.random((100,12)),np.roll(a[20:120],3,axis=1),rng.random((100,12))])
        b/=np.linalg.norm(b,axis=1,keepdims=True)
        result=p.align(a,b)
        self.assertTrue(p.reliable(result),result)
        self.assertGreater(result['seconds'],20)
        self.assertAlmostEqual(result['b_start']-result['a_start'],20,delta=1)
    def test_same_notes_reordered_and_constant_chord_are_not_a_shared_phrase(self):
        a=self.phrase();b=a[np.random.default_rng(25).permutation(len(a))]
        self.assertFalse(p.reliable(p.align(a,b)))
        flat=np.tile(np.eye(12)[3],(140,1))
        self.assertFalse(p.reliable(p.align(flat,flat)))
    def test_screen_searches_outside_the_first_24_seconds(self):
        a=self.phrase();rng=np.random.default_rng(5);b=rng.random((500,12));b/=np.linalg.norm(b,axis=1,keepdims=True);b[300:440]=np.roll(a,4,axis=1)
        pack=lambda c:{'chroma':p.pack(c),'upper':p.pack(c),'seconds':len(c)/4}
        result=p.compare(pack(a),pack(b))
        self.assertTrue(p.reliable(result),result);self.assertGreater(result['b_start'],70)
    def test_embedding_and_generic_beat_cannot_bypass_local_verification(self):
        hit=dict(neural_score=99.,rhythm_score=99.,rhythm_reliable=True,tonal_score=99.,tonal_reliable=True)
        self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,False))
        hit['phrase_verification']={'score':95.,'seconds':16.,'density':.95,'strength':56.,'contrast':3.}
        self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,True))
        self.assertEqual(auto.passing(hit,{**auto.DEFAULTS,'tonal':False}),(False,False))
    def test_cancellation(self):
        event=threading.Event();event.set()
        with self.assertRaises(ScanCancelled):p.align(self.phrase(),self.phrase(),event)


if __name__=='__main__':unittest.main()
