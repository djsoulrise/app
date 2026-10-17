"""Real bundled-decoder/WASM tests; no user's music or Traktor is accessed."""
import json,os,tempfile,time,wave,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np


def music(seconds=12,seed=4):
    sr=22050;t=np.arange(sr*seconds)/sr;notes=[220,261.63,293.66,349.23,329.63,293.66,246.94,261.63]
    rng=np.random.default_rng(seed);notes=np.asarray(notes)[rng.permutation(len(notes))]
    index=(t*2).astype(int)%len(notes);freq=notes[index]
    y=.22*np.sin(2*np.pi*freq*t)+.1*np.sin(4*np.pi*freq*t)+.04*np.sin(6*np.pi*freq*t)
    beat=t%.5;y+=.18*np.sin(2*np.pi*70*t)*np.exp(-beat*40)
    return y.astype('float32')


def write(path,y):
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(22050);wav.writeframes((y*25000).astype('<i2').tobytes())


def run():
    from . import features,storage,jobs,matcher
    with ThreadPoolExecutor(2) as pool:
        probes=list(pool.map(lambda _:features.run_engine({'operation':'probe'}),range(2)))
    assert len({p['pid'] for p in probes})==2 and all(p['pid']!=os.getpid() for p in probes)
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);storage.DATABASE=root/'scanner.db';storage.init()
        write(root/'alpha.wav',music());write(root/'beta.wav',music()*.7);(root/'broken.wav').write_bytes(b'not an audio file')
        jobs.STOP.clear();jobs.PAUSE.clear();jobs.STATE.update(started=time.time(),errors=[],active={})
        result=jobs.scan([str(root)],2)
        assert result['scanned']==2 and result['failed']==1,result
        second=jobs.scan([str(root)],2);assert second['pending_at_start']==0,second
        records=storage.load_records();assert len(records)==2
        keys=list(records);metrics=matcher.compare_records(records[keys[0]],records[keys[1]])
        assert matcher.decision(metrics),metrics
        with storage.connect() as c:c.execute('INSERT OR REPLACE INTO pairs VALUES(?,?,?,?)',(*sorted(keys),__import__('scanner').ENGINE,json.dumps(metrics)))
        out=root/'results.djmatches';storage.export(out);manifest=storage.read_manifest(out)
        assert len(manifest['tracks'])==2 and len(manifest['pairs'])==1
        full=root/'full.djscan';storage.export(full,True)
        storage.DATABASE=root/'destination.db';storage.init();restored=storage.import_full(full)
        assert restored['features']==2 and len(storage.load_records())==2,restored
        return {'ok':True,'frozen':bool(getattr(__import__('sys'),'frozen',False)),'parallel_node_workers':True,'decoder':True,'broken_file_skipped':True,'resume':True,'portable_export':True,'full_import':True,'no_midi':True}
