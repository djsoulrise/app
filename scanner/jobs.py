import os,json,threading,time,subprocess,traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from . import ENGINE
from . import storage,features,matcher
STOP=threading.Event();PAUSE=threading.Event();LOCK=threading.Lock()
STATE={'running':False,'phase':'Miruje','done':0,'total':0,'errors':[],'active':{},'workers':0,'result':None,'cancelled':False,'paused':False}
EXTENSIONS={'.mp3','.flac','.wav','.aif','.aiff','.m4a','.aac','.ogg','.opus','.wma'}


def memory_available():
    try:
        if os.name=='nt':
            import ctypes
            class Memory(ctypes.Structure):_fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(key,ctypes.c_ulonglong) for key in ('total','available','page','availpage','virtual','availvirtual','extended')]
            m=Memory();m.length=ctypes.sizeof(m);ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m));return m.available
        return os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
    except (OSError,ValueError,AttributeError):return 2*1024**3


def workers(value=None,turbo=False):
    cpu=os.cpu_count() or 1;ram=max(1,int((memory_available()-768*1024**2)//(700*1024**2)))
    requested=cpu if turbo else int(value or min(4,max(1,cpu-2)))
    return max(1,min(cpu,ram,requested,32))


def start(name,fn):
    with LOCK:
        if STATE['running']:raise ValueError('Drugi posao je u toku.')
        STOP.clear();PAUSE.clear();STATE.update(running=True,phase=name,done=0,total=0,errors=[],active={},result=None,cancelled=False,paused=False,started=time.time())
    def run():
        try:STATE['result']=fn()
        except InterruptedError:STATE.update(cancelled=True,phase='Zaustavljeno — zavrseni rezultati su sacuvani')
        except Exception as exc:
            STATE['error']=str(exc);STATE['phase']='Greska: '+str(exc)
            (storage.DATA/'last-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        finally:features.close_workers();STATE.update(running=False,active={},paused=False);PAUSE.clear()
    STATE.pop('error',None);threading.Thread(target=run,daemon=True).start();return {'started':True}


def check():
    while PAUSE.is_set():
        STATE['paused']=True
        if STOP.wait(.1):raise InterruptedError('Zaustavljeno')
    STATE['paused']=False
    if STOP.is_set():raise InterruptedError('Zaustavljeno')


def execute(tasks,fn,count):
    from collections import deque
    STATE.update(total=len(tasks),done=0,workers=min(count,len(tasks)))
    pending=deque(tasks);futures={};finished=0
    with ThreadPoolExecutor(max_workers=min(count,max(1,len(tasks)))) as pool:
        def submit(item):
            def work():
                ident=str(threading.get_ident());STATE['active'][ident]=fn.label(item) if hasattr(fn,'label') else str(item[0] if isinstance(item,tuple) else item)
                try:return fn(item)
                finally:STATE['active'].pop(ident,None)
            futures[pool.submit(work)]=item
        while pending or futures:
            stopping=STOP.is_set();STATE['paused']=PAUSE.is_set()
            if not stopping and not PAUSE.is_set():
                while pending and len(futures)<count:submit(pending.popleft())
            if not futures:
                if stopping:raise InterruptedError('Zaustavljeno')
                STOP.wait(.1);continue
            done,_=wait(list(futures),timeout=.2,return_when=FIRST_COMPLETED)
            for future in done:
                item=futures.pop(future)
                try:yield item,future.result(),None
                except InterruptedError:raise
                except Exception as exc:yield item,None,exc
                finished+=1;STATE['done']=finished
                elapsed=max(.01,time.time()-STATE['started']);STATE.update(rate=finished/elapsed,eta=(len(tasks)-finished)/(finished/elapsed) if finished else None)
            if stopping:raise InterruptedError('Zaustavljeno')


def discover(roots,retry=False):
    known={r['path']:r for r in storage.rows('SELECT * FROM files')};pending=[];seen=set()
    valid_hashes={r['hash'] for r in storage.rows('SELECT hash FROM features WHERE engine=?',(ENGINE,))}
    for root in roots:
        check();folder=Path(root).expanduser()
        if folder.is_file():
            walk=[(str(folder.parent),[],[folder.name])]
        elif folder.is_dir():walk=os.walk(folder)
        else:STATE['errors'].append({'path':str(folder),'error':'Folder ili fajl nije dostupan.'});continue
        for parent,dirs,names in walk:
            check()
            for name in names:
                path=Path(parent)/name
                if path.suffix.lower() not in EXTENSIONS:continue
                value=str(path.resolve())
                if value in seen:continue
                seen.add(value)
                try:stat=path.stat()
                except OSError as exc:STATE['errors'].append({'path':value,'error':str(exc)});continue
                old=known.get(value)
                valid=old and old['size']==stat.st_size and old['mtime']==stat.st_mtime_ns
                if valid and ((old['status']=='ok' and old['hash'] in valid_hashes) or (old['status']=='error' and not retry)):continue
                with storage.connect() as c:c.execute('INSERT OR REPLACE INTO files(path,size,mtime,status,error) VALUES(?,?,?,?,?)',(value,stat.st_size,stat.st_mtime_ns,'pending',None))
                pending.append(value)
    return pending


def scan(roots,worker_count=None,turbo=False,retry=False):
    # Preflight global dependencies once: don't fail every library item.
    features.node_path();features.decoder();features.run_engine({'operation':'probe'},timeout=20,stop=STOP)
    os.environ['DJ_SCANNER_TURBO']='1' if turbo else '0'
    tasks=discover(roots,retry);count=workers(worker_count,turbo)
    STATE['phase']='Skeniram audio osobine';ok=0;failed=0
    def calculate(path):
        for attempt in range(2):
            try:return features.extract(path,STOP)
            except RuntimeError as exc:
                if attempt or 'neocekivano ugasen' not in str(exc):raise
    calculate.label=lambda path:Path(path).name
    for path,result,error in execute(tasks,calculate,count):
        if error is None:
            try:storage.save(result);ok+=1
            except Exception as exc:error=exc
        if error is not None:
            failed+=1;STATE['errors'].append({'path':str(path),'error':str(error)})
            STATE['errors']=STATE['errors'][-200:]
            with storage.connect() as c:c.execute("UPDATE files SET status='error',error=? WHERE path=?",(str(error),path))
    STATE['phase']='Sken zavrsen'
    return {'scanned':ok,'failed':failed,'pending_at_start':len(tasks)}


def match(worker_count=None,turbo=False,thorough=False):
    os.environ['DJ_SCANNER_TURBO']='1' if turbo else '0'
    STATE['phase']='Pravim indeks audio sekcija';records=storage.load_records();check()
    index=matcher.SegmentIndex(records,stop=STOP,progress=lambda i,n:STATE.update(done=i,total=n));check()
    existing={(r['a'],r['b']) for r in storage.rows('SELECT a,b FROM pairs WHERE engine=?',(ENGINE,))}
    candidates=set()
    for i,identity in enumerate(records):
        check();STATE.update(done=i,total=len(records))
        for other in index.candidates(identity,100 if thorough else 40):candidates.add(tuple(sorted((identity,other))))
    tasks=sorted(candidates-existing);STATE['phase']='Proveravam muzicke parove';STATE['started']=time.time();accepted=0;compared=0;failed=0
    labels={row['hash']:Path(row['path']).name for row in storage.rows("SELECT hash,path FROM files WHERE status='ok'")}
    def compare(pair):return matcher.compare_records(records[pair[0]],records[pair[1]],STOP)
    compare.label=lambda pair:labels.get(pair[0],pair[0])+' / '+labels.get(pair[1],pair[1])
    for pair,result,error in execute(tasks,compare,workers(worker_count,turbo)):
        if error:
            failed+=1
            STATE['errors'].append({'path':' / '.join(pair),'error':str(error)});STATE['errors']=STATE['errors'][-200:];continue
        with storage.connect() as c:c.execute('INSERT OR REPLACE INTO pairs VALUES(?,?,?,?)',(*pair,ENGINE,json.dumps(result)))
        compared+=1;accepted+=bool(matcher.decision(result))
    features.close_workers()
    STATE['phase']='Provera parova zavrsena'
    return {'compared':compared,'failed':failed,'requested':len(tasks),'accepted':accepted,'candidates':len(candidates),'approximate':len(records)>100}
