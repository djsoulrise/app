import csv,io,json,os,subprocess,threading
from pathlib import Path
from fastapi import FastAPI,Body,HTTPException,Request
from fastapi.responses import FileResponse,Response
from fastapi.staticfiles import StaticFiles
from . import storage,jobs,features,matcher,ENGINE
storage.init();app=FastAPI(title='DJ Miljan Audio Scanner')
CONFIG=storage.DATA/'settings.json'
try:SETTINGS=json.loads(CONFIG.read_text())
except (OSError,ValueError):SETTINGS={'roots':[],'workers':min(4,max(1,(os.cpu_count() or 1)-2)),'turbo':False,'thresholds':matcher.DEFAULTS,'nml':''}
WINDOW=None

@app.middleware('http')
async def local_only(request:Request,call_next):
    origin=request.headers.get('origin')
    host=request.headers.get('host','')
    if request.client.host not in ('127.0.0.1','::1') or (origin and origin not in ('http://'+host,'https://'+host)):
        return Response('Samo lokalni racunar.',status_code=403)
    return await call_next(request)

@app.get('/')
def index():return FileResponse(Path(__file__).parent/'static'/'index.html')

@app.get('/api/status')
def status():
    counts={r['status']:r['n'] for r in storage.rows('SELECT status,COUNT(*) n FROM files GROUP BY status')}
    return {'job':jobs.STATE,'counts':counts,'pairs':storage.rows('SELECT COUNT(*) n FROM pairs WHERE engine=?',(ENGINE,))[0]['n'],
        'database':str(storage.DATABASE),'settings':SETTINGS,'cpu':os.cpu_count() or 1,'engine':ENGINE}

@app.post('/api/settings')
def settings(body:dict=Body(...)):
    if 'roots' in body:SETTINGS['roots']=[str(x) for x in body['roots'] if str(x).strip()]
    if 'workers' in body:SETTINGS['workers']=max(1,min(os.cpu_count() or 1,int(body['workers'])))
    if 'turbo' in body:SETTINGS['turbo']=bool(body['turbo'])
    if 'nml' in body:SETTINGS['nml']=str(body['nml'])
    if 'thresholds' in body:
        values={k:float(body['thresholds'].get(k,matcher.DEFAULTS[k])) for k in matcher.DEFAULTS}
        if not all(0<=v<=100 for v in values.values()):raise HTTPException(400,'Prag mora biti 0–100.')
        SETTINGS['thresholds']=values
    CONFIG.write_text(json.dumps(SETTINGS,ensure_ascii=False,indent=2),encoding='utf-8');return SETTINGS

@app.post('/api/scan')
def scan(body:dict=Body(...)):
    try:
        roots=list(SETTINGS.get('roots',[]))
        if SETTINGS.get('nml'):
            from app.nml import parse_collection
            tracks,_=parse_collection(SETTINGS['nml']);roots.extend(t['path'] for t in tracks)
        return jobs.start('Pripremam sken',lambda:jobs.scan(roots,SETTINGS.get('workers'),SETTINGS.get('turbo'),bool(body.get('retry'))))
    except (ValueError,OSError) as exc:raise HTTPException(400,str(exc))

@app.post('/api/match')
def match(body:dict=Body(...)):
    try:return jobs.start('Pripremam proveru parova',lambda:jobs.match(SETTINGS.get('workers'),SETTINGS.get('turbo'),bool(body.get('thorough'))))
    except ValueError as exc:raise HTTPException(409,str(exc))

@app.post('/api/control/{action}')
def control(action:str):
    if action=='stop':jobs.STOP.set();jobs.PAUSE.clear()
    elif action=='pause':jobs.PAUSE.set();jobs.STATE['paused']=True
    elif action=='resume':jobs.PAUSE.clear();jobs.STATE['paused']=False
    else:raise HTTPException(400,'Nepoznata radnja')
    return {'ok':True}

@app.post('/api/pick/{kind}')
def pick(kind:str):
    if WINDOW is None:raise HTTPException(400,'Pokreni desktop EXE ili unesi putanju rucno.')
    import webview
    if kind=='folder':result=WINDOW.create_file_dialog(webview.FOLDER_DIALOG)
    elif kind=='import':result=WINDOW.create_file_dialog(webview.OPEN_DIALOG,file_types=('Skenirana baza (*.djscan)',))
    elif kind=='nml':result=WINDOW.create_file_dialog(webview.OPEN_DIALOG,file_types=('Traktor NML (*.nml)',))
    else:raise HTTPException(400,'Nepoznat izbor')
    return {'path':result[0] if result else None}

@app.post('/api/export/{kind}')
def export(kind:str):
    if kind not in ('results','full','csv'):raise HTTPException(400,'Nepoznat izvoz.')
    if jobs.STATE['running']:raise HTTPException(409,'Sacekaj ili zaustavi trenutni posao.')
    from datetime import datetime
    folder=storage.DATA/'Exports';folder.mkdir(exist_ok=True);base=folder/('DJ-Miljan-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if kind=='csv':
        path=base.with_suffix('.csv')
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(['path','status','error','hash','seconds'])
            for r in storage.rows('SELECT * FROM files ORDER BY path'):writer.writerow([r['path'],r['status'],r['error'],r['hash'],r['seconds']])
        return {'path':str(path)}
    path=base.with_suffix('.djscan' if kind=='full' else '.djmatches')
    return storage.export(path,kind=='full',SETTINGS.get('thresholds'))

@app.post('/api/import')
def import_cache(body:dict=Body(...)):
    path=str(body.get('path') or '')
    if not Path(path).is_file():raise HTTPException(400,'Izaberi postojecu .djscan bazu.')
    try:return jobs.start('Uvozim / Relocate skenirane baze',lambda:storage.import_full(path,SETTINGS.get('roots',[]),jobs.STOP))
    except ValueError as exc:raise HTTPException(409,str(exc))

@app.get('/api/files')
def files(q:str='',offset:int=0):
    return storage.rows('SELECT * FROM files WHERE path LIKE ? ORDER BY path LIMIT 200 OFFSET ?',('%'+q+'%',max(0,offset)))

@app.get('/api/pairs')
def pairs(q:str='',all_results:bool=False):
    catalogue={r['hash']:r for r in storage.rows("SELECT * FROM files WHERE status='ok'")};feedback={(r['a'],r['b']):r['value'] for r in storage.rows('SELECT * FROM feedback')};out=[]
    for row in storage.rows('SELECT * FROM pairs WHERE engine=?',(ENGINE,)):
        a=catalogue.get(row['a']);b=catalogue.get(row['b'])
        if not a or not b:continue
        if q and q.casefold() not in (a['path']+' '+b['path']).casefold():continue
        metrics=json.loads(row['metrics']);accepted=matcher.decision(metrics,SETTINGS.get('thresholds'));vote=feedback.get((row['a'],row['b']),0)
        if not all_results and (not accepted or vote<0):continue
        out.append({'a':a,'b':b,'metrics':metrics,'accepted':accepted,'feedback':vote,'score':max((metrics[k]['score'] for k in accepted),default=max((v['score'] for v in metrics.values()),default=0))})
    return sorted(out,key=lambda r:-r['score'])[:200]

@app.post('/api/feedback')
def feedback(body:dict=Body(...)):
    a,b=sorted((str(body['a']),str(body['b'])));value=int(body['value'])
    if value not in (-1,0,1):raise HTTPException(400,'Neispravna ocena.')
    with storage.connect() as c:c.execute('INSERT OR REPLACE INTO feedback VALUES(?,?,?)',(a,b,value))
    return {'ok':True}

@app.get('/api/preview/{identity}')
def preview(identity:str,start:float=0,end:float=15):
    found=storage.rows("SELECT path FROM files WHERE hash=? AND status='ok' LIMIT 1",(identity,))
    if not found:raise HTTPException(404)
    if not 0<=start<end or end-start>30:raise HTTPException(400,'Pregled traje najvise 30 sekundi.')
    try:
        result=subprocess.run([features.decoder(),'-v','error','-threads','1','-ss',str(start),'-i',found[0]['path'],'-t',str(end-start),'-ac','2','-ar','22050','-f','wav','pipe:1'],capture_output=True,timeout=15,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return Response(result.stdout,media_type='audio/wav')
    except (OSError,subprocess.SubprocessError):raise HTTPException(400,'Fajl ne moze da se preslusa.')
