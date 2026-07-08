# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop_client.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('desktop_ui\\dist', 'desktop_ui\\dist')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['funasr', 'transformers', 'torch', 'tensorflow', 'pandas', 'scipy', 'sklearn', 'matplotlib', 'IPython', 'notebook'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoiceInput',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\flowvoice_hurricane_eye.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VoiceInput',
)
