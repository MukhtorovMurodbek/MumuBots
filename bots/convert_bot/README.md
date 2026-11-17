# ConvertBot

[↑ bots](../README.md)

Converts files between formats, and reads the text in pictures and scanned PDFs, on its own machine. Settings prefix `CBOT_`, database schema `convert_bot`.

| File | What it is |
|---|---|
| [bot.py](bot.py) · [notes](bot.md) | the handlers and `main()`: the format menu, prices, payments, `/formats` |
| [db.py](db.py) · [notes](db.md) | ConvertBot's tables: settings, the queue's in-flight jobs, reminders |
| [i18n.py](i18n.py) · [notes](i18n.md) | every text ConvertBot shows, in English, Uzbek and Russian |
| [formats.py](formats.py) · [notes](formats.md) | what can be converted to what, and what this host can actually do |
| [jobs.py](jobs.py) · [notes](jobs.md) | the conversion queue, and what happens to the ⚡ credit |
| [convert_runner.py](convert_runner.py) · [notes](convert_runner.md) | the Telegram side of one conversion job |
| [convert_worker.py](convert_worker.py) · [notes](convert_worker.md) | one conversion, in a process of its own, walled in |
| [convert_utils.py](convert_utils.py) · [notes](convert_utils.md) | the image, audio and video conversions, and the price table |
| [doc_convert.py](doc_convert.py) · [notes](doc_convert.md) | documents: PDF, Word, EPUB, Markdown, HTML |
| [office_convert.py](office_convert.py) · [notes](office_convert.md) | Office files through LibreOffice, one fresh process and profile per job |
| [ocr_convert.py](ocr_convert.py) · [notes](ocr_convert.md) | text from pictures and scanned PDFs, recognised on this machine |
| [data_convert.py](data_convert.py) · [notes](data_convert.md) | structured data: CSV, JSON, XML, YAML, spreadsheets |
| [archive_convert.py](archive_convert.py) · [notes](archive_convert.md) | archives: opened, checked and packed again |
| [raw_convert.py](raw_convert.py) · [notes](raw_convert.md) | camera RAW photos |
| [db_merge.py](db_merge.py) · [notes](db_merge.md) | one-off: merging two ConvertBot databases |

Code every bot uses is in [shared/](../../shared/README.md).
