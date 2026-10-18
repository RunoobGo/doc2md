"""Legacy `.doc` → `.docx` conversion (CH-487).

DOC is an OLE compound binary that neither python-docx nor pdfplumber can read.
The pipeline therefore shells out to an installed converter, parses the produced
DOCX with the existing reader, and keeps the original `.doc` hash as document
identity. Content never enters logs; temporary files live under a private temp
directory and are removed by the caller's context manager.

Backends:
- LibreOffice headless (`soffice`) — preferred; cross-platform.
- Microsoft Word COM via PowerShell — Windows fallback when Word is installed.
No shell interpolation is used: paths are passed as argv elements, and the
PowerShell script is a fixed string written to the private temp directory.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from ..security.perms import ensure_private

MAGIC_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class LegacyDocError(RuntimeError):
    """Conversion failed; stage is a stable diagnostic label (no document text)."""

    def __init__(self, stage: str):
        super().__init__(stage)
        self.stage = stage


def find_libreoffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates = [
        Path(program_files) / "LibreOffice" / "program" / "soffice.exe",
        Path(program_files) / "LibreOffice" / "program" / "soffice",
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        Path("/usr/lib/libreoffice/program/soffice"),
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def find_word() -> bool:
    if os.name != "nt" or shutil.which("powershell") is None:
        return False
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    for version in ("Office16", "Office15", "Office14"):
        p = Path(program_files) / "Microsoft Office" / "root" / version / "WINWORD.EXE"
        if p.is_file():
            return True
    return False


def available_backend(requested: str = "auto") -> str | None:
    requested = (requested or "auto").lower()
    if requested == "none":
        return None
    if requested in ("auto", "libreoffice") and find_libreoffice():
        return "libreoffice"
    if requested in ("auto", "word") and find_word():
        return "word"
    return None


def _libreoffice_cmd(soffice: str, src: Path, out_dir: Path, profile: Path) -> list[str]:
    return [
        soffice, "--headless", "--norestore",
        f"-env:UserInstallation={profile.as_uri()}",
        "--convert-to", "docx", "--outdir", str(out_dir), str(src),
    ]


_WORD_SCRIPT = r"""
param(
  [Parameter(Mandatory=$true)][string]$In,
  [Parameter(Mandatory=$true)][string]$Out
)
$ErrorActionPreference = "Stop"
$word = $null
$doc = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $word.AutomationSecurity = 3
  $doc = $word.Documents.Open($In, $false, $true)
  $doc.SaveAs2($Out, 16)
  $doc.Close($false)
  $doc = $null
} finally {
  if ($doc -ne $null) { try { $doc.Close($false) } catch {} }
  if ($word -ne $null) { try { $word.Quit() } catch {} }
}
""".strip()


def _run(cmd: list[str], timeout_s: int) -> None:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout_s, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise LegacyDocError("timeout") from exc
    except OSError as exc:
        raise LegacyDocError("backend_failed") from exc
    if proc.returncode != 0:
        raise LegacyDocError("backend_failed")


def _validate_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                raise LegacyDocError("invalid_output")
    except (OSError, zipfile.BadZipFile) as exc:
        raise LegacyDocError("invalid_output") from exc


def convert_doc_to_docx(src: str | Path, *, work_dir: str | Path,
                        backend: str = "auto", timeout_s: int = 120) -> Path:
    """Convert ``src`` into ``work_dir/input.docx`` and return its path.

    Caller provides a per-conversion private directory (usually a
    ``TemporaryDirectory``) and owns its cleanup.
    """
    src = Path(src)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    ensure_private(work)
    inp = work / "input.doc"
    out = work / "input.docx"
    if inp.resolve() != src.resolve():
        shutil.copy2(src, inp)
    chosen = available_backend(backend)
    if chosen is None:
        raise LegacyDocError("backend_unavailable")
    timeout = max(1, int(timeout_s))
    if chosen == "libreoffice":
        soffice = find_libreoffice()
        if not soffice:
            raise LegacyDocError("backend_unavailable")
        _run(_libreoffice_cmd(soffice, inp, work, work / "lo-profile"), timeout)
    else:
        script = work / "convert-doc.ps1"
        script.write_text(_WORD_SCRIPT, encoding="utf-8")
        powershell = shutil.which("powershell") or "powershell.exe"
        _run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
              "-File", str(script), "-In", str(inp), "-Out", str(out)], timeout)
    if not out.is_file():
        raise LegacyDocError("no_output")
    _validate_docx(out)
    return out


def read_legacy_doc(path: str | Path, *, page_span: tuple[int, int] | None = None,
                    backend: str = "auto", timeout_s: int = 120,
                    tmp_dir: str | Path | None = None):
    """Convert and parse a legacy .doc; the temp DOCX is deleted before return."""
    from .s1_parse import read_docx

    tmp_root = Path(tmp_dir) if tmp_dir else None
    if tmp_root is not None:
        tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="doc2md-doc-", dir=tmp_root) as td:
        ensure_private(td)
        docx_path = convert_doc_to_docx(path, work_dir=td, backend=backend,
                                        timeout_s=timeout_s)
        pages = read_docx(docx_path)
    if page_span:
        return pages[page_span[0]:page_span[1]]
    return pages
