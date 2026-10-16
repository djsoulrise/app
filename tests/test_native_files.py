import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import native_files


class FilePathTests(unittest.TestCase):
    def test_existing_unicode_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'Niš, žurka & pesma 90%.flac'
            path.write_bytes(b'audio')
            with patch.object(native_files.db,'one',return_value={'path':str(path)}):
                self.assertEqual(native_files.resolve_track_path(1), str(path.resolve()))

    def test_exact_unique_relocated_file_is_found(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'unique song.flac'
            path.write_bytes(b'audio')
            with patch.object(native_files.db,'one',return_value={'path':r'Z:\old root\unique song.flac'}), patch.dict(native_files.SETTINGS,{'music_folders':[folder]}):
                self.assertEqual(native_files.resolve_track_path(1), str(path.resolve()))

    def test_ambiguous_relocated_file_is_never_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            for sub in ('first','second'):
                directory = Path(folder)/sub; directory.mkdir()
                (directory/'song.mp3').write_bytes(b'audio')
            with patch.object(native_files.db,'one',return_value={'path':r'Z:\missing\song.mp3'}), patch.dict(native_files.SETTINGS,{'music_folders':[folder]}):
                with self.assertRaisesRegex(FileNotFoundError,'vise fajlova'):
                    native_files.resolve_track_path(1)

    def test_missing_file_is_reported(self):
        with patch.object(native_files.db,'one',return_value={'path':r'Z:\missing\not-here.mp3'}), patch.dict(native_files.SETTINGS,{'music_folders':[]}):
            with self.assertRaises(FileNotFoundError):
                native_files.resolve_track_path(1)

    @unittest.skipUnless(sys.platform=='win32', 'Windows native FileDrop')
    def test_native_unicode_drag_payload(self):
        self.assertTrue(native_files.verify_drag_payload(r'C:\Music\Niš, Žurka & song.flac'))

    @unittest.skipUnless(sys.platform=='win32', 'Windows native clipboard')
    def test_native_clipboard_contains_real_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/'Niš, copy song.flac')
            Path(path).write_bytes(b'audio')
            native_files.copy_song(path)
            Thread, ThreadStart, ApartmentState = native_files._forms()
            result=[]
            def read():
                from System.Windows.Forms import Clipboard
                data=Clipboard.GetFileDropList()
                result.append(data.Count==1 and data[0]==path)
                Clipboard.Clear()  # This test only runs in the CI Windows test environment.
            worker=Thread(ThreadStart(read));worker.SetApartmentState(ApartmentState.STA)
            worker.Start();self.assertTrue(worker.Join(10000))
            self.assertEqual(result,[True])


if __name__=='__main__':
    unittest.main()
