# PyInstaller spec for the frozen wowlab builds (ADR-0038, M17-08, M17-09).
#
# Run by the release workflow only, with PyInstaller from the `packaging`
# dependency group, in a clean virtual environment that holds the released
# wheel and its locked dependencies, never the working tree:
#
#     pyinstaller --noconfirm --clean --distpath DIST --workpath WORK packaging/wowlab.spec
#
# One-folder mode (EXE with exclude_binaries=True, then COLLECT), a console
# build of the ordinary `wowlab` command, no UPX. On macOS the folder is
# wrapped in `wowlab.app` (BUNDLE) with PyInstaller's ad-hoc signature, which
# carries no identity. On Windows the folder itself is the app: `wowlab/`
# with `wowlab.exe` and `_internal/`, which the release zips as it is.
# tests/packaging/test_macos_app.py pins this file's SHA-256: a change here
# is a PR that says why.
import importlib.metadata
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = SPECPATH  # noqa: F821 -- PyInstaller defines it while it runs a spec

# Only the hidden imports listed here, which the test reads: every module of
# wowlab_core (the CLI imports its commands lazily), and nothing of anyone
# else's beyond what PyInstaller's own hooks find.
HIDDEN_IMPORTS = collect_submodules("wowlab_core")
# The package data the wheel carries: the UI's templates, fonts and viewer,
# the bundled lab-addon, and the TOML tables.
DATAS = collect_data_files("wowlab_core")
VERSION = importlib.metadata.version("wowlab")
# The build environment holds PyInstaller and its own dependencies beside the
# wheel; none of them is wowlab's, so none is frozen into the app.
EXCLUDES = ["PyInstaller", "setuptools", "pkg_resources", "_distutils_hack", "distutils"]

a = Analysis(  # noqa: F821
    [f"{HERE}/launcher.py"],
    pathex=[],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="wowlab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="wowlab",
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="wowlab.app",
        icon=None,
        bundle_identifier="io.github.sandgraal.wowlab",
        version=VERSION,
        info_plist={
            "CFBundleName": "wowlab",
            "CFBundleDisplayName": "wowlab",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
        },
    )
