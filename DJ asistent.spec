# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_all

audio_data, audio_bins, audio_imports = collect_all('imageio_ffmpeg')
hiddenimports = ['app.main', 'app.audio_rhythm', 'app.grid_scan'] + audio_imports
hiddenimports += collect_submodules('uvicorn')
hiddenimports += ['app.chorus', 'app.restore_smoke', 'app.window_surface', 'app.shortcuts', 'mido.backends.rtmidi', 'rtmidi']


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=audio_bins,
    datas=[('app/static', 'app/static')] + audio_data,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DJ Miljan Assistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

