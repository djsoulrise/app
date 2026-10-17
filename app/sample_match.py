"""Time-consistent spectral landmarks for a reused fragment inside another song.

Landmark pairing follows the approach described by Dan Ellis' audfprint.
Log-frequency coordinates additionally allow a constant pitch displacement.
"""
import base64
import zlib
from functools import lru_cache
from .scan_control import check_stop

RATE=11025
HOP=256


def frequency_median(spec,stop_event=None):
    """Exact size=(1,31), reflect-boundary median, bounded working memory.

    Partition exploits the one-dimensional frequency neighborhood. It changes
    neither peaks nor descriptors, so existing caches remain compatible.
    """
    import numpy as np
    out=np.empty_like(spec)
    for start in range(0,len(spec),64):
        check_stop(stop_event)
        padded=np.pad(spec[start:start+64],((0,0),(15,15)),mode='symmetric')
        windows=np.lib.stride_tricks.sliding_window_view(padded,31,axis=1)
        out[start:start+64]=np.partition(windows,15,axis=-1)[...,15]
    return out


def extract(samples, stop_event=None):
    import numpy as np
    from scipy.ndimage import maximum_filter
    check_stop(stop_event)
    fft=2048
    frames=np.lib.stride_tricks.sliding_window_view(np.asarray(samples),fft)[::HOP]
    # Bounded blocks keep long library tracks from allocating an enormous FFT.
    spec=np.concatenate([np.log1p(np.abs(np.fft.rfft(frames[i:i+1024]*np.hanning(fft),axis=1))).astype('float32')
                         for i in range(0,len(frames),1024)])
    freqs=np.fft.rfftfreq(fft,1/RATE)
    valid=(freqs>=120)&(freqs<=4500)
    contrast=spec-frequency_median(spec,stop_event)
    peaks=(contrast==maximum_filter(contrast,size=(9,7))) & (contrast>.7) & valid[None,:]
    times,bins=np.where(peaks)
    # Keep the most salient four peaks in each quarter-second interval.
    buckets={}
    for t,f in zip(times,bins):buckets.setdefault(int(t*HOP/RATE*4),[]).append((float(contrast[t,f]),int(t),int(f)))
    selected=[]
    for group in buckets.values():
        for _,t,f in sorted(group,reverse=True)[:4]:
            # Sub-bin interpolation improves pitch-shift consistency.
            y=spec[t,f-1:f+2];denom=y[0]-2*y[1]+y[2]
            correction=.5*(y[0]-y[2])/denom if abs(denom)>1e-8 else 0
            frequency=(f+correction)*RATE/fft
            selected.append((t,int(round(36*np.log2(frequency/55)))))
    selected.sort();landmarks=[]
    for i,(t,f) in enumerate(selected):
        future=[(u,g) for u,g in selected[i+1:i+60] if .2<=(u-t)*HOP/RATE<=1.8 and abs(g-f)>=2]
        for u,g in future[:5]:landmarks.append((t,f,g-f,u-t))
    check_stop(stop_event)
    array=np.asarray(landmarks,dtype='<i2').reshape(-1,4)
    return {'data':base64.b64encode(zlib.compress(array.tobytes())).decode('ascii'),'seconds':len(samples)/RATE}


@lru_cache(maxsize=1024)
def unpack(data):
    import numpy as np
    return np.frombuffer(zlib.decompress(base64.b64decode(data)),dtype='<i2').reshape(-1,4)


def compare(a,b,stop_event=None):
    import numpy as np
    from collections import defaultdict
    aa,bb=unpack(a['data']),unpack(b['data']);best={'count':0,'anchors':0,'seconds':0.,'a_start':0.,'b_start':0.,'ratio':0.}
    index=defaultdict(list)
    for t,f,delta,dt in bb:
        index[(int(delta),round(float(dt)*HOP/RATE*10))].append((int(t),int(f)))
    for scale in (.9,.95,1.,1.05,1.1):
        check_stop(stop_event)
        votes=defaultdict(list)
        for t,f,delta,dt in aa:
            key=(int(delta),round(float(dt)*HOP/RATE*10*scale))
            matches=index.get(key,())
            if len(matches)>40:continue  # ubiquitous pairs provide little identity
            for u,g in matches:
                shift=g-int(f)
                if abs(shift)>18:continue
                offset=round((u-scale*int(t))*HOP/RATE*5)
                votes[(shift,offset)].append((int(t),u))
        for (shift,offset),pairs in sorted(votes.items(),key=lambda p:len(p[1]),reverse=True)[:12]:
            # Include the adjacent time bin, never an unrelated song location.
            pairs=pairs+votes.get((shift,offset-1),[])+votes.get((shift,offset+1),[])
            anchors=sorted(set(t for t,u in pairs))
            if len(anchors)<3:continue
            start,end=anchors[0],anchors[-1];span=(end-start)*HOP/RATE
            total=len(set(int(t) for t,f,d,dt in aa if start<=t<=end))
            ratio=len(anchors)/max(1,total)
            result={'count':len(set(pairs)),'anchors':len(anchors),'seconds':round(span,2),
                'a_start':round(start*HOP/RATE,2),'b_start':round(min(u for t,u in pairs)*HOP/RATE,2),
                'ratio':round(ratio,3),'scale':scale,'pitch_shift':shift/3}
            if (result['anchors'],result['count'])>(best['anchors'],best['count']):best=result
    return best


def reliable(result):
    return result['anchors']>=10 and result['count']>=18 and result['seconds']>=3 and result['ratio']>=.35


@lru_cache(maxsize=1024)
def histogram(data):
    from collections import Counter
    return Counter((int(delta),round(float(dt)*HOP/RATE*10)) for t,f,delta,dt in unpack(data))


def screen(a,b):
    from math import sqrt
    aa,bb=histogram(a['data']),histogram(b['data'])
    return sum(sqrt(n*bb.get(k,0)) for k,n in aa.items())/max(1,sqrt(sum(aa.values())*sum(bb.values())))
