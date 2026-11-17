# `problems.py`

[↑ shared](README.md) · [code](problems.py)

Every problem a person can run into in the family's bots, by code.

This was asked for in so many words: "Give every possible exception that
the user might trigger a code or a unique identifier", a "report the issue"
button beside it, and "a list or a dictionary that can decode this error
message with as much accuracy as possible." This file is that dictionary.

A CODE names a kind of problem and never changes once shipped, so a report
sent months later still decodes. An INCIDENT names one occurrence: it is made
when the message goes out, carried by the report button, and written to the
log beside what happened, so a report can be matched to its log line.

A code is shown at the end of a message as "🆔 CODE". i18n.t() appends that
line for every key in KEYS; a message assembled from several strings gets it
at its end, where it is assembled (ConvertBot's job endings, StickerBot's
sticker errors and imports, DownloaderBot's allowance message).
shared_features logs every message that ends in one, with its time and incident;
the ones that are not SIMPLE say so, and get a details button when WANTS_DETAILS
asks for one or the same chat keeps meeting them (shared_features.offers_details,
since 2.1.6). Every code also
has a LEVEL -- see the block above `reportable()` -- which decides whether the
operator hears about it now or in the nightly report.

Copied, not imported, into all five bots -- ManagerBot's /decode reads it.
Pure: no Telegram, no database, nothing that can fail at import.

_At `_AT_END = re.compile(r"\n\n?🆔 ([A-Z]{2}(?:-[A-Z0-9]+)+)(?:</[a-z]+>)*\s*$")`:_

At the very end of a message, on a line of its own (after a blank line, or
straight under the short "report saved" note) -- possibly inside closing
HTML tags, for the policy messages that are sent as HTML.

_At `"FM-CRASH": Problem(`:_

---- every public bot ----------------------------------------------------

_At `"CV-NO-EXTENSION": Problem(`:_

---- ConvertBot --------------------------------------------------------------

_At `"DL-QUOTA-HOUR": Problem(`:_

---- DownloaderBot ------------------------------------------------------------

_At `"ST-WHOMADE-UNKNOWN": Problem(`:_

---- StickerBot ----------------------------------------------------------------

_At `"AN-LINK-INVALID": Problem(`:_

---- AnonBot ---------------------------------------------------------------------

_At `KEYS = {`:_

i18n key -> code. "shared" applies to every public bot unless the bot's own
table overrides the key.

_At `"reaction_not_passed": "AN-REACTION-NOT-PASSED",`:_

Two notes, one code: what was not passed on is the same fact.

_At `JOB_ENDINGS = {`:_

Codes a message is given where it is assembled, not by i18n.t().

_At `ALERT_KEYS = {`:_

Keys shown in a pop-up rather than a message: they carry the code, cannot
carry a button, and must stay within Telegram's 200 characters.

_At `BRIEF = {`:_

One line per code for the operator's reports, in plain words: what the person ran into. PROBLEMS'
`meaning` is the longer account, for /explain.

_At `RENAMED = {`:_

Codes renamed in 2.2.0, when the texts stopped saying "owner" and "yours": a sticker pack has a
creator, an AnonBot inbox has a holder, and the button for somebody else's pack or conversation is
named after what it points at. The old codes stay in rows written before the rename (the problem log,
reports, events) and in messages already sent, so every lookup here goes through current() first,
and an old code is still explained, briefed and ranked like its new one.

### `current()`

The code under its present name: an old name from RENAMED becomes the new one, anything else comes
back unchanged.

### `lookup()`

The Problem for a code under either name, or None. ManagerBot's lists, the 22:00 report and the
operator's alerts read titles through it rather than from PROBLEMS directly, so a row written before
the rename does not show up as an unknown code.

_At `SIMPLE = frozenset({`:_

Problems that get no Report button. About the one under the unknown-command answer: not every message needs a report button, and that one is simple. These are the refusals that explain themselves and that the
person can act on -- their own typo, a limit they have reached, a button that
has expired or is somebody else's, the other side's choice -- where nothing
on the bot's side is wrong. They keep their code, and every one is still
logged with its time and incident. Everything else is a fault, and its
message says an anonymous report was saved: a crash, a failed download,
conversion or send, Telegram refusing something the bot prepared, or the
bot's own configuration. Which of those also get the details button is
WANTS_DETAILS below (and shared_features.offers_details). A new code is one or the other on
purpose; tests/test_problems.py lists both halves so neither grows by default.

## How loudly a problem should be answered

Asked for in 1.7.0: priority levels for errors. The highest level goes to the operator
immediately -- the most important of those being a bot that crashes entirely -- and the
other errors are visible in the report command.

Three levels, and the line between them is \*whose fault it is and how much
of the bot it takes down\*:

```text
1 URGENT   The bot is broken, or something that had to happen did not:
           a crash, a disk with no room left, a message that was accepted
           and then never delivered, money taken with nothing to show for
           it. One of these is worth a message at three in the morning
           because waiting until midnight makes it worse.
```

```text
2 FAULT    One person asked for something and the bot could not do it,
           through no fault of theirs -- a download that failed, a
           conversion that timed out, Telegram refusing a sticker. Worth
           knowing about, not worth waking up for. Into the daily report.
```

```text
3 EXPLAINED  The bot did exactly the right thing and told the person why
           it could not do what they asked: a file over the limit, a
           message that was not a reply, an expired button, an unknown
           command. Not a fault at all, and the reason SIMPLE exists --
           these are the problems whose own message explains them. In the
           daily report by KIND (below), never one incident at a time.
           (The constant is still called REFUSED; the report stopped
           saying "refused" in 2.1.0 -- see KINDS for why.)
```

A level-2 code can still reach the operator immediately without being promoted:
see BURST_ESCALATION in shared_features. Fifty of the same failure in an
hour is not fifty people having bad luck, it is a route that has gone down,
and that is a level-1 fact assembled out of level-2 parts.

_At `"FM-CRASH", "CV-CRASH",`:_

The bot itself fell over.

_At `"FM-INVOICE",`:_

Money moved, or should have and did not.

_At `"FM-ERASE",`:_

A legal obligation the bot failed to honour.

_At `"CV-STORAGE-FULL", "DL-NO-SPACE",`:_

No room left on the machine -- everybody's next request fails too.

_At `"CV-RESTARTED",`:_

Work accepted and then lost to a restart.

_At `"AN-DELIVERY", "AN-REPLY-FAILED",`:_

AnonBot took a message and did not deliver it. The whole bot is a
promise to carry one message to one person, so this is the only
failure it has that is not recoverable by trying again -- the sender
believes it arrived.

### `level()`

URGENT, FAULT or REFUSED for a code. An unknown code is a FAULT: it
came from somewhere, and guessing that it is harmless is the wrong way to
be wrong.

## What kind of "the bot was right" it was

Level 3 used to reach the operator as one line: "Refused — 2", "people told no
by a bot that was working correctly". On 2026-10-03, about a night whose only two
entries were AnonBot messages sent without a swipe-reply, "Refused" was found
misleading, and the wording was to be fixed for that and every similar case. Nobody
had been refused anything -- they had used the bot the wrong way and been shown the
right one. Level 3 is five different things, and each is now named for what it
is, with every code listed under it by its title:

```text
GUIDED      they sent something the bot cannot take -- no swipe-reply, an
            unknown command, a typo -- and were told what to do instead
LIMIT       a size, a quota, a pace, a format or a setting, as designed
GONE        an old button or link, a record that has gone, or a button
            that was somebody else's
OTHER_SIDE  the other person's choice: blocked, paused, never started
PAUSED      they came while the bots were paused for an update
```

Every SIMPLE code is in exactly one; tests/test_reporting.py holds that.

_At `KIND_ORDER = (GUIDED, LIMIT, GONE, OTHER_SIDE, PAUSED)`:_

The order the nightly report shows them in -- the ones that say something
about the bots themselves first: people getting it wrong may mean a sentence
needs rewriting, a limit may need raising.

_At `KIND_LABELS = {`:_

icon, heading, and the line saying what it means.

### `kind()`

Which KIND a level-3 code is, or None for anything that is not level 3.
A level-3 code in no kind would be a gap in the table above; it is read as
GUIDED rather than dropped, and the test is what finds it.

### `kind_label()`

"💡 shown how to use it" -- what /reports and /report say for a level-3
code, in place of the level's bare name.

### `urgent()`

Whether this one is worth interrupting the operator for.

### `reportable()`

Whether a message ending in this code is a fault: it says an anonymous report was saved, and may offer the details button (wants_details, or shared_features' repeat count).

_At `WANTS_DETAILS = frozenset({`:_

A person's details (id, @username, language, chat type) are asked for only where knowing who it
was changes what the operator can do: a crash to follow up, an erase or a piece of feedback that did
not happen and is theirs, a reply that went to the wrong conversation. Anything else -- a timeout,
a download or upload that failed, Telegram refusing -- is fixed from the anonymous record; there
the button is offered only when it keeps happening to the same person
(shared_features.DETAILS_AFTER).

### `code_for()`

The code a message key carries in this bot, or None.

### `find_code()`

The code a message ends with, if it is a known one.

### `strip_code()`

The message without its code line.

### `decode()`

Plain-text explanation of a code, for the operator.
