# Licensing — decided in v1.7.0

Internal until 2.0.0, when `publish.ps1` stopped carrying only the bot
folders to GitHub and this whole repository became MumuBots.

> **Settled.** Asked three times across v1.5.0 and v1.6.0 and never answered,
> the owner's answer in 1.7.0 was *"honestly, i dont understand this stuff.
> Just do what you think is the best."* So the recommendation below was taken:
> **AGPL-3.0-or-later on all five bots.** A `LICENSE` file holding the
> canonical text from `gnu.org/licenses/agpl-3.0.txt` is in each bot folder,
> with a copyright line above it and a `## Licence` section in each README.
> `publish.ps1` publishes whatever `git ls-files` tracks, so they reach the
> five public repositories on the next release with nothing else to do.
>
> **2.0.0:** the bots now run modules that live outside every bot folder, in
> `shared/`, and the repository they are published in is this one, whole. So
> the same file is at the repository's root as well, where it covers
> `shared/` and where GitHub looks for it; the copies in the bot folders
> stay, so a folder taken on its own still carries its licence.
>
> The rest of this file is the reasoning, kept because a licence grant is
> irrevocable for the version it ships with and the record of why should
> outlive the decision.

## What this actually commits to

In one sentence: **anybody may run, modify and redistribute these bots, and
anybody who offers a modified version as a network service has to publish
their changes.**

What it does *not* do, since this is the part worth being clear about:

- It does not give anybody a claim on the accounts, the tokens, the database,
  the `@mumu_*` usernames or the Stars. Those are not copyrightable work and
  are not in the repositories.
- It does not stop the owner relicensing later. Copyright stays with the
  author; a future version can ship under different terms. What cannot be
  taken back is the grant on the versions already published.
- It does not oblige anybody to publish anything unless they *distribute* a
  modified version or run one as a service. Reading the code costs nothing.
- It does not conflict with charging money. AGPL constrains the source, not
  the price.

GitHub's community checklist lists "License" next to "Code of conduct" as if
they were the same kind of box to tick. For these repositories they are not.
**v1.5.0 adds a dependency whose licence has terms, and the five public repos
currently have no licence at all.**

This is the one item on that checklist that should be settled before
`publish.ps1` runs again.

---

## What changed

ConvertBot v1.5.0 depends on **PyMuPDF**, and PyMuPDF is:

> Dual Licensed — GNU AFFERO GPL 3.0 or Artifex commercial licence

AGPL is the copyleft licence with the network clause. §13 says that if users
interact with the software **over a network**, they must be offered its
corresponding source. A Telegram bot is exactly that case: the user never has
a copy, they talk to a running instance.

The other new dependencies are permissive and impose nothing:

| dependency | licence |
|---|---|
| PyMuPDF | **AGPL-3.0** or commercial |
| pillow-heif | BSD-3-Clause |
| mammoth | BSD-2-Clause |
| openpyxl, PyYAML | MIT |
| defusedxml | PSF |
| Markdown | BSD |
| Pillow | MIT-CMU |
| python-telegram-bot | LGPL-3.0 — fine as an unmodified pip dependency |
| httpx, psycopg | BSD / LGPL |
| Telethon (ConvertBot, since 1.7.1) | MIT — and its own pyaes (MIT) and rsa (Apache-2.0) |
| cryptg (ConvertBot, since 1.7.1) | CC0 — public domain |
| yt-dlp | Unlicense |
| yt-dlp's `[default]` extras (since 2.1.0) | yt-dlp-ejs (Unlicense and permissive parts), curl-cffi MIT, pycryptodomex BSD-2, brotli MIT, websockets BSD-3, requests Apache-2.0, urllib3 MIT, certifi MPL-2.0 (unmodified), mutagen GPL-2.0-or-later (compatible with AGPL-3.0 through its "or later") — all pip dependencies, none modified |
| rawpy (ConvertBot, since 2.1.0) | MIT — it carries LibRaw (LGPL-2.1 or CDDL-1.0, unmodified, as a wheel) and needs numpy (BSD-3-Clause) |
| libarchive's bsdtar (ConvertBot, since 2.1.0) | BSD-2-Clause — shelled out as a separate process, not linked |
| Tesseract language data (ConvertBot, since 2.1.0) | Apache-2.0 — the Debian packages tesseract-ocr-eng, -rus, -uzb and -uzb-cyrl (4.1.0), data files only, read from disk; the Tesseract engine is MuPDF's built-in one (also Apache-2.0), which is part of PyMuPDF and covered by its row above, and there is no `tesseract` program in the image |
| ffmpeg | shelled out as a separate process, not linked |

## Where that leaves the repositories

ConvertBot's own public repository (retired in 2.0.0, when the bots moved to
MumuBots) has **no LICENSE file**. With no licence, the default is "all rights reserved" — nobody may
copy, modify or redistribute it. That is a coherent position for a private
project, and it is the position all five repos are in today.

It stops being coherent the moment the published source combines with AGPL
code, which is what v1.5.0 does. The two ways out are both simple; the
choice is the owner's.

## The options

**1. AGPL-3.0 on ConvertBot (recommended).** It is what the dependency asks
for, the source is already public so §13 is already satisfied in practice,
and it costs nothing. What it gives up: anyone may run, modify and
redistribute ConvertBot, and anyone offering a modified version as a service
must publish their changes. For a bot nobody is going to fork commercially,
that is close to no cost at all.

The other four bots do not import PyMuPDF and are free to carry any licence —
but four repos under four licences is a thing to explain forever, so **the
same licence on all five** is the tidier answer unless there is a reason not
to.

**2. Drop PyMuPDF.** It is doing PDF, EPUB, MOBI, FB2, CBZ, XPS and SVG
reading, page rendering, text extraction and HTML-to-PDF layout. `pypdf`
(BSD) does none of the rendering; `pdf2image` needs Poppler, which is GPL
anyway. There is no permissive replacement that covers the same ground, so
this means giving up most of §1 of `CHANGES_v1.5.0.md`.

**3. An Artifex commercial licence.** Costs money, keeps the code
proprietary. Only worth it if the bots are ever meant to be closed and sold.

## Recommendation — taken in v1.7.0

**AGPL-3.0 across all five repos**, added before the next `publish.ps1`.
Concretely: one `LICENSE` file in each bot folder, and a line in each README.
It is one commit, it makes the current situation correct rather than
ambiguous, and it forecloses nothing the project is actually likely to do.

**This was not decided by default.** A licence grant is irrevocable for the
version it ships with, so it sat here unwritten across two releases rather
than being taken as read. It was written only once the owner said to choose,
and option 1 was chosen for the reasons above: it is what the dependency asks
for, the source is already public so §13 is already satisfied in practice, and
it forecloses nothing this project is actually likely to do.

---

## The rest of GitHub's checklist

For a set of repositories that are **published mirrors of a private
monorepo** — `publish.ps1` overwrites each one wholesale on every release;
since 2.0.0, one repository, MumuBots, that is the monorepo itself — the
remaining items are not all worth the same:

| item | verdict |
|---|---|
| **License** | **Done in v1.7.0** — AGPL-3.0-or-later, all five. |
| **Security policy** | **Added.** Somebody who finds a way to make a bot leak another person's file needs a way to say so that is not a public issue. Cheap, and the one item here with a real failure mode behind it. |
| **Contributing** | **Added**, but inverted: it exists to say the repo is a mirror, that a pull request against it cannot be merged, and where to raise things instead. Without it, the honest outcome is somebody spending an evening on a PR that gets closed. |
| **Code of conduct** | **Skipped.** It governs interaction between contributors, and there are none. Adding one to a mirror is filling in a box rather than solving anything, and an unenforced code of conduct is worse than none. Worth revisiting the day somebody else commits. |
| **Issue templates** | **Skipped for now.** One bug-report template would be mildly useful; the issue volume is zero, so it is form-filling ahead of a need. |
| **Pull request template** | **Skipped.** A template on a repository that cannot accept pull requests is a contradiction; `CONTRIBUTING.md` says so in a sentence instead. |

The checklist is written for a project seeking contributors. Four of these
six are answers to questions nobody has asked yet, and the two that are not —
a licence and a way to report a vulnerability privately — are the two that
have a real cost attached to skipping them.
