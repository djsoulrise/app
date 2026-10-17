"""Audio-only segment index and precise local sequence verification."""
import json,math
from collections import defaultdict,Counter
import numpy as np
from .features import unpack,run_engine
DEFAULTS={'tonal':55.,'rhythm':65.,'sample':70.}
CONTRAST={'tonal':1.6,'rhythm':1.8,'sample':1.7}
MIN_SECONDS={'tonal':8.,'rhythm':8.,'sample':6.}


def arrays(features):
    hpcp=unpack(features['hpcp']);onset=unpack(features['onsets'])
    rhythm=np.concatenate([np.roll(onset,lag,axis=0) for lag in [0,2,4,6,8,10,14,18,24,32]],axis=1)[32::4]
    rhythm/=np.maximum(np.linalg.norm(rhythm,axis=1,keepdims=True),1e-6)
    return {'tonal':hpcp,'rhythm':rhythm,'sample':unpack(features['sample'])}


def decision(metrics,thresholds=None):
    thresholds=thresholds or DEFAULTS;accepted=[]
    for kind,match in metrics.items():
        if match['score']>=thresholds.get(kind,DEFAULTS[kind]) and match['contrast']>=CONTRAST[kind] and match['seconds']>=MIN_SECONDS[kind]:accepted.append(kind)
    return accepted


def segments(data,step,seconds=30,stride=15):
    length=len(data);width=max(12,round(seconds/step));hop=max(1,round(stride/step))
    starts=list(range(0,max(1,length-width+1),hop))
    last=max(0,length-width)
    if starts[-1]!=last:starts.append(last)
    return [(start*step,data[start:start+width]) for start in starts if len(data[start:start+width])>=12]


def vector(data,kind):
    # Time- and pitch-translation invariant coarse chroma summary (2DFT).
    if kind=='tonal':
        sample=np.stack([np.interp(np.linspace(0,len(data)-1,32),np.arange(len(data)),data[:,j]) for j in range(12)],axis=1)
        v=np.abs(np.fft.fft2(sample))[1:7,1:7].flatten()
    elif kind=='rhythm':
        v=np.abs(np.fft.rfft(data[:,:6],n=128,axis=0))[1:13].flatten()
    else:v=np.std(data,axis=0)
    v=np.log1p(v);return (v/max(np.linalg.norm(v),1e-6)).astype('float32')


class SegmentIndex:
    """Seeded multi-table LSH. No metadata gates; recall is approximate."""
    def __init__(self,records,stop=None,progress=None):
        self.records=records;self.windows={};self.vectors={};self.buckets={};self.planes={};self.owners={};self.centroids={}
        for kind in ('tonal','rhythm','sample'):
            windows=[];vectors=[]
            for number,(identity,record) in enumerate(records.items()):
                if stop is not None and stop.is_set():raise InterruptedError('Zaustavljeno')
                if progress and number%20==0:progress(number,len(records))
                data=record['arrays'][kind];step=record['step'][kind]
                for offset,section in segments(data,step):windows.append((identity,offset));vectors.append(vector(section,kind))
            if not vectors:continue
            values=np.asarray(vectors,dtype='float32');self.windows[kind]=windows;self.vectors[kind]=values
            owners=defaultdict(list)
            for i,(owner,_) in enumerate(windows):owners[owner].append(i)
            self.owners[kind]=owners
            keys=list(owners);centroids=np.stack([values[owners[key]].mean(axis=0) for key in keys]);centroids/=np.maximum(np.linalg.norm(centroids,axis=1,keepdims=True),1e-6)
            self.centroids[kind]=(keys,centroids)
            rng=np.random.default_rng(711);planes=rng.normal(size=(6,15,values.shape[1])).astype('float32');self.planes[kind]=planes
            tables=[]
            for plane in planes:
                codes=((values@plane.T>0)*(1<<np.arange(15))).sum(axis=1)
                table=defaultdict(list)
                for i,code in enumerate(codes):table[int(code)].append(i)
                tables.append(table)
            self.buckets[kind]=tables

    def candidates(self,identity,limit=40):
        if len(self.records)<=100:return [other for other in sorted(self.records) if other!=identity]
        votes=Counter()
        for kind,windows in self.windows.items():
            values=self.vectors[kind]
            for index in self.owners[kind].get(identity,()):
                query=values[index];matches=set()
                for table,plane in zip(self.buckets[kind],self.planes[kind]):
                    code=int(((plane@query>0)*(1<<np.arange(15))).sum())
                    for key in [code]+[code^(1<<bit) for bit in range(15)]:
                        bucket=table.get(key,())
                        if len(bucket)<=300:matches.update(bucket)
                best={}
                for other_index in matches:
                    other,_=windows[other_index]
                    if other==identity:continue
                    similarity=float(query@values[other_index]);best[other]=max(best.get(other,0),similarity)
                for other,score in sorted(best.items(),key=lambda x:-x[1])[:20]:votes[other]+=score**4
        # Add global audio-only neighbors when a common LSH bucket was skipped.
        for kind,(keys,centroids) in self.centroids.items():
            if identity not in keys:continue
            scores=centroids@centroids[keys.index(identity)]
            for i in np.argsort(scores)[-12:]:
                if keys[i]!=identity:votes[keys[i]]+=float(scores[i])**4
        return [key for key,_ in votes.most_common(limit)]


def compare_records(a,b,stop=None):
    metrics={}
    # Compare up to three best audio-window combinations for each criterion.
    # One request can include different winning windows for independent criteria.
    for kind in ('tonal','rhythm','sample'):
        wa=segments(a['arrays'][kind],a['step'][kind]);wb=segments(b['arrays'][kind],b['step'][kind])
        va=[vector(x,kind) for _,x in wa];vb=[vector(x,kind) for _,x in wb]
        scores=np.asarray(va)@np.asarray(vb).T
        positions=np.argsort(scores.ravel())[-3:][::-1]
        best=None
        for position in positions:
            if stop is not None and stop.is_set():raise InterruptedError('Zaustavljeno')
            i,j=np.unravel_index(position,scores.shape);ao,aa=wa[i];bo,bb=wb[j]
            result=run_engine({'operation':'compare','a':{kind:aa.tolist()},'b':{kind:bb.tolist()}},timeout=60,stop=stop)[kind]
            # Rhythm context begins 32 original onset frames into the song.
            ashift=32*256/11025 if kind=='rhythm' else 0
            begin_a=ao+result['start'][0]*a['step'][kind]+ashift
            begin_b=bo+result['start'][1]*b['step'][kind]+ashift
            end_a=min(a['seconds'],ao+result['end'][0]*a['step'][kind]+ashift)
            end_b=min(b['seconds'],bo+result['end'][1]*b['step'][kind]+ashift)
            result.update(a_section=[begin_a,end_a],b_section=[begin_b,end_b],seconds=max(0,min(end_a-begin_a,end_b-begin_b)))
            if best is None or result['score']>best['score']:best=result
        if best:metrics[kind]=best
    return metrics


def record(features,seconds):
    return {'arrays':arrays(features),'seconds':seconds,'step':{'tonal':features['step'],'rhythm':features['texture_step'],'sample':features['texture_step']}}
