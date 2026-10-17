# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_all

hiddenimports = collect_submodules('uvicorn') + ['app.restore_smoke', 'app.window_surface', 'app.shortcuts']

a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[('app/static', 'app/static')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch','torchaudio','torchvision','onnx','onnxruntime','librosa','numpy','scipy','mido','rtmidi','imageio_ffmpeg','numba'],
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
    name='DJ Miljan Assistant Lite',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

