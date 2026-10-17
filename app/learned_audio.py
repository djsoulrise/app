"""CPU-only pretrained musical-version signatures. No titles/tags enter the model."""
import base64
from functools import lru_cache
import hashlib
import json
import os
os.environ['ORT_DISABLE_TELEMETRY']='1'
from pathlib import Path
import threading
import zlib
from . import config
from .scan_control import check_stop

MODEL_ID='Discogs-VINet-ISMIR2024-9d0b1d5cdd45-cqt-v1'
ASSETS=Path(__file__).resolve().parent.parent/'assets'
SESSION=None
SESSION_LOCK=threading.Lock()


def available():
    return (ASSETS/'discogs-vinet.onnx').is_file() and (ASSETS/'discogs-vinet.json').is_file()


def info():
    return {'available':available(),'name':'Discogs-VINet','id':MODEL_ID,'runtime':'CPU / offline'}


def session():
    global SESSION
    with SESSION_LOCK:
        if SESSION is None:
            import onnxruntime as ort
            # Library audio is processed locally. Disable the runtime's optional
            # telemetry before constructing an inference session.
            ort.disable_telemetry_events()
            path=ASSETS/'discogs-vinet.onnx'
            metadata=json.loads((ASSETS/'discogs-vinet.json').read_text())
            if hashlib.sha256(path.read_bytes()).hexdigest()!=metadata['onnx_sha256']: raise ValueError('AI model je promenjen ili ostecen.')
            options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
            SESSION=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
    return SESSION


def tensor(samples, sr=11025, stop_event=None):
    """Match the published preprocessing; FFmpeg replaces only the audio loader."""
    import numpy as np
    from scipy.signal import resample_poly
    # A writable cache is required for numba inside a frozen Windows application.
    cache=config.DATA/'model_jit';cache.mkdir(exist_ok=True)
    os.environ.setdefault('NUMBA_CACHE_DIR',str(cache))
    import librosa
    check_stop(stop_event)
    audio=np.asarray(samples,dtype='float32')
    if sr!=22050:
        import math
        divisor=math.gcd(int(sr),22050);audio=resample_poly(audio,22050//divisor,int(sr)//divisor)
    cqt=np.abs(librosa.cqt(y=audio,sr=22050,hop_length=512,n_bins=84,bins_per_octave=12)).T.astype(np.float16).astype(np.float32)
    if len(cqt)<8000:cqt=np.pad(cqt,((0,8000-len(cqt)),(0,0)))
    cqt=cqt[:8000].reshape(400,20,84).mean(axis=1)
    cqt=np.maximum(cqt,0);cqt/=cqt.max()+1e-6
    check_stop(stop_event)
    return cqt.T.copy()[None,None].astype('float32')


def signatures(samples, section, sr=11025, stop_event=None):
    import numpy as np
    length=min(24.,len(samples)/sr);last=max(0,len(samples)/sr-length)
    starts=sorted({max(0,min(last,float(section['start'])+offset)) for offset in (-6,0,6)})
    engine=session();values=[]
    for start in starts:
        check_stop(stop_event)
        part=samples[round(start*sr):round((start+length)*sr)]
        value=engine.run(None,{'cqt':tensor(part,sr,stop_event)})[0][0]
        if value.shape!=(512,) or not np.isfinite(value).all():raise ValueError('AI potpis nije ispravan.')
        value/=max(float(np.linalg.norm(value)),1e-8);values.append(value)
    check_stop(stop_event)
    return {'model':MODEL_ID,'starts':starts,'vectors':base64.b64encode(zlib.compress(np.array(values,dtype='<f2').tobytes())).decode('ascii')}


@lru_cache(maxsize=8192)
def vectors(encoded):
    import numpy as np
    value=np.frombuffer(zlib.decompress(base64.b64decode(encoded)),dtype='<f2').astype('float32').reshape(-1,512)
    value/=np.maximum(np.linalg.norm(value,axis=1,keepdims=True),1e-8)
    return value


def similarity(a,b):
    import numpy as np
    if not a or not b or a.get('model')!=MODEL_ID or b.get('model')!=MODEL_ID:return 0.,None,None
    scores=vectors(a['vectors'])@vectors(b['vectors']).T
    i,j=np.unravel_index(np.argmax(scores),scores.shape)
    return float(np.clip(scores[i,j],0,1))*100,a['starts'][i],b['starts'][j]
