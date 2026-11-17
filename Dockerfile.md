# `Dockerfile`

[↑ MumuBots](README.md) · [code](Dockerfile)

The production image. Railway builds it from this file (`railway.json`); the build runs `main.py --check`, so an image that cannot load every bot is never built.

_At `RUN apt-get update \`:_

LibreOffice, in its -nogui build (no X11 or GTK), does ConvertBot's PowerPoint, Word and
Excel files: PPT/PPTX/ODP, DOC/ODT/RTF, XLS/ODS, and a PDF opened as slides (the PDF
import is in libreoffice-core-nogui). The fonts are what decks and documents are laid out
in: Carlito and Caladea measure the same as Calibri and Cambria, Liberation as Arial,
Times New Roman and Courier New, so the text breaks where it did in Office; DejaVu covers
Cyrillic and Noto CJK Korean, Chinese and Japanese, which would otherwise come out as boxes.

_At `RUN python -m compileall -q main.py bots shared \`:_

The code stays root's: the bots' user can write only data/, so nothing that takes over a bot
can change the code it runs.

_At `ENTRYPOINT ["/usr/bin/tini", "--", "/app/start.sh"]`:_

tini (process 1) stays root, so its environment -- every variable the host sets, tokens
included -- can't be read by the bots' user. [start.sh](start.sh) hands the volume to that user
and starts the bots as `bots`, with every capability dropped and no way to gain one back.
