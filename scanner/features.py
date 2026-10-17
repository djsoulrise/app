"""Portable descriptors; no title, folder, key or genre participates."""
import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[k]='1'
import hashlib,json,shutil,subprocess,sys,zlib
from pathlib import Path
import numpy as np
from scipy import signal,ndimage
from . import ENGINE
ROOT=Path(__file__).resolve().parent


def node_path():
    bundled=ROOT/'runtime'/'node.exe'
    node=str(bundled) if bundled.exists() else shutil.which('node')
    if not node:raise RuntimeError('Node audio runtime nedostaje; instalacija skenera nije potpuna.')
    return node


def decoder():
    import imageio_ffmpeg
    path=imageio_ffmpeg.get_ffmpeg_exe()
    if not Path(path).is_file():raise RuntimeError('FFmpeg dekoder nedostaje u paketu.')
    return path


def run_engine(payload,timeout=120,stop=None):
    process=subprocess.Popen([node_path(),str(ROOT/'engine.cjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        creationflags=(getattr(subprocess,'CREATE_NO_WINDOW',0)|(0 if os.environ.get('DJ_SCANNER_TURBO')=='1' else getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))))
    import time
    started=time.monotonic();data=json.dumps(payload,separators=(',',':')).encode()
    # communicate() handles pipe back-pressure. Poll timeout also checks Stop.
    first=True
    try:
        while True:
            if stop is not None and stop.is_set():raise InterruptedError('Zaustavljeno')
            if time.monotonic()-started>timeout:raise TimeoutError('Audio radnik je prekoracio vremenski limit.')
            try:
                out,_=process.communicate(data if first else None,timeout=.25)
                break
            except subprocess.TimeoutExpired:first=False
        if len(out)>100*1024*1024:raise ValueError('Prevelik rezultat audio radnika.')
        try:result=json.loads(out)
        except (ValueError,UnicodeDecodeError):raise RuntimeError('Audio radnik je neocekivano ugasen.')
        if not result.get('ok'):raise ValueError(result.get('error','Audio radnik nije uspeo.'))
        return result['result']
    finally:
        if process.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=(getattr(subprocess,'CREATE_NO_WINDOW',0)|(0 if os.environ.get('DJ_SCANNER_TURBO')=='1' else getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))))
            else:
                # FFmpeg has its own timeout; terminate this worker too.
                process.kill()
            process.wait()


def file_id(path,stop=None):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        while block:=stream.read(1024*1024):
            if stop is not None and stop.is_set():raise InterruptedError('Zaustavljeno')
            digest.update(block)
    return digest.hexdigest()


def texture(path,stop=None):
    command=[decoder(),'-v','error','-threads','1','-i',str(path),'-vn','-ar','11025','-ac','1','-t','1800','-f','f32le','pipe:1']
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=(getattr(subprocess,'CREATE_NO_WINDOW',0)|(0 if os.environ.get('DJ_SCANNER_TURBO')=='1' else getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))))
    import time
    started=time.monotonic()
    try:
        while True:
            if stop is not None and stop.is_set():raise InterruptedError('Zaustavljeno')
            if time.monotonic()-started>90:raise TimeoutError('Dekodiranje je prekoracilo limit.')
            try:raw,_=process.communicate(timeout=.25);break
            except subprocess.TimeoutExpired:pass
        if process.returncode:raise ValueError('Audio dekodiranje nije uspelo.')
    finally:
        if process.poll() is None:process.kill();process.wait()
    y=np.frombuffer(raw,dtype='<f4')
    if len(y)<11025*8:raise ValueError('Audio je kraci od 8 sekundi.')
    if not np.isfinite(y).all() or np.max(np.abs(y))<1e-5:raise ValueError('Tisina ili neispravan audio signal.')
    # Work in bounded FFT blocks, not a huge whole-song spectrogram.
    frames=np.lib.stride_tricks.sliding_window_view(np.pad(y,(512,512)),1024)[::256]
    spec=np.concatenate([abs(np.fft.rfft(frames[i:i+512]*np.hanning(1024),axis=1)).astype('float32') for i in range(0,len(frames),512)])
    freqs=np.fft.rfftfreq(1024,1/11025)
    # Independent multi-band accent pattern, with local loudness normalization.
    bands=[]
    for lo,hi in zip([35,150,400,1000,2500,4000],[150,400,1000,2500,4000,5500]):
        energy=np.log1p(2*spec[:,(freqs>=lo)&(freqs<hi)]).mean(axis=1)
        flux=np.maximum(0,energy-ndimage.minimum_filter1d(energy,size=3))
        flux/=np.maximum(1e-4,ndimage.uniform_filter1d(flux,size=85));bands.append(np.minimum(flux,6))
    onsets=np.array(bands,dtype='float32').T
    edges=np.geomspace(60,5500,49);mel=[]
    for lo,hi in zip(edges[:-1],edges[1:]):
        take=(freqs>=lo)&(freqs<hi)
        values=np.log1p(.4*spec[:,take]).mean(axis=1) if take.any() else np.log1p(.4*spec[:,np.argmin(abs(freqs-(lo+hi)/2))])
        mel.append(values)
    mel=np.array(mel,dtype='float32').T
    mel-=ndimage.uniform_filter1d(mel,size=129,axis=0)
    mel=ndimage.uniform_filter1d(mel,size=3,axis=0)[::4]
    mel/=np.maximum(np.linalg.norm(mel,axis=1,keepdims=True),1e-6)
    # Store raw six-band onsets compactly; reconstruct context when matching.
    return {'onsets':pack(onsets),'sample':pack(mel),'texture_step':256/11025*4}


def pack(array):
    import base64
    a=np.asarray(array,dtype='<f2')
    if not np.isfinite(a).all():raise ValueError('Neispravne audio osobine.')
    return {'shape':list(a.shape),'data':base64.b64encode(zlib.compress(a.tobytes(),3)).decode()}


def unpack(value):
    import base64
    shape=value['shape']
    if len(shape)!=2 or min(shape)<1 or max(shape)>100000 or shape[0]*shape[1]>10000000:raise ValueError('Neispravan oblik osobina.')
    raw=zlib.decompress(base64.b64decode(value['data']))
    return np.frombuffer(raw,dtype='<f2').astype('float32').reshape(shape)


def extract(path,stop=None):
    path=Path(path);before=path.stat()
    if before.st_size>2*1024**3:raise ValueError('Fajl je veci od 2 GB; preskocen.')
    identity=file_id(path,stop)
    tonal=run_engine({'operation':'extract','path':str(path),'decoder':decoder()},stop=stop)
    if tonal['seconds']>=1799:raise ValueError('Fajl je predug (limit 30 minuta); nije sacuvan delimican sken.')
    other=texture(path,stop)
    if stop is not None and stop.is_set():raise InterruptedError('Zaustavljeno')
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Fajl je promenjen tokom analize; pokreni ponovo.')
    return {'hash':identity,'path':str(path.resolve()),'size':after.st_size,'mtime':after.st_mtime_ns,'engine':ENGINE,
            'seconds':tonal['seconds'],'features':{'hpcp':pack(tonal['hpcp']),'step':tonal['step'],**other}}
