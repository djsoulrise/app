import unittest
import numpy as np
from app import loop_audio as m,learned_audio

class PeriodicTests(unittest.TestCase):
    def signature(self,real,control):
        return dict(version=m.ID,model=learned_audio.MODEL_ID,starts=[12.],seconds=24.,vectors=m.pack([real]),controls=m.pack([control]))

    def test_high_cosine_does_not_override_order_control(self):
        v=np.zeros(512);v[0]=1
        same=self.signature(v,v)
        result=m.compare(same,same)
        self.assertGreater(result['score'],99)
        self.assertFalse(m.reliable(result))

    def test_control_uses_both_directions(self):
        a=np.eye(512)[0];b=.7*a+np.sqrt(1-.7**2)*np.eye(512)[1];noise=np.eye(512)[2]
        left=self.signature(a,noise);right=self.signature(b,noise)
        result=m.compare(left,right)
        self.assertTrue(m.reliable(result))
        right['controls']=m.pack([a])
        self.assertFalse(m.reliable(m.compare(left,right)))
        self.assertFalse(m.reliable(m.compare(right,left)))

    def test_cutoff_and_short_duration_are_enforced(self):
        proof=dict(kind='periodic',experimental=True,score=65,gap=5,seconds=24)
        self.assertTrue(m.reliable(proof,62));self.assertFalse(m.reliable(proof,66))
        proof['seconds']=7
        self.assertFalse(m.reliable(proof))
