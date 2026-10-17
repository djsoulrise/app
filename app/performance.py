"""Resource policy shared by scans and comparison workers."""
import os
from . import config

def enabled():
    return bool(config.SETTINGS.get('gig_mode', False))

def workers():
    return max(1,min(4,int(config.SETTINGS.get('audio_workers',config.SETTINGS.get('scan_workers',4))),max(1,(os.cpu_count() or 2)-2)))

def low_priority():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
    else:
        try: os.nice(10)
        except OSError: pass

def initialize_worker(stop=None,db_file=None):
    global STOP
    STOP=stop
    if db_file is not None:
        from . import db
        from pathlib import Path
        db.DB_FILE=Path(db_file)
    os.environ['DJ_AUDIO_WORKER']='1'
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMBA_NUM_THREADS'):
        os.environ[key]='1'
    from threadpoolctl import threadpool_limits
    global THREAD_LIMIT
    THREAD_LIMIT=threadpool_limits(limits=1)
    low_priority()

STOP=None


def worker_probe():
    return {"pid":os.getpid(),"threads":os.environ.get("OMP_NUM_THREADS"),"worker":os.environ.get("DJ_AUDIO_WORKER")}
