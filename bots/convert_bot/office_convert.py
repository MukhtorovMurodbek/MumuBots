from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import formats
from convert_utils import Result, ConversionError, report

PRESENTATIONS = ("pptx", "ppt", "odp")
TEXT_DOCUMENTS = ("doc", "odt", "rtf")
SPREADSHEETS = ("xls", "ods")

_DIRECT = {
    "impress": ("pdf", "pptx", "ppt", "odp"),
    "writer": ("pdf", "docx", "doc", "odt", "rtf"),
    "calc": ("pdf", "xlsx", "xls", "ods"),
    "pdf": ("pptx", "odp"),
}

ONLY_HERE = ("pptx", "ppt", "odp", "doc", "odt", "rtf", "xls", "ods")

_FILTERS = {
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "docx": "docx:MS Word 2007 XML",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
}

SOFFICE_TIMEOUT_S = int(os.environ.get("CONVERT_OFFICE_TIMEOUT_SECONDS") or 600)

_RESTART = 81

_PROFILE_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="UseOpenCL" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""


def soffice_path() -> "str | None":
    explicit = os.environ.get("CBOT_SOFFICE")
    candidates = [explicit] if explicit else []
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates += ["/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice"]
    for candidate in candidates:
        if not candidate or not os.path.exists(candidate):
            continue
        real = Path(os.path.realpath(candidate))
        if real.name == "soffice.bin":
            return str(real)
        binary = real.parent / "soffice.bin"
        if binary.is_file():
            return str(binary)
    return None


def has_pdf_import() -> bool:
    binary = soffice_path()
    if not binary:
        return False
    program = Path(binary).parent
    return any(program.glob("libpdfimportlo.so")) or any(program.glob("pdfimport*.dll")) \
        or any(program.glob("libpdfimport*"))


def family_of(src_ext: str) -> "str | None":
    src_ext = formats.normalise(src_ext)
    if src_ext in PRESENTATIONS:
        return "impress"
    if src_ext in TEXT_DOCUMENTS or src_ext == "docx":
        return "writer"
    if src_ext in SPREADSHEETS or src_ext == "xlsx":
        return "calc"
    if src_ext == "pdf":
        return "pdf"
    return None


def handles(src_ext: str, target_ext: str) -> bool:
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    if src_ext in PRESENTATIONS or src_ext in TEXT_DOCUMENTS or src_ext in SPREADSHEETS:
        return True
    if target_ext in ONLY_HERE:
        return family_of(src_ext) is not None
    return src_ext == "docx" and target_ext in ("pdf", "png", "jpg") and formats.met("office")


def _run(binary: str, source: Path, target_ext: str, out_dir: Path, profile: Path,
         infilter: "str | None" = None) -> None:
    command = [binary, "--headless", "--invisible", "--norestore", "--nolockcheck", "--nodefault",
               "--nologo", "--nofirststartwizard", f"-env:UserInstallation={profile.as_uri()}"]
    if infilter:
        command.append(f"--infilter={infilter}")
    command += ["--convert-to", _FILTERS.get(target_ext, target_ext), "--outdir", str(out_dir), str(source)]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(profile),
           "TMPDIR": str(profile), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "SAL_USE_VCLPLUGIN": "svp"}
    for attempt in range(2):
        try:
            proc = subprocess.run(command, capture_output=True, text=True, env=env,
                                  timeout=SOFFICE_TIMEOUT_S, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ConversionError(
                f"LibreOffice was still working on that after {SOFFICE_TIMEOUT_S // 60} minutes, so it was stopped."
            ) from None
        except OSError as exc:
            raise ConversionError(f"LibreOffice couldn't be started here: {exc}") from None
        if proc.returncode == _RESTART and attempt == 0:
            continue
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
            raise ConversionError(
                f"LibreOffice couldn't convert that file (exit {proc.returncode}). {tail[0][:200]}".strip()
            )
        return


def soffice(in_path, src_ext: str, target_ext: str, work_dir, infilter: "str | None" = None) -> Path:
    binary = soffice_path()
    if not binary:
        raise ConversionError("Office documents can't be converted on this host: LibreOffice isn't installed.")
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    room = Path(work_dir) / f"office-{os.getpid()}-{target_ext}"
    shutil.rmtree(room, ignore_errors=True)
    profile, inbox, outbox = room / "profile", room / "in", room / "out"
    for folder in (profile / "user", inbox, outbox):
        folder.mkdir(parents=True, exist_ok=True)
    (profile / "user" / "registrymodifications.xcu").write_text(_PROFILE_SETTINGS, encoding="utf-8")
    source = inbox / f"source.{src_ext}"
    shutil.copyfile(in_path, source)
    _run(binary, source, target_ext, outbox, profile, infilter)
    produced = outbox / f"source.{target_ext}"
    if not produced.is_file() or produced.stat().st_size == 0:
        raise ConversionError(
            f"LibreOffice couldn't open that .{src_ext}. It may be damaged, password-protected, "
            "or not really a ." + src_ext + " file."
        )
    kept = Path(work_dir) / f"{room.name}.{target_ext}"
    produced.replace(kept)
    shutil.rmtree(room, ignore_errors=True)
    return kept


def convert(in_path, src_ext: str, target_ext: str, work_dir, stem: str = "converted") -> Result:
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    work_dir = Path(work_dir)
    family = family_of(src_ext)
    if family is None:
        raise ConversionError(f"Nothing here converts a .{src_ext} to .{target_ext}.")
    out_path = work_dir / f"{stem}.{target_ext}"
    report(0, 2)

    if target_ext in _DIRECT[family]:
        infilter = "impress_pdf_import" if family == "pdf" else None
        written = soffice(in_path, src_ext, target_ext, work_dir, infilter)
        written.replace(out_path)
        report(2, 2)
        return Result(out_path)

    if family in ("impress", "writer"):
        import doc_convert
        pdf = soffice(in_path, src_ext, "pdf", work_dir)
        report(1, 2)
        try:
            return doc_convert.convert(pdf, "pdf", target_ext, work_dir, stem)
        finally:
            pdf.unlink(missing_ok=True)

    if family == "calc":
        import data_convert
        xlsx = soffice(in_path, src_ext, "xlsx", work_dir)
        report(1, 2)
        try:
            return data_convert.convert(xlsx, "xlsx", target_ext, work_dir, stem)
        finally:
            xlsx.unlink(missing_ok=True)

    raise ConversionError(f"Nothing here converts a .{src_ext} to .{target_ext}.")


def selftest() -> None:
    import tempfile
    import pymupdf
    with tempfile.TemporaryDirectory(prefix="office-check-") as folder:
        folder = Path(folder)
        doc = pymupdf.open()
        for number in (1, 2):
            page = doc.new_page(width=960, height=540)
            page.insert_text((60, 120), f"Office check slide {number}", fontsize=36)
        doc.save(folder / "check.pdf")
        doc.close()
        try:
            deck = convert(folder / "check.pdf", "pdf", "pptx", folder, "deck").path
            old = convert(deck, "pptx", "ppt", folder, "old").path
            back = convert(old, "ppt", "pdf", folder, "back").path
            with pymupdf.open(back) as again:
                text = " ".join(page.get_text() for page in again)
                pages = again.page_count
        except ConversionError as exc:
            print(f"  FAIL  LibreOffice: {exc}")
            raise SystemExit(1) from None
        if pages != 2 or "Office check slide 2" not in text:
            print(f"  FAIL  LibreOffice: the round trip came back as {pages} page(s): {text[:120]!r}")
            raise SystemExit(1)
        (folder / "check.html").write_text("<p>Office check document</p>", encoding="utf-8")
        (folder / "check.csv").write_text("name,score\nOffice check,1\n", encoding="utf-8")
        try:
            odt = soffice(folder / "check.html", "html", "odt", folder)
            doc = convert(odt, "odt", "doc", folder, "doc").path
            docx = convert(doc, "doc", "docx", folder, "docx").path
            text = Path(convert(docx, "docx", "pdf", folder, "docpdf").path)
            ods = soffice(folder / "check.csv", "csv", "ods", folder)
            xls = convert(ods, "ods", "xls", folder, "xls").path
            csv = convert(xls, "xls", "csv", folder, "csv").path.read_text(encoding="utf-8")
        except ConversionError as exc:
            print(f"  FAIL  LibreOffice (Writer or Calc): {exc}")
            raise SystemExit(1) from None
        with pymupdf.open(text) as again:
            document = " ".join(page.get_text() for page in again)
        if "Office check document" not in document or "Office check,1" not in csv:
            print(f"  FAIL  LibreOffice: Writer gave {document[:80]!r}, Calc gave {csv[:80]!r}")
            raise SystemExit(1)
