"""Generate PyInstaller's Windows version resource from the shared app version."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from haffop import __version__

version = tuple(int(part) for part in __version__.split(".")) + (0,)
output = Path(sys.argv[1])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'Haffop health and performance'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductName', 'Haffop'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('OriginalFilename', 'Haffop.exe')
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
