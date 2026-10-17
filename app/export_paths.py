"""Resolve a writable export destination on this computer, never another profile."""
import ntpath
import os
import tempfile
from pathlib import Path
from . import config


def foreign_profile(value, home=None):
    parts=ntpath.normpath(str(value)).split('\\')
    home=ntpath.normpath(str(home or Path.home())).split('\\')
    return (len(parts)>2 and parts[1].casefold()=='users' and
            len(home)>2 and home[1].casefold()=='users' and
            parts[2].casefold()!=home[2].casefold())


def default_folder():
    # Windows Documents may be redirected to OneDrive or another volume.
    if os.name=='nt':
        try:
            import ctypes
            buffer=ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None,5,None,0,buffer)==0:
                return Path(buffer.value)/'DJ Miljan Assistant Exports'
        except (OSError,AttributeError):
            pass
    return Path.home()/'Documents'/'DJ Miljan Assistant Exports'


def resolve():
    saved=str(config.SETTINGS.get('export_dir') or '').strip()
    candidates=[]
    if saved and not (os.name=='nt' and foreign_profile(saved)):
        candidates.append(Path(saved).expanduser())
    candidates.extend((default_folder(),config.DATA/'Exports'))
    errors=[]
    for folder in dict.fromkeys(candidates):
        try:
            folder=folder.resolve()
            folder.mkdir(parents=True,exist_ok=True)
            # Probe actual write access before starting a long copy job.
            with tempfile.TemporaryFile(dir=folder):pass
            if str(folder)!=saved:
                config.SETTINGS['export_dir']=str(folder)
                config.save(config.SETTINGS)
            return folder
        except OSError as exc:
            errors.append(f'{folder}: {exc}')
    raise ValueError('Nema dostupnog foldera za izvoz. '+ '; '.join(errors))
