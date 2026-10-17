"""Resource policy shared by scans and comparison workers."""
import os
from . import config

def enabled():
    return bool(config.SETTINGS.get('gig_mode', False))

def memory_limit():
    """Keep headroom for Windows; 768 MiB per independent analysis process."""
    try:
        if os.name=='nt':
            import ctypes
            class Memory(ctypes.Structure):
                _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
            value=Memory();value.length=ctypes.sizeof(value)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):raise OSError()
            available=value.available
        else:
            from pathlib import Path
            line=next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))
            available=int(line.split()[1])*1024
        return max(1,int(available*.7//(768*1024*1024)))
    except (OSError,ValueError,StopIteration):return 4

def workers():
    cpu=max(1,os.cpu_count() or 1)
    if config.SETTINGS.get('scan_turbo',False):return min(cpu,memory_limit())
    return max(1,min(cpu,int(config.SETTINGS.get('audio_workers',config.SETTINGS.get('scan_workers',min(4,max(1,cpu-2)))))))


def executable_path():
    import sys
    if os.name=='nt' and getattr(sys,'frozen',False):
        import ctypes
        value=ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetModuleFileNameW(None,value,len(value)):raise OSError('Nije moguce pronaci aktivni EXE.')
        return value.value
    return sys.executable


def spawn_context():
    """Frozen workers must launch the running EXE, never a build-host Python."""
    import multiprocessing,sys
    from pathlib import Path
    exe=executable_path()
    if not Path(exe).is_file():raise RuntimeError('EXE za radnike nije dostupan: '+exe+'. Zatvori aplikaciju i pokreni je iz stalnog foldera.')
    if os.name=='nt' and getattr(sys,'frozen',False):
        multiprocessing.set_executable(exe)
        import multiprocessing.popen_spawn_win32 as winspawn
        # A frozen application is not a venv redirector. Python's venv shortcut
        # can otherwise substitute sys._base_executable from the build machine.
        winspawn.WINENV=False
    try:os.getcwd()
    except OSError:os.chdir(config.DATA)
    return multiprocessing.get_context('spawn')


def runtime_status():
    import sys,multiprocessing.spawn
    def exists(value):
        try:return os.path.isfile(value)
        except (OSError,TypeError):return False
    exe=executable_path()
    return dict(executable=exe,executable_exists=exists(exe),python_base=getattr(sys,'_base_executable',None),
                spawn_executable=os.fsdecode(multiprocessing.spawn.get_executable()),frozen=bool(getattr(sys,'frozen',False)))


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
