"""Run with python -m unittest discover -s tests -p test_audio_rhythm.py."""
import unittest
import numpy as np
from app.audio_rhythm import analyze_pcm, similarity, MIN_SIMILARITY


def drum_audio(kind='dance', gain=1):
    sr = 11025
    y = np.zeros(sr*96, dtype=np.float32)
    for beat in np.arange(0, 96, .5):
        events = [(beat, 70, .8)]
        if kind == 'dance':
            if round(beat*2) % 4 in (1, 3):
                events.append((beat, 1300, .4))
            events.append((beat+.25, 3500, .3))
        else:
            if round(beat*2) % 4 in (0, 3):
                events.append((beat+.125, 1300, .9))
            events.extend([(beat+.125, 3500, .3), (beat+.375, 3500, .3)])
        for at, frequency, amplitude in events:
            n, i = int(.10*sr), int(at*sr)
            if i+n > len(y):
                continue
            t = np.arange(n)/sr
            y[i:i+n] += gain*amplitude*np.sin(2*np.pi*frequency*t)*np.exp(-t*60)
    return y


class RhythmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = analyze_pcm(drum_audio())

    def test_same_pattern_different_gain(self):
        score = similarity(self.a, analyze_pcm(drum_audio(gain=.6)))
        self.assertIsNotNone(score)
        self.assertGreaterEqual(score, MIN_SIMILARITY)

    def test_same_bpm_different_pattern(self):
        score = similarity(self.a, analyze_pcm(drum_audio('broken')))
        self.assertLess(score or 0, MIN_SIMILARITY)

    def test_silence_is_not_a_match(self):
        with self.assertRaises(ValueError):
            analyze_pcm(np.zeros(11025*60, dtype=np.float32))

    def test_variable_tempo_excluded(self):
        other = {**self.a, 'variable_tempo_suspected': True}
        self.assertIsNone(similarity(self.a, other))

    def test_large_tempo_difference_excluded(self):
        self.assertIsNone(similarity(self.a, {**self.a, 'bpm': 155}))


if __name__ == '__main__':
    unittest.main()
