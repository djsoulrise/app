"""Windows file operations. No Traktor automation or playback controls."""
import ntpath
import os
import sys
import threading
import urllib.parse

from . import db
from .config import SETTINGS

_drag_lock = threading.Lock()


def resolve_track_path(track_id):
    track = db.one('SELECT path FROM tracks WHERE id=?', (track_id,))
    if not track:
        raise FileNotFoundError('Pesma nije pronadjena u biblioteci.')
    raw = (track['path'] or '').strip()
    if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1]
    if '\x00' in raw:
        raise ValueError('Putanja sadrzi neispravan znak.')
    candidates = [os.path.normpath(os.path.expandvars(os.path.expanduser(raw)))] if raw else []
    if raw.startswith('file:'):
        parsed = urllib.parse.urlsplit(raw)
        uri_path = urllib.parse.unquote(parsed.path)
        if parsed.netloc and parsed.netloc != 'localhost':
            uri_path = '//' + parsed.netloc + uri_path
        if len(uri_path) > 2 and uri_path[0] == '/' and uri_path[2] == ':':
            uri_path = uri_path[1:]
        candidates.append(os.path.normpath(uri_path))
    elif '%' in raw:
        candidates.append(os.path.normpath(urllib.parse.unquote(raw)))
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    # Repair only an unambiguous exact filename within configured music roots.
    # Never pick a fuzzy artist/title match or an arbitrary duplicate.
    name = ntpath.basename(raw).casefold()
    found = set()
    for root in SETTINGS.get('music_folders', []):
        if not root or not os.path.isdir(root):
            continue
        for folder, _, files in os.walk(root):
            for filename in files:
                if filename.casefold() == name:
                    found.add(os.path.abspath(os.path.join(folder, filename)))
                    if len(found) > 1:
                        raise FileNotFoundError('Putanja nije dostupna, a postoji vise fajlova istog naziva. Osvezi kolekciju da bi izabrao tacan fajl: ' + raw)
    if len(found) == 1:
        return found.pop()
    raise FileNotFoundError('Audio fajl ne postoji na sacuvanoj putanji. Proveri disk / muzicke foldere i osvezi kolekciju: ' + (raw or '(prazna putanja)'))


def _windows():
    if sys.platform != 'win32':
        raise OSError('Ova opcija radi samo na Windows DJ racunaru.')


def reveal_file(path):
    """Select the actual Unicode path through the shell API, without command quoting."""
    _windows()
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32')
    ole = ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    shell.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    shell.SHParseDisplayName.restype = ctypes.c_long
    shell.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.DWORD]
    shell.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    outcome = []
    def run():
        pidl = ctypes.c_void_p()
        initialized = ole.CoInitializeEx(None, 2)
        try:
            if initialized < 0:
                raise OSError(f'Windows COM nije spreman: {initialized}')
            result = shell.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None)
            if result < 0:
                raise OSError(f'Windows ne moze da pronadje fajl: {path} ({result})')
            result = shell.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
            if result < 0:
                raise OSError(f'Windows Explorer nije otvorio fajl: {result}')
        except Exception as exc:
            outcome.append(exc)
        finally:
            if pidl:
                ole.CoTaskMemFree(pidl)
            if initialized >= 0:
                ole.CoUninitialize()
    worker = threading.Thread(target=run, daemon=True)
    worker.start(); worker.join(15)
    if worker.is_alive():
        raise OSError('Windows Explorer nije odgovorio na vreme.')
    if outcome:
        raise outcome[0]


def _forms():
    _windows()
    import clr
    clr.AddReference('System.Windows.Forms')
    clr.AddReference('System.Drawing')
    from System.Threading import Thread, ThreadStart, ApartmentState
    return Thread, ThreadStart, ApartmentState


def _file_data(path):
    from System.Collections.Specialized import StringCollection
    from System.Windows.Forms import DataObject
    from System.IO import MemoryStream
    from System import Array, Byte
    files = StringCollection()
    files.Add(path)
    data = DataObject()
    data.SetFileDropList(files)
    # COPY only: never ask Windows to move/delete the user's music file.
    data.SetData('Preferred DropEffect', MemoryStream(Array[Byte]([1, 0, 0, 0])))
    return data


def copy_song(path):
    Thread, ThreadStart, ApartmentState = _forms()
    errors = []
    def run():
        try:
            from System.Windows.Forms import Clipboard
            Clipboard.SetDataObject(_file_data(path), True, 5, 150)
        except Exception as exc:
            errors.append(str(exc))
    worker = Thread(ThreadStart(run))
    worker.SetApartmentState(ApartmentState.STA)
    worker.IsBackground = True
    worker.Start()
    if not worker.Join(10000):
        raise OSError('Windows clipboard nije odgovorio na vreme.')
    if errors:
        raise OSError('Fajl nije kopiran u clipboard: ' + errors[0])


def verify_drag_payload(path):
    """Build check: verify FileDrop payload without opening UI or touching the clipboard."""
    Thread, ThreadStart, ApartmentState = _forms()
    result, errors = [], []
    def run():
        try:
            data = _file_data(path)
            files = data.GetFileDropList()
            result.append(data.ContainsFileDropList() and files.Count == 1 and files[0] == path)
        except Exception as exc:
            errors.append(str(exc))
    worker = Thread(ThreadStart(run))
    worker.SetApartmentState(ApartmentState.STA)
    worker.IsBackground = True
    worker.Start()
    if not worker.Join(10000) or errors:
        raise OSError('FileDrop test nije prosao: ' + '; '.join(errors))
    return bool(result and result[0])


def drag_song(path):
    """Show a native file drag source; the user chooses the stopped deck manually."""
    Thread, ThreadStart, ApartmentState = _forms()
    if not _drag_lock.acquire(blocking=False):
        raise OSError('Prozor za prevlacenje je vec otvoren. Prevuci pesmu ili prvo zatvori taj prozor.')
    ready = threading.Event()
    errors = []
    def run():
        form = None
        try:
            from System.Windows.Forms import (Application, Form, Label, Button, DockStyle,
                DragDropEffects, MouseButtons, FormStartPosition, Cursor)
            from System.Drawing import Size, Point
            form = Form()
            form.Text = 'Prevuci pesmu u zaustavljeni Traktor deck'
            form.Size = Size(520, 190)
            form.TopMost = True
            form.StartPosition = FormStartPosition.Manual
            form.Location = Point(max(0, Cursor.Position.X-250), max(0, Cursor.Position.Y-70))
            info = Label()
            info.Text = 'Drzi levi klik na nazivu i prevuci fajl u deck koji si zaustavio.\nAplikacija ne bira deck i ne salje komande Traktoru.'
            info.Dock = DockStyle.Top
            info.Height = 55
            handle = Button()
            handle.Text = os.path.basename(path)
            handle.Dock = DockStyle.Fill
            def on_down(sender, event):
                if event.Button != MouseButtons.Left:
                    return
                try:
                    effect = handle.DoDragDrop(_file_data(path), DragDropEffects.Copy)
                    if effect == DragDropEffects.Copy:
                        form.Close()
                except Exception as exc:
                    from System.Windows.Forms import MessageBox
                    MessageBox.Show('Prevlacenje nije uspelo: ' + str(exc))
            handle.MouseDown += on_down
            form.Controls.Add(handle)
            form.Controls.Add(info)
            form.Shown += lambda sender, event: ready.set()
            Application.Run(form)
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if form is not None:
                form.Dispose()
            _drag_lock.release()
            ready.set()
    worker = Thread(ThreadStart(run))
    worker.SetApartmentState(ApartmentState.STA)
    worker.IsBackground = True
    try:
        worker.Start()
    except Exception:
        _drag_lock.release()
        raise
    if not ready.wait(10):
        raise OSError('Prozor za prevlacenje se jos pokrece. Proveri otvorene prozore.')
    if errors:
        raise OSError('Prevlacenje nije pokrenuto: ' + errors[0])
