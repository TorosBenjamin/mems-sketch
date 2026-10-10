# PyInstaller spec for the downloadable app: one folder with Python, Qt, the
# engine and the package (see .github/workflows/app.yml). Run from the
# repository root:  pyinstaller packaging/mems-sketch.spec
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 - set by PyInstaller
ICONS = ROOT / "packaging" / "icons"

datas = collect_data_files("mems_sketch")  # built-in components, icons, examples in the package
datas += copy_metadata("mems-sketch")  # its version and entry points (exporters are found by them)
hidden = collect_submodules("mems_sketch")  # plugins are imported by name, at run time

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
icon = {
    "win32": ICONS / "mems-sketch.ico",
    "darwin": ICONS / "mems-sketch.icns",
}.get(sys.platform)
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mems-sketch",
    console=False,  # a window application (--version and --self-test print to a console when there is one)
    icon=str(icon) if icon and icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="mems-sketch")  # noqa: F821
if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="MEMS Sketch.app",
        icon=str(icon) if icon and icon.exists() else None,
        bundle_identifier="io.github.torosbenjamin.mems-sketch",
        info_plist={"NSHighResolutionCapable": True},
    )
