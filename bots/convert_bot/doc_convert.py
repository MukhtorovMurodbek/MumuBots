from __future__ import annotations

import html as html_module
import io
from pathlib import Path

import formats
from convert_utils import Result, ConversionError, PAGE_DPI, convert_image, report, zip_files

_MUPDF_NATIVE = ("pdf", "epub", "mobi", "fb2", "cbz", "xps", "svg", "html", "txt")

WRITES = frozenset({"pdf", "png", "jpg", "webp", "tiff", "svg", "txt", "html", "md", "docx"})

_PAGE = "a4"
_MARGIN = 40


def _fitz():
    try:
        import pymupdf
        return pymupdf
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError(
            "The document engine isn't installed on this host, so PDF and "
            "e-book conversion isn't available."
        ) from exc


def _open(in_path, src_ext: str):
    pymupdf = _fitz()
    if src_ext in _MUPDF_NATIVE:
        try:
            return pymupdf.open(in_path)
        except Exception as exc:
            raise ConversionError(f"Couldn't read that {src_ext.upper()}: {exc}") from exc
    pdf_bytes = _to_pdf_bytes(in_path, src_ext)
    return pymupdf.open(stream=pdf_bytes, filetype="pdf")


def _page_count(doc) -> int:
    count = doc.page_count
    if count > formats.MAX_PAGES:
        raise ConversionError(
            f"That document is {count} pages, over the {formats.MAX_PAGES}-page "
            "limit for one conversion. Split it and send it in parts."
        )
    return count


def _check_page_sizes(doc, pages: int) -> None:
    for index in range(pages):
        rect = doc[index].rect
        megapixels = (rect.width * PAGE_DPI / 72) * (rect.height * PAGE_DPI / 72) / 1_000_000
        if megapixels > formats.MAX_PAGE_MEGAPIXELS:
            raise ConversionError(
                f"Page {index + 1} is {rect.width / 72:.0f} by {rect.height / 72:.0f} inches, "
                f"which would be {megapixels:,.0f} megapixels as a picture -- over the "
                f"{formats.MAX_PAGE_MEGAPIXELS}-megapixel limit for one page."
            )


def _docx_to_html(in_path) -> str:
    try:
        import mammoth
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("Word documents aren't supported on this host.") from exc
    try:
        with open(in_path, "rb") as handle:
            return mammoth.convert_to_html(handle).value
    except Exception as exc:
        raise ConversionError(f"Couldn't read that .docx: {exc}") from exc


def _md_to_html(in_path) -> str:
    try:
        import markdown
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("Markdown isn't supported on this host.") from exc
    try:
        text = Path(in_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ConversionError(f"Couldn't read that file: {exc}") from exc
    return markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])


def _source_html(in_path, src_ext: str) -> str:
    if src_ext == "docx":
        return _docx_to_html(in_path)
    if src_ext == "md":
        return _md_to_html(in_path)
    if src_ext == "html":
        return Path(in_path).read_text(encoding="utf-8", errors="replace")
    if src_ext == "txt":
        body = html_module.escape(Path(in_path).read_text(encoding="utf-8", errors="replace"))
        return f"<pre>{body}</pre>"
    doc = _open(in_path, src_ext)
    try:
        pages = _page_count(doc)
        return "\n".join(doc[index].get_text("html") for index in range(pages))
    finally:
        doc.close()


def html_to_pdf(source_html: str, out_path) -> None:
    pymupdf = _fitz()
    try:
        story = pymupdf.Story(html=f"<html><body>{source_html}</body></html>")
        page_rect = pymupdf.paper_rect(_PAGE)
        frame = page_rect + (_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
        writer = pymupdf.DocumentWriter(out_path if hasattr(out_path, "write") else str(out_path))
        more = 1
        pages = 0
        while more:
            device = writer.begin_page(page_rect)
            more, _ = story.place(frame)
            story.draw(device)
            writer.end_page()
            pages += 1
            if pages > formats.MAX_PAGES:
                writer.close()
                raise ConversionError(
                    f"That comes to more than {formats.MAX_PAGES} pages, which is "
                    "over the limit for one conversion."
                )
        writer.close()
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't lay that out as a PDF: {exc}") from exc


def _to_pdf_bytes(in_path, src_ext: str) -> bytes:
    _fitz()
    if src_ext in _MUPDF_NATIVE:
        doc = _open(in_path, src_ext)
        try:
            _page_count(doc)
            if src_ext == "pdf":
                return doc.tobytes()
            return doc.convert_to_pdf()
        finally:
            doc.close()
    buffer = io.BytesIO()
    html_to_pdf(_source_html(in_path, src_ext), buffer)
    return buffer.getvalue()


def _to_pdf(in_path, src_ext: str, out_path) -> None:
    if src_ext in ("docx", "md"):
        html_to_pdf(_source_html(in_path, src_ext), out_path)
        return
    doc = _open(in_path, src_ext)
    try:
        _page_count(doc)
        Path(out_path).write_bytes(doc.convert_to_pdf())
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't write that as a PDF: {exc}") from exc
    finally:
        doc.close()


def _render_pages(in_path, src_ext: str, target_ext: str, work_dir, stem: str) -> list[Path]:
    doc = _open(in_path, src_ext)
    produced: list[Path] = []
    try:
        pages = _page_count(doc)
        _check_page_sizes(doc, pages)
        for index in range(pages):
            report(index, pages)
            pixmap = doc[index].get_pixmap(dpi=PAGE_DPI)
            png_path = Path(work_dir) / f"{stem}-page{index + 1:03d}.png"
            pixmap.save(str(png_path))
            if target_ext == "png":
                produced.append(png_path)
                continue
            out_path = png_path.with_suffix(f".{target_ext}")
            try:
                convert_image(png_path, target_ext, out_path)
            finally:
                png_path.unlink(missing_ok=True)
            produced.append(out_path)
    except ConversionError:
        for path in produced:
            path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        for path in produced:
            path.unlink(missing_ok=True)
        raise ConversionError(f"Couldn't render that document: {exc}") from exc
    finally:
        doc.close()
    return produced


def _page_svgs(in_path, src_ext: str, work_dir, stem: str) -> list[Path]:
    doc = _open(in_path, src_ext)
    produced: list[Path] = []
    try:
        pages = _page_count(doc)
        for index in range(pages):
            report(index, pages)
            path = Path(work_dir) / f"{stem}-page{index + 1:03d}.svg"
            path.write_text(doc[index].get_svg_image(), encoding="utf-8")
            produced.append(path)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't write that page as SVG: {exc}") from exc
    finally:
        doc.close()
    return produced


def _pack(produced: list[Path], target_ext: str, work_dir, stem: str) -> Result:
    if not produced:
        raise ConversionError("That document had no pages in it.")
    if len(produced) == 1:
        final = Path(work_dir) / f"{stem}.{target_ext}"
        produced[0].replace(final)
        return Result(final, items=1)
    archive = Path(work_dir) / f"{stem}.zip"
    zip_files(produced, archive)
    for path in produced:
        path.unlink(missing_ok=True)
    return Result(archive, items=len(produced), zipped=True)


def _to_text(in_path, src_ext: str) -> str:
    if src_ext == "docx":
        try:
            import mammoth
            with open(in_path, "rb") as handle:
                return mammoth.extract_raw_text(handle).value
        except ImportError as exc:  # pragma: no cover - formats.py gates this
            raise ConversionError("Word documents aren't supported on this host.") from exc
        except Exception as exc:
            raise ConversionError(f"Couldn't read that .docx: {exc}") from exc
    if src_ext == "md":
        doc = _open(in_path, src_ext)
        try:
            return "\n".join(doc[index].get_text() for index in range(_page_count(doc)))
        finally:
            doc.close()
    doc = _open(in_path, src_ext)
    try:
        return "\n".join(doc[index].get_text() for index in range(_page_count(doc)))
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't extract the text: {exc}") from exc
    finally:
        doc.close()


_HEADING_RATIO = 1.25
_HEADING_2_RATIO = 1.6
_MIN_IMAGE_PIXELS = 64 * 64
_MAX_IMAGE_PIXELS = 50_000_000
_DOCX_PAGE_WIDTH_INCHES = 6.0


def _docx():
    try:
        import docx
        return docx
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError(
            "The Word writer isn't installed on this host, so converting to "
            "DOCX isn't available."
        ) from exc


def _body_size(doc) -> float:
    sizes: dict = {}
    for page in doc:
        for block in page.get_text("dict").get("blocks", ()):
            for line in block.get("lines", ()):
                for span in line.get("spans", ()):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    size = round(float(span.get("size") or 0), 1)
                    sizes[size] = sizes.get(size, 0) + len(text)
    if not sizes:
        return 0.0
    return max(sizes.items(), key=lambda pair: pair[1])[0]


def _line_text(line) -> str:
    return "".join(span.get("text") or "" for span in line.get("spans", ())).strip()


def _to_docx(in_path, src_ext: str, out_path, work_dir) -> None:
    docx = _docx()
    pymupdf = _fitz()
    document = docx.Document()
    with _open(in_path, src_ext) as doc:
        _page_count(doc)
        body_size = _body_size(doc)
        wrote_anything = False
        for index, page in enumerate(doc):
            report(index, len(doc))
            if index:
                document.add_page_break()
            for block in sorted(page.get_text("dict").get("blocks", ()),
                                key=lambda b: (round(b.get("bbox", (0, 0, 0, 0))[1]),
                                               round(b.get("bbox", (0, 0, 0, 0))[0]))):
                for line in block.get("lines", ()):
                    text = _line_text(line)
                    if not text:
                        continue
                    spans = [span for span in line.get("spans", ()) if (span.get("text") or "").strip()]
                    size = max((float(span.get("size") or 0) for span in spans), default=body_size)
                    bold = any(int(span.get("flags") or 0) & (1 << 4) for span in spans)
                    if body_size and size >= body_size * _HEADING_2_RATIO:
                        document.add_heading(text, level=1)
                    elif body_size and size >= body_size * _HEADING_RATIO:
                        document.add_heading(text, level=2)
                    else:
                        paragraph = document.add_paragraph()
                        run = paragraph.add_run(text)
                        run.bold = bold
                    wrote_anything = True
            _add_page_images(document, doc, page, pymupdf, work_dir)
    if not wrote_anything:
        raise ConversionError(
            "There is nothing in that file to put into a Word document -- it "
            "looks like scanned pages rather than text. Converting it to PNG "
            "or JPG gives the pages themselves."
        )
    document.save(str(out_path))


def _add_page_images(document, doc, page, pymupdf, work_dir) -> None:
    import io
    from PIL import Image as PILImage
    try:
        listed = page.get_images(full=True)
    except Exception:
        return
    for number, info in enumerate(listed):
        try:
            data = doc.extract_image(info[0])
            raw, width, height = data["image"], data.get("width", 0), data.get("height", 0)
            if width * height < _MIN_IMAGE_PIXELS or width * height > _MAX_IMAGE_PIXELS:
                continue
            picture = PILImage.open(io.BytesIO(raw))
            if picture.mode not in ("RGB", "L"):
                picture = picture.convert("RGB")
            buffer = io.BytesIO()
            picture.save(buffer, format="PNG")
            buffer.seek(0)
            inches = min(_DOCX_PAGE_WIDTH_INCHES, max(1.0, width / 96))
            from docx.shared import Inches
            document.add_picture(buffer, width=Inches(inches))
        except Exception:
            continue


def convert(in_path, src_ext: str, target_ext: str, work_dir, stem: str = "converted") -> Result:
    src_ext = formats.normalise(src_ext)
    target_ext = formats.normalise(target_ext)
    work_dir = Path(work_dir)
    out_path = work_dir / f"{stem}.{target_ext}"

    if target_ext == "pdf":
        _to_pdf(in_path, src_ext, out_path)
        return Result(out_path)

    if target_ext == "docx":
        _to_docx(in_path, src_ext, out_path, work_dir)
        return Result(out_path)

    if target_ext in ("png", "jpg", "webp", "tiff"):
        return _pack(_render_pages(in_path, src_ext, target_ext, work_dir, stem),
                     target_ext, work_dir, stem)

    if target_ext == "svg":
        return _pack(_page_svgs(in_path, src_ext, work_dir, stem), "svg", work_dir, stem)

    if target_ext == "txt":
        text = _to_text(in_path, src_ext)
        if not text.strip():
            raise ConversionError(
                "There is no text in that file to extract -- it looks like "
                "scanned pages rather than a document. Converting it to PNG "
                "or JPG gives the pages themselves."
            )
        out_path.write_text(text, encoding="utf-8")
        return Result(out_path)

    if target_ext == "html":
        out_path.write_text(
            "<!doctype html>\n<meta charset=\"utf-8\">\n" + _source_html(in_path, src_ext),
            encoding="utf-8",
        )
        return Result(out_path)

    if target_ext == "md":
        if src_ext != "docx":
            raise ConversionError(f"Nothing here turns a .{src_ext} into Markdown.")
        try:
            import mammoth
            with open(in_path, "rb") as handle:
                out_path.write_text(mammoth.convert_to_markdown(handle).value, encoding="utf-8")
        except ImportError as exc:  # pragma: no cover - formats.py gates this
            raise ConversionError("Word documents aren't supported on this host.") from exc
        except Exception as exc:
            raise ConversionError(f"Couldn't read that .docx: {exc}") from exc
        return Result(out_path)

    raise ConversionError(f"Nothing here converts a .{src_ext} to .{target_ext}.")
