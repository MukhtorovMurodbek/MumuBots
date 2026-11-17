from __future__ import annotations

import csv
import html as html_module
import io
import json
import re
from pathlib import Path
from typing import NamedTuple

import formats
from convert_utils import Result, ConversionError, zip_files


class Table(NamedTuple):
    headers: list[str]
    rows: list[list]


WRITES = frozenset({"json", "yaml", "xlsx", "csv", "tsv", "md", "html", "pdf", "txt"})


_DELIMITERS = {"csv": ",", "tsv": "\t"}


def _read_text(in_path) -> str:
    try:
        return Path(in_path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise ConversionError(f"Couldn't read that file: {exc}") from exc


def _check_size(rows: int, columns: int) -> None:
    if rows > formats.MAX_ROWS:
        raise ConversionError(
            f"That is {rows:,} rows, over the {formats.MAX_ROWS:,}-row limit "
            "for one conversion."
        )
    if rows * max(columns, 1) > formats.MAX_CELLS:
        raise ConversionError(
            f"That is about {rows * columns:,} cells, over the "
            f"{formats.MAX_CELLS:,}-cell limit for one conversion."
        )


class _Budget:
    def __init__(self):
        self.rows = 0
        self.cells = 0

    def add(self, row) -> None:
        self.rows += 1
        self.cells += len(row)
        if self.rows > formats.MAX_ROWS + 1:
            raise ConversionError(
                f"That is over the {formats.MAX_ROWS:,}-row limit for one conversion.")
        if self.cells > formats.MAX_CELLS:
            raise ConversionError(
                f"That is over the {formats.MAX_CELLS:,}-cell limit for one conversion.")


def _read_delimited(in_path, src_ext: str) -> Table:
    text = _read_text(in_path)
    reader = csv.reader(io.StringIO(text), delimiter=_DELIMITERS[src_ext])
    rows = []
    budget = _Budget()
    try:
        for row in reader:
            budget.add(row)
            rows.append(row)
    except csv.Error as exc:
        raise ConversionError(f"That isn't a CSV this can read: {exc}.") from exc
    if not rows:
        raise ConversionError("That file is empty.")
    headers = [str(cell) for cell in rows[0]]
    _check_size(len(rows) - 1, len(headers))
    return Table(headers, rows[1:])


def _read_xlsx(in_path) -> list[tuple[str, Table]]:
    try:
        import openpyxl
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("Spreadsheets aren't supported on this host.") from exc
    try:
        book = openpyxl.load_workbook(in_path, read_only=True, data_only=True)
    except Exception as exc:
        raise ConversionError(f"Couldn't read that spreadsheet: {exc}") from exc
    sheets = []
    try:
        for sheet in book.worksheets:
            rows = []
            budget = _Budget()
            for row in sheet.iter_rows(values_only=True):
                if any(cell is not None for cell in row):
                    budget.add(row)
                    rows.append(list(row))
            if not rows:
                continue
            headers = ["" if cell is None else str(cell) for cell in rows[0]]
            _check_size(len(rows) - 1, len(headers))
            sheets.append((sheet.title, Table(headers, rows[1:])))
    finally:
        book.close()
    if not sheets:
        raise ConversionError("That spreadsheet has no data in it.")
    return sheets


def _read_json(in_path):
    try:
        return json.loads(_read_text(in_path))
    except json.JSONDecodeError as exc:
        raise ConversionError(f"That isn't valid JSON: line {exc.lineno}, {exc.msg}.") from exc
    except RecursionError as exc:
        raise ConversionError("That JSON is nested too deeply to convert.") from exc


def _refuse_expansion(obj) -> None:
    seen = 0
    stack = [obj]
    while stack:
        node = stack.pop()
        seen += 1
        if seen > formats.MAX_CELLS:
            raise ConversionError(
                "That YAML repeats itself through anchors and aliases until it "
                f"would come to more than {formats.MAX_CELLS:,} values, which is "
                "over the limit for one conversion.")
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)


def _read_yaml(in_path):
    try:
        import yaml
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("YAML isn't supported on this host.") from exc
    try:
        obj = yaml.safe_load(_read_text(in_path))
    except Exception as exc:
        raise ConversionError(f"That isn't valid YAML: {exc}") from exc
    _refuse_expansion(obj)
    return obj


def _element_to_object(element):
    node = {}
    for name, value in element.attrib.items():
        node[f"@{name}"] = value
    for child in element:
        value = _element_to_object(child)
        if child.tag in node:
            if not isinstance(node[child.tag], list):
                node[child.tag] = [node[child.tag]]
            node[child.tag].append(value)
        else:
            node[child.tag] = value
    text = (element.text or "").strip()
    if text:
        if node:
            node["#text"] = text
        else:
            return text
    return node or None


def _read_xml(in_path):
    try:
        from defusedxml.ElementTree import parse
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("XML isn't supported on this host.") from exc
    try:
        root = parse(str(in_path)).getroot()
    except Exception as exc:
        raise ConversionError(f"That isn't XML this can read: {exc}") from exc
    try:
        return {root.tag: _element_to_object(root)}
    except RecursionError as exc:
        raise ConversionError("That XML is nested too deeply to convert.") from exc


def _load(in_path, src_ext: str):
    if src_ext in _DELIMITERS:
        return None, [("", _read_delimited(in_path, src_ext))]
    if src_ext == "xlsx":
        return None, _read_xlsx(in_path)
    if src_ext == "json":
        return _read_json(in_path), None
    if src_ext == "yaml":
        return _read_yaml(in_path), None
    if src_ext == "xml":
        return _read_xml(in_path), None
    raise ConversionError(f"Nothing here reads a .{src_ext} file.")


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def as_table(obj) -> Table:
    if isinstance(obj, list) and obj and all(isinstance(item, dict) for item in obj):
        headers: list[str] = []
        for item in obj:
            for key in item:
                if key not in headers:
                    headers.append(key)
        _check_size(len(obj), len(headers))
        return Table(headers, [[_cell(item.get(key)) for key in headers] for item in obj])

    if isinstance(obj, list) and obj and all(isinstance(item, (list, tuple)) for item in obj):
        width = max(len(row) for row in obj)
        _check_size(len(obj), width)
        headers = [f"column {index + 1}" for index in range(width)]
        return Table(headers, [[_cell(cell) for cell in row] + [""] * (width - len(row))
                               for row in obj])

    if isinstance(obj, dict):
        lists = [value for value in obj.values()
                 if isinstance(value, list) and value and all(isinstance(item, dict) for item in value)]
        if len(lists) == 1:
            return as_table(lists[0])
        _check_size(len(obj), 2)
        return Table(["key", "value"], [[key, _cell(value)] for key, value in obj.items()])

    raise ConversionError(
        "That file isn't shaped like a table -- it has no list of records in "
        "it to make rows out of. JSON, YAML and XML will still convert to "
        "each other, or to PDF."
    )


def _table_to_object(table: Table) -> list[dict]:
    return [dict(zip(table.headers, row)) for row in table.rows]


def _object_of(obj, sheets) -> object:
    if obj is not None:
        return obj
    if len(sheets) == 1:
        return _table_to_object(sheets[0][1])
    return {name or f"sheet {index + 1}": _table_to_object(table)
            for index, (name, table) in enumerate(sheets)}


def _tables_of(obj, sheets) -> list[tuple[str, Table]]:
    if sheets is not None:
        return sheets
    return [("", as_table(obj))]


def _write_delimited(table: Table, out_path, target_ext: str) -> None:
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=_DELIMITERS[target_ext])
        writer.writerow(table.headers)
        writer.writerows(table.rows)


def _write_xlsx(sheets: list[tuple[str, Table]], out_path) -> None:
    try:
        import openpyxl
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("Spreadsheets aren't supported on this host.") from exc
    book = openpyxl.Workbook()
    book.remove(book.active)
    for index, (name, table) in enumerate(sheets):
        title = re.sub(r"[:\\/?*\[\]]", "-", name or f"Sheet{index + 1}")[:31]
        sheet = book.create_sheet(title=title)
        sheet.append(table.headers)
        for row in table.rows:
            sheet.append([_cell(value) for value in row])
    book.save(out_path)


def _markdown_table(table: Table) -> str:
    def escape(value):
        return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(escape(head) for head in table.headers) + " |",
             "| " + " | ".join("---" for _ in table.headers) + " |"]
    for row in table.rows:
        cells = [escape(value) for value in row]
        cells += [""] * (len(table.headers) - len(cells))
        lines.append("| " + " | ".join(cells[:len(table.headers)]) + " |")
    return "\n".join(lines)


def _html_table(table: Table) -> str:
    def cell(value, tag):
        return f"<{tag}>{html_module.escape(str('' if value is None else value))}</{tag}>"

    head = "<tr>" + "".join(cell(head, "th") for head in table.headers) + "</tr>"
    body = "".join(
        "<tr>" + "".join(cell(value, "td") for value in row) + "</tr>" for row in table.rows
    )
    return f"<table border=\"1\" cellspacing=\"0\" cellpadding=\"4\">\n{head}\n{body}\n</table>"


def _write_yaml(obj, out_path) -> None:
    try:
        import yaml
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("YAML isn't supported on this host.") from exc
    with open(out_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(obj, handle, allow_unicode=True, sort_keys=False, default_flow_style=False)


def convert(in_path, src_ext: str, target_ext: str, work_dir, stem: str = "converted") -> Result:
    try:
        return _convert(in_path, src_ext, target_ext, work_dir, stem)
    except RecursionError as exc:
        raise ConversionError("That file is nested too deeply to convert.") from exc


def _convert(in_path, src_ext: str, target_ext: str, work_dir, stem: str) -> Result:
    src_ext = formats.normalise(src_ext)
    target_ext = formats.normalise(target_ext)
    work_dir = Path(work_dir)
    out_path = work_dir / f"{stem}.{target_ext}"
    obj, sheets = _load(in_path, src_ext)

    if target_ext == "json":
        out_path.write_text(
            json.dumps(_object_of(obj, sheets), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return Result(out_path)

    if target_ext == "yaml":
        _write_yaml(json.loads(json.dumps(_object_of(obj, sheets), default=str)), out_path)
        return Result(out_path)

    if target_ext == "xlsx":
        _write_xlsx(_tables_of(obj, sheets), out_path)
        return Result(out_path)

    if target_ext in _DELIMITERS:
        tables = _tables_of(obj, sheets)
        if len(tables) == 1:
            _write_delimited(tables[0][1], out_path, target_ext)
            return Result(out_path)
        produced = []
        for index, (name, table) in enumerate(tables, start=1):
            safe = re.sub(r"[^\w.-]", "_", name) or f"sheet{index}"
            path = work_dir / f"{safe}.{target_ext}"
            _write_delimited(table, path, target_ext)
            produced.append(path)
        archive = work_dir / f"{stem}.zip"
        zip_files(produced, archive)
        for path in produced:
            path.unlink(missing_ok=True)
        return Result(archive, items=len(produced), zipped=True)

    if target_ext in ("md", "html", "pdf"):
        tables = _tables_of(obj, sheets)
        if target_ext == "md":
            parts = []
            for name, table in tables:
                parts.append(f"## {name}\n" if name and len(tables) > 1 else "")
                parts.append(_markdown_table(table))
            out_path.write_text("\n".join(part for part in parts if part), encoding="utf-8")
            return Result(out_path)
        body = "\n".join(
            (f"<h2>{html_module.escape(name)}</h2>" if name and len(tables) > 1 else "")
            + _html_table(table)
            for name, table in tables
        )
        if target_ext == "html":
            out_path.write_text("<!doctype html>\n<meta charset=\"utf-8\">\n" + body, encoding="utf-8")
            return Result(out_path)
        import doc_convert
        doc_convert.html_to_pdf(body, out_path)
        return Result(out_path)

    if target_ext == "txt":
        out_path.write_text(
            json.dumps(_object_of(obj, sheets), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return Result(out_path)

    raise ConversionError(f"Nothing here converts a .{src_ext} to .{target_ext}.")


_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")
_TIMING_RE = re.compile(r"^\s*[\d:.,]+\s*-->\s*[\d:.,]+")
_INDEX_RE = re.compile(r"^\s*\d+\s*$")
_ASS_EVENT_RE = re.compile(r"^Dialogue:\s*(?:[^,]*,){9}(.*)$")


def subtitle_to_text(in_path, src_ext: str, out_path) -> None:
    source = _read_text(in_path)
    lines: list[str] = []
    if formats.normalise(src_ext) == "ass":
        for line in source.splitlines():
            match = _ASS_EVENT_RE.match(line.strip())
            if match:
                lines.append(_TAG_RE.sub("", match.group(1)).replace("\\N", " ").strip())
    else:
        for line in source.splitlines():
            stripped = line.strip()
            if (not stripped or _INDEX_RE.match(stripped) or _TIMING_RE.match(stripped)
                    or stripped.upper().startswith(("WEBVTT", "NOTE ", "STYLE"))):
                continue
            lines.append(_TAG_RE.sub("", stripped).strip())
    text = "\n".join(line for line in lines if line)
    if not text:
        raise ConversionError("There were no subtitle lines in that file.")
    Path(out_path).write_text(text, encoding="utf-8")
