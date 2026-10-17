"""Local musical-phrase verification; embeddings retrieve, ordered notes decide.

Inspired by the HPCP / stacked cross-recurrence / local-alignment chain
documented by MTG Essentia. This implementation has no Essentia dependency.
"""
import base64
import zlib
from functools import lru_cache
from .scan_control import check_stop

HZ = 4
VERSION = 1


def pack(value):
    import numpy as np
    return base64.b64encode(zlib.compress(np.asarray(value, dtype='<f2').tobytes())).decode('ascii')


@lru_cache(maxsize=1024)
def unpack(value):
    import numpy as np
    return np.frombuffer(zlib.decompress(base64.b64decode(value)), dtype='<f2').astype('float32').reshape(-1, 12)


def extract(samples, sr=11025, stop_event=None):
    import os
    import numpy as np
    from scipy.ndimage import median_filter
    from . import config
    cache = config.DATA / 'model_jit'; cache.mkdir(exist_ok=True)
    os.environ.setdefault('NUMBA_CACHE_DIR', str(cache))
    import librosa
    check_stop(stop_event)
    # The harmonic mask suppresses simultaneous drum transients, while CQT
    # separates semitones more accurately than broad FFT pitch-class buckets.
    magnitude = np.abs(librosa.cqt(y=np.asarray(samples, dtype='float32'), sr=sr,
                                  hop_length=512, n_bins=84, bins_per_octave=12))
    harmonic = median_filter(magnitude, size=(1, 17))
    percussive = median_filter(magnitude, size=(17, 1))
    magnitude *= harmonic**2 / np.maximum(harmonic**2 + percussive**2, 1e-12)
    # Two frequency ranges let a different bass arrangement coexist with the
    # same higher-register melodic hook. Neither is a musical key label.
    result = {'hz': HZ, 'seconds': len(samples)/sr, 'version': VERSION}
    from . import sample_match
    result['sample']=sample_match.extract(samples,stop_event)
    for name, lower in (('chroma', 12), ('upper', 36)):
        chroma = np.sqrt(np.maximum(magnitude[lower:], 0)).reshape(-1, 12, magnitude.shape[1]).sum(axis=0).T
        edges = np.arange(0, len(samples)/sr, 1/HZ)
        frames = np.minimum((edges*sr/512).astype(int), len(chroma)-1)
        reduced = np.array([chroma[a:max(a+1,b)].mean(axis=0) for a,b in zip(frames, [*frames[1:],len(chroma)])])
        # Remove stationary accompaniment and ubiquitous pitch classes.
        reduced = np.maximum(0, reduced - .8*np.median(reduced, axis=0))
        reduced /= np.maximum(np.linalg.norm(reduced, axis=1, keepdims=True), 1e-8)
        result[name] = pack(reduced)
    check_stop(stop_event)
    return result


def stacked(chroma, width=5):
    import numpy as np
    if len(chroma) < width: return np.empty((0, width*12), dtype='float32')
    value = np.lib.stride_tricks.sliding_window_view(chroma, width, axis=0).transpose(0,2,1).reshape(-1,width*12).copy()
    value /= np.maximum(np.linalg.norm(value, axis=1, keepdims=True), 1e-8)
    return value


def recurrence(matches,stop_event=None):
    import numpy as np
    n,m = matches.shape[1:]
    scores = np.zeros((12,n+1,m+1),dtype='float32')
    trace = np.zeros((12,n+1,m+1),dtype='uint8')
    for diagonal in range(2,n+m+1):
        if diagonal%64==0:check_stop(stop_event)
        ii=np.arange(max(1,diagonal-m), min(n,diagonal-1)+1);jj=diagonal-ii
        reward=np.where(matches[:,ii-1,jj-1], 1., -1.)
        choices=np.stack([np.zeros((12,len(ii))), scores[:,ii-1,jj-1]+reward,
                          np.where(ii[None,:]>1,scores[:,np.maximum(ii-2,0),jj-1]+reward-.25,-1),
                          np.where(jj[None,:]>1,scores[:,ii-1,np.maximum(jj-2,0)]+reward-.25,-1)])
        ways=choices.argmax(axis=0);scores[:,ii,jj]=np.take_along_axis(choices,ways[None],axis=0)[0];trace[:,ii,jj]=ways
    return scores,trace


def align(a, b, stop_event=None):
    """Slope-constrained cross-recurrence, rather than unconstrained gap walks."""
    import numpy as np
    check_stop(stop_event)
    aa, bb = stacked(a), stacked(b)
    if not len(aa) or not len(bb): return {'score':0., 'seconds':0., 'density':0., 'a_start':0., 'b_start':0., 'shift':0}
    rotated = np.stack([stacked(np.roll(b, shift, axis=1)) for shift in range(12)])
    similarity = np.einsum('id,sjd->sij', aa, rotated, optimize=True)
    # A match must be unusually close from BOTH directions, not merely share
    # some loud notes. Absolute agreement guards against unrelated flat audio.
    row = np.quantile(similarity, .905, axis=2, keepdims=True)
    col = np.quantile(similarity, .905, axis=1, keepdims=True)
    matches = (similarity >= row) & (similarity >= col) & (similarity >= .65)
    scores,trace=recurrence(matches,stop_event)
    shift,i,j=np.unravel_index(np.argmax(scores),scores.shape);peak=float(scores[shift,i,j]);end_i,end_j=i,j
    values=[];positive=steps=0
    while i>0 and j>0 and trace[shift,i,j]:
        way=trace[shift,i,j];values.append(float(similarity[shift,i-1,j-1]));positive+=int(matches[shift,i-1,j-1]);steps+=1
        i-=2 if way==2 else 1;j-=2 if way==3 else 1
    seconds=min(end_i-i,end_j-j)/HZ
    density=positive/max(1,steps)
    # Strength grows with the amount of ordered evidence, not with a single
    # high cosine. Expose all components rather than call this a probability.
    # Compare the best alignment with the same data after disrupting time
    # order. Four fixed controls make this reproducible and title independent.
    rng=np.random.default_rng(731)
    null=[]
    for _ in range(4):
        blocks=[np.arange(i,min(i+HZ*2,matches.shape[2])) for i in range(0,matches.shape[2],HZ*2)]
        order=np.concatenate([blocks[i] for i in rng.permutation(len(blocks))])
        control=matches[:,:,order]
        null.append(float(recurrence(control,stop_event)[0].max()))
    null_max=max(null,default=1.)
    contrast=peak/max(null_max,1.)
    quality=.5*min(1,peak/(HZ*16))+.5*max(0,min(1,(contrast-1)/1.5))
    return {'score':round(100*quality,2),'strength':round(peak,2),'contrast':round(contrast,3),'null_strength':round(null_max,2),'seconds':float(round(seconds,2)),
            'density':round(density,3),'cosine':round(float(np.mean(values)) if values else 0,3),
            'a_start':float(round(i/HZ,2)),'b_start':float(round(j/HZ,2)),'shift':int(shift)}


def screen(a,b,stop_event=None):
    """Cheap local retrieval across the whole song, including outside cue hints."""
    import numpy as np
    from scipy.fft import fft2,ifft2,next_fast_len
    best={'score':0.,'a_start':0.,'b_start':0.,'scale':1.}
    for band in ('chroma','upper'):
        aa,bb=unpack(a[band]),unpack(b[band])
        width=min(len(aa),24*HZ)
        # Every part of the source and target remains reachable. Short final
        # fragments are covered by a final full window instead of being lost.
        starts=sorted(set([*range(0,max(1,len(aa)-width+1),12*HZ),max(0,len(aa)-width)]))
        for scale in (.8,1.,1.25):
            check_stop(stop_event)
            target=bb[np.minimum((np.arange(max(1,round(len(bb)/scale)))*scale).astype(int),len(bb)-1)]
            n=next_fast_len(len(target)+width)
            target_fft=fft2(target,s=(n,12))
            for start in starts:
                part=aa[start:start+width]
                length=len(part)
                if len(target)<length:continue
                corr=ifft2(np.conj(fft2(part,s=(n,12)))*target_fft).real[:len(target)-length+1]
                j,shift=np.unravel_index(np.argmax(corr),corr.shape)
                score=float(corr[j,shift])/max(1,length)
                if score>best['score']:best={'score':score,'a_start':start/HZ,'b_start':j*scale/HZ,'scale':scale}
    return best


def compare(a,b,stop_event=None,hint=None):
    # Bound DP memory for long mixes while verifying enough surrounding sound
    # to avoid assuming that a cue or rough retrieval offset is the chorus.
    hint=hint or screen(a,b,stop_event)
    offsets=[]
    for value,key in ((a,'a_start'),(b,'b_start')):
        offsets.append(max(0,min(int((hint[key]-12)*HZ),max(0,len(unpack(value['chroma']))-64*HZ))))
    results=[]
    for band in ('chroma','upper'):
        aa=unpack(a[band])[offsets[0]:offsets[0]+64*HZ]
        bb=unpack(b[band])[offsets[1]:offsets[1]+64*HZ]
        result=align(aa,bb,stop_event)
        result.update(band=band,a_start=result['a_start']+offsets[0]/HZ,b_start=result['b_start']+offsets[1]/HZ)
        results.append(result)
    return max(results,key=lambda r:(r['score'],r['strength']))


def reliable(result, cutoff=80):
    return (result.get('score',0)>=cutoff and result.get('seconds',0)>=8
            and result.get('density',0)>=.8 and result.get('strength',0)>=32)
