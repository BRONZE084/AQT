# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AQT — A-Share Quant Toolkit Windows app."""

import sys
from pathlib import Path

_root = Path(SPECPATH)  # directory containing this .spec file

a = Analysis(
    [_root / "run_aqt.py"],
    pathex=[str(_root)],
    binaries=[],
    datas=[
        # Static web assets
        (str(_root / "aqt" / "static" / "index.html"), "aqt/static"),
        (str(_root / "aqt" / "static" / "app.js"), "aqt/static"),
        (str(_root / "aqt" / "static" / "styles.css"), "aqt/static"),
        (str(_root / "aqt" / "static" / "echarts.min.js"), "aqt/static"),
        # akshare data files (stock list cache etc.)
        (str(_root / "aqt" / "strategies" / "__init__.py"), "aqt/strategies"),
        (str(_root / "aqt" / "strategies" / "ma_crossover.py"), "aqt/strategies"),
        (str(_root / "aqt" / "strategies" / "breakout.py"), "aqt/strategies"),
        (str(_root / "aqt" / "strategies" / "mean_reversion.py"), "aqt/strategies"),
    ],
    hiddenimports=[
        "aqt",
        "aqt.web",
        "aqt.backtest",
        "aqt.cli",
        "aqt.data",
        "aqt.fetcher",
        "aqt.math_utils",
        "aqt.models",
        "aqt.planner",
        "aqt.rules",
        "aqt.strategy",
        "aqt.strategies",
        "aqt.strategies.ma_crossover",
        "aqt.strategies.breakout",
        "aqt.strategies.mean_reversion",
        "csv",
        "json",
        "mimetypes",
        "threading",
        "webbrowser",
        "urllib.request",
        "http.server",
        "akshare",
        "curl_cffi",
        "subprocess",
    ],
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
    name="AQT",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_root / "aqt" / "static" / "favicon.ico") if (_root / "aqt" / "static" / "favicon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AQT",
)
