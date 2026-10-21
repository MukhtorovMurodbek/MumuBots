# Privacy — DownloaderBot

DownloaderBot takes a link to a public post on Instagram, TikTok, Pinterest,
Reddit or X and sends back the media behind it.

This describes what the bot holds about the people who use it, who else sees
it, how long it stays, and how to have it erased. The short version is
available inside the bot as `/privacy`, in English, Uzbek and Russian.

**Operator contact:** mukhtorovmurodbek@gmail.com

_Last reviewed: 3 October 2026._

---

## What is held

- **A Telegram user id.** A number, held from the first message, because it is
  the only thing a bot can address a person by.
- **A chosen language, and the caption, quality and large-file settings** that
  go with it.
- **The last download, for five minutes.** What the bot has just sent you stays on
  its server for five minutes, so that the button under it, or the same link sent
  again, is answered without fetching the post a second time. Each of those starts
  the five minutes again, but it never lasts past 12 hours after the download. It
  is deleted sooner when you send a different link, when the bot restarts, when the
  server is short of space, or when you use `/deletemydata`. Only you can get it back, and a download over 100 MB is never
  kept.
- **References to files the bot has already sent** (Telegram's file IDs), filed
  under a scrambled name of the post and not under who asked for it. They let the
  bot send the same post again without downloading it. Before re-using one, the
  bot checks at least every 12 hours that the post is still public. References
  nobody uses are deleted after 90 days. A reference is not about anyone: it holds
  no user id, chat, username, link, platform name or caption, and the scrambled
  name cannot be turned back into the link.
- **The links of your last five downloads**, so the buttons under them work, until
  you have not used the bot for 12 hours. Every link is cut down to the address of
  the post before the bot does anything else with it: the share-tracking parts that
  apps add when you copy a link (such as `igsh`, `utm_source` or `si`) are removed,
  and what is held is the plain link.
- **One row per download** — which platform, and when. This is what the daily
  allowance is counted from. The link itself is not part of the row.
- **A timestamp per use** — one row saying this id used the bot at this
  minute, so the operator can tell whether anybody is using it.
- **Button taps, counted.** How many times each button is tapped, per day for the whole bot. Nothing about who
  tapped it.
- **Donations**, if any: the amount, the status, and Telegram's payment id.
- **A ⚡ credit balance, and its ledger**, once you have paid or been given
  credit. It is shared by the family's four bots: the balance, and one row for
  every top-up, bonus, charge, credit returned and correction, with the amount, which
  bot, and why. `/balance` shows it.
- **Bonus credit, and when it expires** — for each payment that earned a
  bonus and each gift: how much, how much of it is left, and the date it runs
  out. Bonus that runs out unused is taken off the balance, and the ledger
  says so.
- **Gifts given** — that a gift, by its name, was given to your id and
  when, so that it is given only once across the family. Kept like the ledger;
  `/deletemydata` does not remove it.
- **Problems, whenever one is shown to anybody** — the error code, an
  incident number, when it happened and the bot's version. This is written
  down automatically, because a fault nobody reports is still a fault, and
  nothing in it is about you: not your ID, not your name, not what you sent.
  The message that carries the code says so, above the button.
- **Your own details, only if you add them** — tapping "Add my details" under
  an error offers to attach four things to that incident: your Telegram user
  ID, your @username if you have one, the language you picked, and whether the
  chat is private or a group. All four are named on screen before anything is
  sent, and nothing is sent unless you tap Send. Not what you wrote and not
  the file you sent, ever.

  Those four are **cleared after 30 days**, automatically, whether or not you
  ask. The record of the error itself stays — it still happened, and a count
  of it is not about anybody — but after a month it no longer says who hit it.
  `/deletemydata` clears them straight away instead of waiting.

  The error records themselves are kept for **180 days** and then deleted.

  One more thing worth saying plainly: the operator is messaged the moment a
  serious error happens — a crash, or a message this bot accepted and failed
  to deliver — so those are seen the same day rather than in a list. That
  message says which error and when. It does not say who, even when somebody
  has attached their details; the operator has to look those up deliberately.
- **Work in progress** — a download waiting on a choice, so it survives a
  restart.
- **Feedback, if you send it** — what you write with `/feedback`, which bot it
  was sent from, the language you use, and when. Not your user id, your name
  or your username. The operator receives it as a Telegram message — straight
  away, or with the nightly summary, as they choose — and the copy in the
  database is **deleted after 30 days**. Because nothing in it says who sent
  it, `/deletemydata` cannot find it to erase it sooner. For the same reason it
  is **one-way**: only the operator reads it, and they cannot reply.

## What is not held

- The media itself, beyond those five minutes. It is fetched, sent, and deleted.
- Who asked for which post, in the references to files already sent. They are
  kept under a scrambled name of the post alone; nothing in them says who the post
  was sent to.
- Anything about the author of the post that was downloaded, beyond what is
  inside the media file they published.

## Who else sees it

- **Telegram.** Every message in either direction passes through Telegram,
  which is a separate company operating under its own terms and privacy
  policy. Nothing reaches this bot that Telegram has not already handled. A
  large file (over 48 MB, if you have switched large files on) is sent over
  MTProto, Telegram's own protocol, instead of the Bot API -- still only
  between Telegram and this bot.
- **The hosting provider.** The bot runs as a container on a commercial host,
  and writes to a managed Postgres database. Both are operated by third
  parties under their own terms; neither is given access for any purpose
  beyond running the bot.

- **The site the link points to.** The bot requests the post from it directly
  first, which means that site sees a request from the bot's server.
- **Fallback download services.** When a site refuses the bot directly, the
  link is handed to a third-party resolver instead. That service receives the
  link and sees the request as coming from this bot's server, not from the
  person who sent it. What those services log is governed by their own
  policies, not by this one.

Nothing is sold, rented, shared for advertising, or used to build a profile of
anybody. There is no analytics service, no advertising identifier and no
tracking of any kind — a Telegram bot has no browser to put one in.

## Why

Every item above exists because the bot cannot do what it is for without it,
or — in the case of the timestamp per use — because the person running it
would otherwise have no way of telling whether it is worth continuing to run.
Nothing is collected speculatively, and nothing is collected to be sold later.

## How long it is kept

| what | how long |
|---|---|
| user id, language, settings | until erased, or indefinitely while the bot is in use |
| the links of your last five downloads | 12 hours after you last used the bot (`USER_DATA_TTL_HOURS`) |
| the last download's files | 5 minutes after you last asked for it, or until you send a different link (`DBOT_CACHE_SECONDS`); never past 12 hours after the download |
| references to files already sent | 90 days after they were last used (`DBOT_FILEID_KEEP_DAYS`); the bot re-checks that the post is still public at least every 12 hours (`DBOT_FILEID_CONFIRM_HOURS`) before re-using one |
| one row per download | 7 days (`DBOT_DOWNLOAD_RETENTION_DAYS`) |
| timestamp per use | about 90 days (`ACTIVITY_RETENTION_DAYS`) |
| button tap counts | 45 days (`FAMILY_USAGE_RETENTION_DAYS`) |
| work in progress | 12 hours (`DEPLOY_STATE_TTL_HOURS`) |
| donation records | kept, for accounts and payment disputes |
| ⚡ credit balance and its ledger | kept, like the donation records |
| gifts given (which gift was given to your id, and when) | kept, like the ledger |
| feedback sent with `/feedback` | 30 days (`FEEDBACK_RETENTION_DAYS`) |

## Erasing it

`/deletemydata`, inside the bot. It asks once, then erases in that moment. No
account, no form, and no waiting period.

Erasing the download rows resets the daily allowance along with them. It also
deletes the last download if it is still being held; nothing older was ever kept
here.

`/deletemydata` has nothing of yours to delete among the references to files
already sent, because they hold nothing about you: no id, no chat, no username,
no link. They go on their own, 90 days after they were last used.

What survives is the payment ledger, without the username on it. A payment
record has to outlive the payer asking to be forgotten: it is what a payment
dispute is settled against, and what the totals are counted from. The username is cleared
because it is the one free-text identifier on the row; the numeric id stays,
because a dispute cannot be settled with nobody.

The ⚡ credit balance and its ledger survive erasing for the same reason, and
hold no username at all. Erasing does not forfeit credit: it is still there if
you come back, and the operator removes it on request.

Blocking the bot in Telegram stops it from sending anything, but erases
nothing — the two are separate actions, and `/deletemydata` is the one that
removes data.

There is no separate export command. Everything held is listed above, and
`/deletemydata` reports how many records it removed.

## Age

Telegram sets the minimum age for holding a Telegram account, and this bot is
available to anyone who has one. It does not ask for a date of birth, does not
hold one, and has no way of telling how old anyone is.

## Changes

Material changes are announced in the bot before they take effect. The date at
the top of this file is when it was last reviewed.

## Contact

Questions, complaints and data requests go to the operator named at the top of
this file.
