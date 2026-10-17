"""Experimental short-pattern retrieval with an independent shuffled-order control.

Uses the existing frozen network, not newly trained weights. Repetition fills its
long fixed input with actual music instead of a mostly silent 24-second input.
Scores are cosine similarities, not probabilities or proof of a copied sample.
"""
import base64
import zlib
from . import learned_audio
from .scan_control import check_stop

ID='periodic24-stride12-shuffle731-v1'


def pack(values):
    import numpy as np
    return base64.b64encode(zlib.compress(np.asarray(values,dtype='<f2').tobytes())).decode('ascii')


def extract(samples, sr=11025, stop_event=None):
    import numpy as np
    import librosa
    from scipy.signal import resample_poly
    from . import config
    import os
    cache=config.DATA/'model_jit';cache.mkdir(exist_ok=True)
    os.environ.setdefault('NUMBA_CACHE_DIR',str(cache))
    check_stop(stop_event)
    y=np.asarray(samples,dtype='float32')
    if sr!=22050:
        import math
        d=math.gcd(sr,22050);y=resample_poly(y,22050//d,sr//d)
    duration=len(y)/22050
    if duration<8:return None
    last=max(0,duration-24)
    starts=sorted({0.,last,*[float(s) for s in range(12,int(last)+1,12)]})
    engine=learned_audio.session();ordered=[];controls=[]
    # Each window is transformed separately to preserve the tested CQT boundary behavior.
    for start in starts:
        check_stop(stop_event)
        part=y[round(start*22050):round((start+24)*22050)]
        c=np.abs(librosa.cqt(y=part,sr=22050,hop_length=512,n_bins=84,bins_per_octave=12)).T.astype('float16').astype('float32')
        blocks=[c[i:i+43] for i in range(0,len(c),43)]
        shuffled=np.concatenate([blocks[i] for i in np.random.default_rng(731).permutation(len(blocks))])
        for data,out in ((c,ordered),(shuffled,controls)):
            check_stop(stop_event)
            data=np.tile(data,(int(np.ceil(8000/len(data))),1))[:8000].reshape(400,20,84).mean(axis=1)
            data/=data.max()+1e-6
            v=engine.run(None,{'cqt':data.T[None,None].astype('float32')})[0][0]
            if v.shape!=(512,) or not np.isfinite(v).all():raise ValueError('Neispravan AI potpis kratke fraze.')
            out.append(v/max(float(np.linalg.norm(v)),1e-8))
    return dict(version=ID,model=learned_audio.MODEL_ID,starts=starts,seconds=min(24.,duration),vectors=pack(ordered),controls=pack(controls))


def compare(a,b):
    import numpy as np
    if not a or not b or a.get('version')!=ID or b.get('version')!=ID or a.get('model')!=learned_audio.MODEL_ID or b.get('model')!=learned_audio.MODEL_ID:return {}
    av,bv=learned_audio.vectors(a['vectors']),learned_audio.vectors(b['vectors'])
    scores=av@bv.T;i,j=np.unravel_index(np.argmax(scores),scores.shape)
    # Same search budget for real and shuffled recordings; use both directions.
    null=max(float((learned_audio.vectors(a['controls'])@bv.T).max()),float((av@learned_audio.vectors(b['controls']).T).max()))
    value=float(scores[i,j])*100
    return dict(kind='periodic',score=round(value,3),control_score=round(null*100,3),gap=round(value-null*100,3),
        a_start=a['starts'][i],b_start=b['starts'][j],seconds=min(a['seconds'],b['seconds']),
        strength=round(value-null*100,3),density=0.,contrast=0.,experimental=True)


def reliable(result,cutoff=62):
    return bool(result and result.get('kind')=='periodic' and result.get('experimental') is True and result.get('seconds',0)>=8
                and result.get('score',0)>=cutoff and result.get('gap',0)>=4.)
