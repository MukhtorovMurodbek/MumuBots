# `data_convert.py`

[↑ convert_bot](README.md) · [code](data_convert.py)

Structured data: CSV, TSV, JSON, YAML, XLSX and XML -- and the plain-text
form of a subtitle file, which has nowhere better to live.

This is the corner of the bot that no general-purpose converter bothers with,
and it is cheap: everything here is the standard library plus `openpyxl`,
`PyYAML` and `defusedxml`, all pure wheels. Somebody who has a CSV and needs
a spreadsheet, or a JSON export and needs a table they can read, currently
opens a website and pastes their data into it. They should not have to.

\## One intermediate, two shapes

Every format here is read into an ordinary Python object -- lists, dicts,
strings, numbers -- and written out of one. That is enough for the pairs
where both sides are nested (JSON, YAML, XML).

The other five formats are \*tables\* and cannot hold a nested object, so
`as_table()` is the narrow place where a nested thing either flattens or is
refused with a sentence saying why. Silently writing `[object Object]` into a
cell, which is what most converters do, is worse than not converting.

\## XML is parsed with defusedxml and nothing else

The standard library's XML parsers will expand entities, which makes a
fourteen-line file into gigabytes of memory ("billion laughs") and can be
pointed at a local file or a URL ("XXE"). Neither is theoretical and both
arrive as an ordinary-looking upload from a stranger. `defusedxml` refuses
all of it, and `formats.py` does not offer XML at all when it is missing.

_At `WRITES = frozenset({"json", "yaml", "xlsx", "csv", "tsv", "md", "html", "pdf", "txt"})`:_

What convert() below can write -- see doc_convert.WRITES.

_At `class _Budget:`:_

---------- reading ----------

### `_Budget`

What has been read so far, checked as it is read.

The limits in \_check_size are about what a table may be, and they used to
be asked after every row was already in memory: a file of a hundred million
blank lines was read whole -- gigabytes of empty lists -- before it was
told it was over 100,000 rows. Asked row by row, a file over the limit is
refused at the limit.

#### `_Budget.add()`

- `if self.rows > formats.MAX_ROWS + 1:` — + the header row

### `_read_delimited()`

- At `raise ConversionError(f"That isn't a CSV this can read: {exc}.") from exc`:
  A cell past the csv module's 128 KB field limit is the usual one. It
  is the file, not the bot: a refusal, not a fault to page the operator about.

### `_read_xlsx()`

Every sheet in the workbook, named.

All of them rather than the first: a workbook with three sheets converted
to "a CSV" would silently be a third of the answer, and which third would
depend on which tab happened to be open when it was saved.

- At `book = openpyxl.load_workbook(in_path, read_only=True, data_only=True)`:
  read_only streams the sheet rather than building the whole object
  model; data_only hands back what a formula evaluated to last time it
  was calculated, which is the number the user can see in the cell.
- At `rows = []`:
  Blank rows dropped as they stream past, and the limits asked as
  they go: Excel reports a sheet with a formatted column as a
  million rows, and reading them all in first was the biggest
  thing this function held.

### `_refuse_expansion()`

Refuse a YAML document that is small on disk and enormous once read.

YAML lets a value be named once and used many times -- an anchor and its
aliases -- and a loader keeps one object however often it is used. That is
cheap to read and ruinous to write: every alias is written out in full, so
a nine-line file of nested lists that each name the one before nine times
is nine to the ninth power values once it is JSON. A file under 500 bytes
that way took 573 MB and five seconds to write at seven levels, and each
level more is nine times that. A loop -- an anchor that contains its own
alias -- is the same thing without end.

So the values are counted as they would be written, by walking the object
and not remembering what has been seen, and a document with more than the
limit a table has (MAX_CELLS) is refused. Anchors are still fine: the
ordinary use of them, a shared block in a config file, expands to a few
times the size of the file.

### `_read_yaml()`

- At `obj = yaml.safe_load(_read_text(in_path))`:
  safe_load and never load(): the full loader constructs arbitrary
  Python objects named in the document, which from a stranger's file
  is remote code execution.

### `_element_to_object()`

An XML element as dicts and lists, attributes prefixed with `@`.

The same shape `xmltodict` produces, which is the one most people expect
when they ask for XML as JSON: repeated children become a list, an
element with only text becomes that text.

_At `def _load(in_path, src_ext: str):`:_

---------- the two shapes ----------

### `_load()`

(object, sheets) -- one of the two is always None.

A spreadsheet is the only source that can be several tables at once, and
keeping that in the return type is what stops the rest of this file from
having to know about worksheets.

### `_cell()`

What a nested value looks like in a cell that can only hold a string.

### `as_table()`

A nested object as one table, or a refusal that says why not.

Three shapes flatten honestly and the rest do not:

```text
a list of objects   the ordinary shape of an export -- one row each,
                    columns being the union of the keys, in the order
                    they were first seen.
a list of lists     already a table; the columns get numbers.
an object           two columns, key and value.
```

Anything else -- a bare number, a list of lists of objects -- has no
table in it, and saying so is more useful than inventing one.

- At `lists = [value for value in obj.values()`:
  A single object with one list of objects inside it is what almost
  every API export looks like: {"items": [...]}. Use the list.

### `_object_of()`

Whatever was loaded, as a nested object.

### `_tables_of()`

Whatever was loaded, as one or more tables.

_At `def _write_delimited(table: Table, out_path, target_ext: str) -> None:`:_

---------- writing ----------

### `_write_xlsx()`

- At `title = re.sub(r"[:\\/?*\[\]]", "-", name or f"Sheet{index + 1}")[:31]`:
  Excel's own rules, and it rejects the file rather than the sheet
  name: 31 characters, and none of : \ / ? \* [ ].

_At `def convert(in_path, src_ext: str, target_ext: str, work_dir, stem: str = "converted") - …`:_

---------- the dispatcher ----------

### `convert()`

- At `raise ConversionError("That file is nested too deeply to convert.") from exc`:
  Reading a deeply nested file is guarded where it is read; this is
  writing one out, which walks the same depth again. Either way it is
  the file's shape, not a fault in the bot.

### `_convert()`

- At `produced = []`:
  A workbook is several tables and a CSV is one. Every sheet, named,
  in a zip -- rather than picking one and not saying which.

_At `_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")`:_

---------- subtitles, as plain text ----------
ffmpeg converts between the subtitle formats themselves; this is the one
subtitle target it has no encoder for, because "the words with none of the
timings" is not a subtitle format. It is, however, the thing people most
often want out of a subtitle file: a transcript.

- `_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")` — &lt;i>, {\an8}

### `subtitle_to_text()`

- At `lines.append(_TAG_RE.sub("", match.group(1)).replace("\\N", " ").strip())`:
  ASS puts hard line breaks in the text itself.
