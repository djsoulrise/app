import unittest,numpy as np
from scipy.ndimage import median_filter
from app import sample_match as s
class FastMedianTests(unittest.TestCase):
 def test_bit_exact_reflect_edges_repeats_and_block_boundaries(self):
  for shape in [(1,7),(65,33),(130,1025)]:
   for x in [np.random.default_rng(23).normal(size=shape).astype('float32'),np.ones(shape,dtype='float32')]:
    np.testing.assert_array_equal(s.frequency_median(x),median_filter(x,size=(1,31)))
 def test_existing_landmark_bytes_are_unchanged(self):
  from unittest.mock import patch
  sr=11025;t=np.arange(sr*14)/sr;rng=np.random.default_rng(51);notes=rng.integers(0,24,56);f=220*2**(notes[(t*4).astype(int)]/12)
  y=((.3*np.sin(2*np.pi*f*t)+.15*np.sin(2*np.pi*2*f*t)+.1*np.sin(2*np.pi*3*f*t))*np.minimum(1,(t%.25)*30)).astype('float32')
  fast=s.extract(y);self.assertGreater(len(s.unpack(fast['data'])),0)
  with patch.object(s,'frequency_median',side_effect=lambda x,stop:median_filter(x,size=(1,31))):reference=s.extract(y)
  self.assertEqual(fast,reference)
