import json
import threading
import unittest
import numpy as np
from app import learned_audio as learned,auto_audio as auto
from app.scan_control import ScanCancelled


class LearnedAudioTests(unittest.TestCase):
 def test_model_is_bundled_and_cpu_only(self):
  self.assertTrue(learned.available(),'Export the pinned model before running model tests.')
  self.assertEqual(learned.session().get_providers(),['CPUExecutionProvider'])
  meta=json.loads((learned.ASSETS/'discogs-vinet.json').read_text())
  self.assertEqual(meta['checkpoint_sha256'],'9d0b1d5cdd459a22b1062f69f0ef12ff59d0681a5d90d338827598dc55e8bf13')
  self.assertLess(meta['onnx_parity_max_error'],1e-4)
 def test_real_feature_shape_finiteness_and_self_match(self):
  sr=11025;t=np.arange(sr*13)/sr;notes=np.array([0,4,7,2,5,9,11,4]);freq=220*2**(notes[(t*2).astype(int)%len(notes)]/12)
  sound=(.4*np.sin(2*np.pi*freq*t)+.1*np.sin(2*np.pi*80*t)*np.exp(-(t%.5)*40)).astype('float32')
  x=learned.tensor(sound);self.assertEqual(x.shape,(1,1,84,400));self.assertTrue(np.isfinite(x).all());self.assertGreater(x.max(),.99)
  signature=learned.signatures(sound,{'start':0,'end':13});vectors=learned.vectors(signature['vectors'])
  self.assertEqual(vectors.shape,(1,512));self.assertGreater(learned.similarity(signature,signature)[0],99.9)
 def test_neural_cosine_alone_does_not_prove_a_shared_phrase(self):
  hit=dict(rhythm_reliable=True,rhythm_score=99,tonal_reliable=True,tonal_score=99,neural_score=99)
  self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,False))
  hit['phrase_verification']=dict(score=95,seconds=16,density=.95,strength=56,contrast=3.)
  self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,True))
  self.assertEqual(auto.passing(hit,{**auto.DEFAULTS,'learned_model':False}),(False,True))
  self.assertEqual(auto.passing(hit,{**auto.DEFAULTS,'tonal':False}),(False,False))
 def test_high_cosine_cannot_override_short_or_incoherent_phrase(self):
  hit=dict(rhythm_reliable=False,rhythm_score=0,tonal_reliable=True,tonal_score=99,neural_score=99)
  hit['phrase_verification']=dict(score=99,seconds=2,density=.99,strength=8,contrast=3.)
  self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,False))
  hit['phrase_verification'].update(seconds=16,strength=56,density=.4)
  self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,False))
 def test_stop_and_unknown_model(self):
  stop=threading.Event();stop.set()
  with self.assertRaises(ScanCancelled):learned.tensor(np.ones(11025*13,dtype='float32'),stop_event=stop)
  self.assertEqual(learned.similarity({'model':'unknown'},None),(0.,None,None))

if __name__=='__main__':unittest.main()
