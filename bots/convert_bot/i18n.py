"""Translation strings for ConvertBot's end-user-facing text (English,
Uzbek, Russian). Deliberately duplicated per bot -- same "no shared files
between bots" independence as shared_features.py -- but the STRINGS content
here is specific to this bot's own commands and flows.

Admin-only output (/stars, /dbdump, /status) is intentionally NOT
translated -- only the bot owner reads it, same reasoning as
downloader_bot's i18n.py.

The keys below split into two groups:
  - "Shared" keys (donate flow, sibling-bot blurb) exist under the exact
    same names in every bot's i18n.py, since shared_features.py is
    duplicated byte-identical across the family and calls t() with these
    names regardless of which bot it's running in.
  - Bot-specific keys, everything below the shared block, for this bot's
    own bot.py/convert_utils.py strings only.
"""
import asyncio

import db

SUPPORTED_LANGUAGES = ("en", "uz", "ru")
LANGUAGE_LABELS = {"en": "English 🇬🇧", "uz": "O'zbekcha 🇺🇿", "ru": "Русский 🇷🇺"}

# What every /start shows, whether or not the user already has a language.
# Deliberately not part of STRINGS: it is trilingual on purpose, so there
# is no single `lang` to look it up under.
LANGUAGE_PROMPT = (
    "👋 Welcome! / Xush kelibsiz! / Добро пожаловать!\n\n"
    "Please choose your language / Iltimos, tilni tanlang / "
    "Пожалуйста, выберите язык:"
)

STRINGS = {
    "en": {
        "animation_too_long_note": "🎞 {formats} are not offered: this video is longer than {seconds} seconds, more than an animation can be made from in time. Trim it, or pick a video format.",
        "animation_too_long_alert": "This video is longer than {seconds} seconds, too long to become an animation in time. Nothing was charged. Trim it, or pick a video format.",
        "animation_too_many_frames": "That animation has {frames} frames, more than the {max} I can convert. Nothing was charged. Try a shorter one.",
        "transfer_waiting": "⏳ Another big file is being moved right now. Yours starts as soon as it is done — nothing for you to do.",
        "flood_wait": "You're going faster than I can keep up with — give it about {seconds} second(s) and carry on.",
        # ---- shared keys (same name in every bot's i18n.py) ----
        "sibling_blurb": "Also part of this bot family, see below \U0001f447",
        "donation_nudge": '⚡ Converting bigger files often? /recharge tops up your credit ahead of time.',
        "donate_unknown_currency": 'Unknown currency "{currency}" — try xtr or usd.',
        "donate_currency_not_configured": "{currency} payments aren't set up on this bot yet — use Stars instead.",
        "donate_invalid_amount": "That's not a valid amount — try e.g. /recharge 500.",
        "donate_prompt": 'Top up your ⚡ credit for conversions. Choose an amount below, or Custom to enter your own (you can also send /recharge <number> directly).',
        "donate_custom_button": "✏️ Custom {symbol}",
        "donate_too_many_stars": "That's a lot of Stars! Keep it under {max} ⭐ per top-up.",
        "donate_out_of_range": "{currency} donations need to be between {lo} and {hi} {symbol}.",
        "donate_invoice_title": 'Top up ⚡ credit',
        "donate_invoice_description": 'Adds {credited} ⚡ of credit to your balance for conversions.',
        "donate_invoice_label": '⚡ credit top-up',
        "donate_invoice_description_fiat": 'A one-time voluntary donation towards hosting costs. Thank you!',
        "donate_prompt_credit": 'Each ⭐ you pay becomes ⚡ credit for conversions. Your next {left} ⭐ earn {each} ⚡ each ({mult}×): {rate} ⚡ of ordinary credit plus a bonus that expires {days} days after payment. ⚡ can NOT be withdrawn or turned back into Stars.',
        "donate_prompt_credit_base": 'Each ⭐ you pay becomes {rate} ⚡ of credit for conversions. ⚡ can NOT be withdrawn or turned back into Stars.',
        "donate_invoice_error": "⚠️ Telegram wouldn't create that invoice: {error}",
        "stars_unit": "Stars",
        "donate_custom_ask": 'How many {unit} would you like to top up with? Reply with a number.',
        "donate_invalid_amount_retry": "That's not a valid amount — send /recharge to try again.",
        "donate_thanks": "🙏 Thank you for the {amount} ⭐ — genuinely appreciated!",
        "topup_thanks": '⚡ Recharged — {stars} ⭐ received. Thank you!\n\nAdded to your balance: +{credited} ⚡\nYour balance now: {balance} ⚡',
        "topup_thanks_bonus": '🎁 Plus {bonus} ⚡ bonus credit — it expires on {date}.',
        "credit_cannot_be_withdrawn": '⚡ is credit for conversions in ConvertBot, and can NOT be withdrawn or turned back into Stars. A problem with a payment? /paysupport',
        "topup_refunded": "⚠️ Your payment of {stars} ⭐ went through, but a problem on my side kept the ⚡ from reaching your balance, so I've refunded it: the {stars} ⭐ are back in your account. Please try again in a few minutes.",
        "topup_failed": "⚠️ Your payment of {stars} ⭐ went through, but a problem on my side kept the ⚡ from reaching your balance. Nothing is lost: the owner has been told and will add it by hand. To follow up: /paysupport",
        "paysupport_text": '💳 Help with a payment\n\nPayments are FINAL: Stars are NOT refunded, and ⚡ credit can NOT be withdrawn or turned back into Stars. The one exception: a payment the bot could not turn into credit is refunded to you automatically, in full.\n\nIf a payment went wrong — you were charged and no credit arrived, or you were charged twice — write to {contact} with the date, the amount and your Telegram id, {user_id}. It will be checked and put right with credit.\n\n/balance lists every payment and what it added.',
        "report_button": '🐞 Add my details',
        "problem_logged_note": "This is already written down for the bot's owner — the code, the time and the version, and nothing about you.",
        "report_disclaimer": "📨 Add your own details to this problem?\n\nAlready recorded, with you left out of it: the bot's name, the error code {code}, the incident number {incident}, when it happened and the bot's version.\n\nTapping Send adds four things: your Telegram user ID, your @username if you have one, the language you picked, and whether this chat is private or a group. Not what you wrote, and not the file you sent.\n\nIt lets your case be found in the logs, which is usually what makes a rare problem fixable. Entirely your choice.",
        "report_send": '📨 Send my details',
        "report_cancel": '✖️ Cancel',
        "report_sent": '✅ Thank you — your details are on incident {incident} now, which is what makes this one findable.',
        "report_already": 'Your details are already on incident {incident}. Nothing more was sent.',
        "report_cancelled": 'Cancelled — nothing about you was sent. The problem itself stays logged.',
        "report_failed": "⚠️ The report couldn't be sent right now. Please try again later.",
        "report_invalid": 'This button no longer works.',
        "crash_notice": "⚠️ Something went wrong on the bot's side while handling that, so it wasn't done. Please try again in a moment.",
        "sandbox_notice": "🧪 Test mode — no real Stars were charged for this.",
        "balance_header": "⚡ Your balance: {balance}",
        "balance_totals": "Paid {paid} ⭐ in total · credited {credited} ⚡ · spent {spent} ⚡",
        "balance_rate": 'Your next {left} ⭐ earn {each} ⚡ each ({mult}×).',
        "balance_rate_base": '1 ⭐ buys {rate} ⚡.',
        "balance_bonus_line": 'Of that, {bonus} ⚡ is bonus credit — {soon} ⚡ of it expires on {date}.',
        "balance_recent": "Recent:",
        "balance_empty_hint": '/recharge adds credit whenever you want some.',
        "bot_short_description": (
            "Converts pictures, raw photos, video, audio, documents, spreadsheets, subtitles and archives "
            "— about 90 formats."
        ),
        "bot_description": 'Send a file and pick what to turn it into. Pictures, raw, video, audio, documents, spreadsheets, subtitles, archives — 90 formats in, 50 out.\n\nA HEIC from an iPhone becomes a JPG. Several pictures become one PDF. A PDF becomes pictures or text. /formats has the rest.\n\nAnything under 3 MB is free. Above that it is priced by size in ⚡ credit, bought with Stars and shown before you choose. A conversion the bot fails gives its credit back.\n\nEnglish, Uzbek and Russian. /privacy says what it keeps about you.',
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Privacy",
        "privacy_kept_heading": "What this bot keeps:",
        "privacy_stored": '• your Telegram user id, and the language you chose\n• the file you send — deleted as soon as it has been converted, or kept for up to 15 minutes after each conversion if you turn on "keep file" to convert it to more formats; anything a crash leaves behind is swept up on a timer and at startup\n• a timestamp each time you use the bot, so its owner can tell whether anyone is using it\n• how many times each button is tapped, counted per day for the whole bot, with nothing about who tapped it\n• your ⚡ credit balance, and a record of every top-up, conversion charge and ⚡ returned to your balance: the amount, what it was for, and Telegram\'s payment id for anything paid\n• whatever the bot is in the middle of doing with you, until it is finished',
        "privacy_problems_heading": "When something goes wrong:",
        "privacy_problems": (
            "Every error this bot shows anybody is written down by itself: the error code, an "
            "incident number, when it happened and the bot's version. Nothing in that is about "
            "you — not your id, not your name, not what you sent. A fault nobody reports is "
            "still a fault, which is why it does not wait to be asked.\n"
            "\n"
            "\"Add my details\" under an error offers to attach four things to that one "
            "incident: your Telegram user id, your @username if you have one, the language you "
            "picked, and whether the chat is private or a group. All four are named on screen "
            "first, and nothing is sent unless you tap Send. What you wrote and the files you "
            "sent are never part of it.\n"
            "\n"
            "Those four are cleared after 30 days on their own, and /deletemydata clears them "
            "straight away. The record of the error stays — it still happened — it just stops "
            "saying who hit it. The records themselves are deleted after 180 days."
        ),
        "privacy_seen_by_heading": "Who else sees it:",
        "privacy_seen_by": (
            "• Telegram, which carries every message both ways and sets its own terms\n"
            "• the hosting provider this bot runs on, and the database it writes to"
        ),
        "privacy_others": (
            "• and nobody beyond those — files are converted on the machine the bot runs on and are never "
            "handed to an outside service"
        ),
        "privacy_kept_for_heading": "How long it stays:",
        "privacy_kept_for": 'Settings and anything the bot is holding for you stay until you erase them or stop using it. Counted use is dropped after about three months. Payment records and your ⚡ balance are kept longer, because a dispute about a payment is settled against them.\n\nNothing here is sold, rented or used for advertising, and nothing goes to anyone not named above.',
        "privacy_your_choices": (
            "What you can do:\n"
            "/deletemydata — erase what this bot holds on you\n"
            "/terms — what the bot may be used for\n"
            "\n"
            "Blocking the bot in Telegram stops it talking to you but erases nothing, so send "
            "/deletemydata first if you want both."
        ),
        "terms_heading": "📜 Terms",
        "terms_use": (
            "Use it for what it is for, within the law and within Telegram's own terms. Do not "
            "use it to harass anyone, and do not drive it past the limits it sets — an account "
            "doing either is blocked."
        ),
        "terms_specific": "Files and payment: convert what you have the right to convert. Anything under 3 MB is free; above that the price is in ⚡ credit, bought with Telegram Stars, and is shown before you choose a format. ⚡ is taken when a conversion STARTS. If you stop a running conversion yourself, it is NOT given back; if it fails on the bot's side, ⚡ goes back to your balance automatically. Payments are FINAL: Stars are NOT refunded, and credit can NOT be withdrawn or turned back into Stars. The one exception: a payment the bot could not turn into credit is refunded automatically, in full.",
        "terms_money": 'Money: /recharge buys ⚡ credit for conversions: 2 ⚡ per ⭐, or 6 for your first 500 Stars ever and 4 for the next 500. The part above 2 is bonus credit and expires 90 days after the payment. Payments are FINAL: Stars are NOT refunded, and credit can NOT be withdrawn or turned back into Stars. The one exception: a payment the bot could not turn into credit is refunded automatically, in full. Telegram handles every payment and the bot never sees a card number. A payment that went wrong: /paysupport.',
        "terms_no_warranty": (
            "No promises: one person runs this, it is free, and it can be slow, wrong, or off "
            "entirely without warning. Keep your own copy of anything that matters."
        ),
        "policy_full_text": "Full text: {url}",
        "policy_contact": "Questions, complaints or a data request: {contact}",
        "delete_data_confirm": "⚠️ This erases what this bot holds on you. There is no undo.",
        "delete_data_consequences": 'Your language, your position in the top-up reminder and every record of when you used the bot all go. Files are not involved: whatever you converted was deleted when the conversion finished.\n\nWhat you paid stays, without your username, because a dispute about a payment is settled against it — and so does your ⚡ balance, which /balance will still show.',
        "delete_data_button_yes": "🗑 Erase it",
        "delete_data_button_no": "↩️ Keep my data",
        "delete_data_kept": "Nothing was erased.",
        "delete_data_done": (
            "🗑 Done — {rows} record(s) erased.\n"
            "\n"
            "Send /start whenever you like; the bot will treat you as new."
        ),
        "delete_data_failed": (
            "Couldn't erase that just now — something went wrong at my end. Please try again in "
            "a few minutes."
        ),
        "language_set_confirmation": "✅ Language set to English.",
        "cancel_header": "\u274c Cancelled:",
        "cancel_nothing": "Nothing to cancel — I wasn't waiting on anything from you.",
        "cancel_ask": "What should I stop? Here's what I'm waiting on:",
        "cancel_kept": "Alright — nothing cancelled.",
        "cancel_reply_box_freed": "Your reply box is free again.",
        "cancel_button_all": "❌ All of it",
        "cancel_button_none": "↩️ Nothing, keep going",
        "cancel_button_donation": '💸 Top-up amount',
        "cancel_item_donation": 'the top-up amount I asked you for',
        "cancel_item_stale_prompt": "a leftover prompt that was still waiting on an answer",
        "feedback_ask": (
            "💬 What would you like to tell the person who runs these bots? A suggestion, a "
            "problem, anything — write it in one message, as a reply to this one.\n"
            "\n"
            "Only they read it, and they can't write back: it is sent without your name or id. "
            "/cancel to stop."
        ),
        "feedback_placeholder": "Your feedback",
        "feedback_text_only": "That one has to be in words — write your feedback as a text reply to this message, or send /cancel.",
        "feedback_thanks": "✅ Thank you — sent. Only the person who runs these bots reads it, and they can't reply: nothing in it says who you are.",
        "feedback_limit": "You've sent {per_day} messages with /feedback today, which is as many as I take in a day. Thank you — write again tomorrow.",
        "feedback_failed": "Your feedback wasn't sent — something went wrong on my side. Please try again in a few minutes.",
        "cancel_button_feedback": "💬 Feedback",
        "cancel_item_feedback": "the feedback I asked you for",
        "help_brief": (
            "How it works:\n"
            "\n"
            "• Send me a file — a picture, camera raw photo, video, audio, document, spreadsheet, subtitles or archive — and choose what it should become. /formats lists everything.\n"
            "• 📝 Text from a photo or a scanned PDF: send it and choose TXT or Searchable PDF.\n"
            "• A conversion costs ⚡ credit, and its price is shown before anything is taken. /recharge tops it up; /balance shows what you have and where it went.\n"
            "• Ideas or problems: /feedback — only the person who runs these bots reads it, and they can't reply.\n"
            "\n"
            "Main commands: /convert · /formats · /balance · /recharge · /cancel\n"
            "Every command, and the prices: the full guide, below or /fullhelp.\n"
            "\n"
        ),
        "help_full_button": "📖 Full guide",
        "help_brief_button": "↩️ Short version",
        "help_page_everything": "The full guide: /fullhelp",
        "help_page_gone": "That help page no longer exists. Send /help to see the current ones.",
        "price_free_upto": "0–{mb} MB: ",
        "price_over": "over {mb} MB: ",
        "helppage_convert": (
            "🔄 /convert — turning a file into another format\n"
            "\n"
            "1. Send me the file — or send /convert first, then the file. Up to {max_mb} MB.\n"
            "2. I list the formats it can become. Tap one; \"⋯ More formats\" shows the rest.\n"
            "3. A free conversion starts at once. Otherwise you first see the price, and only \"✅ Convert\" starts it — nothing is taken before that.\n"
            "\n"
            "Price in ⚡ credit:\n"
            "{pricing}\n"
            "\n"
            "• Free: the file you send and the file you get back come to less than {free_mb} MB together.\n"
            "• The ⚡ is taken when the conversion starts, not while it waits its turn. If I fail it (I refuse the file, it runs out of time, or the result can't be sent), the ⚡ goes back to your balance. If you stop it yourself, it doesn't.\n"
            "• The price screen says how big the result may be. If it comes out bigger, it isn't sent, and the ⚡ goes back.\n"
            "• An album of pictures gets one menu for all of them (up to {batch} files). PDF puts one picture on each page; the other formats come back as a ZIP.\n"
            "• 📝 Text from a photo or a scanned PDF: choose TXT (just the text) or Searchable PDF (the same pages, with text you can select and search). For a picture, both are under \"⋯ More formats\". A Searchable PDF can be made from up to {ocr_pages} scanned pages. \"🔤 Text language\" switches between English, Russian and Uzbek. Up to {ocr_free_pages} page(s), with the files under {free_mb} MB, reading the text is free.\n"
            "• \"📌 Keep file for more formats\" keeps your file {minutes} minutes after each conversion, so you can convert it again without sending it again. It is off at first: the file is deleted straight after.\n"
            "• I wait {minutes} minutes for you to choose a format.\n"
            "\n"
            "Example: send report.docx and tap PDF. A small document converts at once, for free. A bigger one first shows its price — {least} ⚡ or more — and \"✅ Convert\" starts it."
        ),
        "helppage_recharge": (
            "⭐ /recharge — topping up ⚡ credit\n"
            "\n"
            "Conversions are paid in ⚡ credit, and ⚡ is bought with Telegram Stars (⭐).\n"
            "1. Send /recharge and tap an amount ({options} ⭐), or \"✏️ Custom ⭐\" for your own. Or straight away: /recharge 200.\n"
            "2. Telegram asks you to pay. The ⚡ is on your balance as soon as the payment arrives.\n"
            "\n"
            "What one ⭐ buys depends on how many Stars you have paid in all, ever, in any of our bots:\n"
            "{ladder}\n"
            "{bonus_rule}\n"
            "• Bonus credit is spent first, the soonest-to-expire first, so only bonus you haven't used can run out. /balance shows how much of yours is bonus, and when it runs out.\n"
            "• Payments are final: Stars are not refunded, and ⚡ can't be withdrawn or turned back into Stars.\n"
            "• /donate is the same command.\n"
            "\n"
            "{example}"
        ),
        "helppage_recharge_step_first": "  • your first {stars} ⭐: {each} ⚡ each{split}",
        "helppage_recharge_step_next": "  • the next {stars} ⭐: {each} ⚡ each{split}",
        "helppage_recharge_step_after": "  • after that: {rate} ⚡ each",
        "helppage_recharge_flat": "  • {rate} ⚡ for every ⭐",
        "helppage_recharge_bonus_part": " ({rate} + {bonus} bonus)",
        "helppage_recharge_bonus_rule": "The {rate} ⚡ per ⭐ never expire. The bonus expires {days} days after the payment.",
        "helppage_recharge_example": "Example: a first payment of {stars} ⭐ gives {total} ⚡: {base} ⚡ that never expire, and {bonus} ⚡ bonus.",
        "helppage_recharge_example_flat": "Example: {stars} ⭐ give {total} ⚡.",
        "helppage_balance": (
            "⚡ /balance — your credit, and where it went\n"
            "\n"
            "/balance shows:\n"
            "• your balance, and in all: the Stars you paid, the ⚡ they gave, the ⚡ you spent;\n"
            "• how much of the balance is bonus, how much of that runs out first, and on which day;\n"
            "• what your next Stars would buy;\n"
            "• your last {rows} movements, each with its date and one word:\n"
            "  topup — ⚡ for Stars you paid\n"
            "  bonus — the extra ⚡ a top-up gave; it expires {days} days after the payment\n"
            "  grant — ⚡ given to you: a gift (bonus that expires at the end of the day it names) or credit added by the person who runs these bots (it doesn't expire)\n"
            "  spend — a conversion\n"
            "  returned — a conversion's ⚡ back, because I failed it\n"
            "  adjustment — a correction by the person who runs these bots\n"
            "  expired — bonus that ran out unused\n"
            "• Bonus is spent before the rest, the soonest-to-expire first.\n"
            "• One wallet: the same balance in all our bots.\n"
            "\n"
            "{example}"
        ),
        "helppage_balance_example": "Example: after a first payment of {stars} ⭐ you would see \"+{base} ⚡ topup\" and \"+{bonus} ⚡ bonus\".",
        "helppage_balance_example_one": "Example: after a first payment of {stars} ⭐ you would see \"+{total} ⚡ topup\".",
        "privacy_feedback_heading": "If you send /feedback:",
        "privacy_feedback": (
            "What you write reaches the person who runs these bots as a Telegram message, with "
            "which bot it came from and your language — not your name, your username or your id. "
            "Only they read it, and they cannot reply to it. A copy is kept in the bots' "
            "database for 30 days and then deleted."
        ),
        "cancel_item_conversion": "the .{ext} file you were converting",
        "cancel_button_conversion": "🔄 The file waiting to convert",
        # ---- convert_bot-specific keys ----
        "start_greeting": "Hey! Send me a file and I'll convert it to another format.\n\n",
        "help_text": "Send me a file and I'll convert it to another format — pictures, camera raw photos, video, audio, documents, spreadsheets, subtitles and archives.\n\nCommands:\n/convert - start a conversion (or just send a file directly)\n/formats - every format I read and write\n/balance (or /mystars) - your ⚡ credit and what it was spent on\n/cancel - stop something I'm waiting on you for (I'll ask which)\n/feedback - suggest something or report a problem; only whoever runs these bots reads it, and they cannot reply\n/recharge (or /donate) - top up your ⚡ credit\n/en, /uz, /rus - switch language (or /language, which asks)\n/paysupport - trouble with a payment\n/privacy - what I keep about you\n/terms - what I may be used for\n/deletemydata - delete everything I hold on you\n/start - what I do, and how to start\n/fullhelp - this full guide (/help - the short one)\n\nPrice in ⚡ credit:\n{pricing}\n\n⚠️ NOTE: Payments are final — Stars are NOT refunded, and ⚡ credit can NOT be withdrawn. A conversion's ⚡ is taken when it STARTS, and goes back to your balance only if the bot fails it.\n\n",
        "convert_start_prompt": (
            "Send me a file to convert, up to {max_mb} MB — a picture, a video, a "
            "sound, a document, a spreadsheet or a subtitle file. Send several "
            "pictures at once and I can bind them into one PDF.\n\n"
            "/formats lists every format I know.\n\n"
            "Price in ⚡ credit:\n{pricing}\n\n/cancel to stop."
        ),
        "cancelled": "Cancelled.",
        "btn_cancel_conversion": "✖️ Cancel",
        "unknown_extension": (
            "Couldn't tell that file's format from its name — try sending it as a "
            "document with a normal extension (e.g. photo.png). /formats lists the "
            "ones I know."
        ),
        "file_too_large_download": "That file is over {max_mb} MB — Telegram bots can't download anything bigger, sorry.",
        "file_too_large_in_group": "Files over {limit} MB only come through in a private chat with me. Send this one to me directly — there it can be up to {max_mb} MB.",
        "big_download_unavailable": "Your file wasn't downloaded: files over {limit} MB reach me through a separate connection to Telegram, and it isn't answering right now. Try again in a few minutes, or send a file under {limit} MB.",
        "card_downloading": "📥 Downloading your file…",
        "card_download_sizes": "{done} of {total}",
        "card_converting": "Converting…",
        "card_sending": "Sending the result…",
        "card_elapsed": "{elapsed} so far",
        "card_left": "about {left} left",
        "card_paid": "{price} ⚡ taken, {balance} ⚡ left.",
        "card_free": "Free conversion.",
        "download_too_slow": "it was still arriving after {minutes} minutes",
        "download_stalled": "it stopped arriving: nothing came through for {seconds} seconds",
        "restarting_send_again": "🔄 I'm being updated right now — give me a few seconds and send that again.",
        "update_soon_try_later": "🔧 I'm being updated in a moment, so I can't start anything new right now — please try again in about {minutes} minute(s). I'll message you when I'm back.",
        "update_soon_try_later_soon": "🔧 I'm being updated right now, so I can't start anything new — please try again shortly. I'll message you when I'm back.",
        "update_will_reset": "🔧 Heads up: I'm about to be updated, and what you have going right now will be reset. You'll be able to start it again in a few minutes.",
        "update_done_try_now": '✅ The update is done — go ahead and try again now.',
        "gift_granted": "🎁 We have passed 100 users, thank you! You get {amount} ⚡ of free credit, good until {date}. It pays for about {conversions} paid conversions in {convert} or big-file downloads in {download}. Your balance: /balance",
        "gift_claimed": "🎁 Gift received: {amount} ⚡ of free credit! Mumu bots have passed {users} users, and this is our thank-you.\nIt is good until {date} (UTC) and pays for about {conversions} paid conversions in {convert} or big-file downloads in {download}.\nYour balance: /balance",
        "download_failed": "Couldn't download that file: {error}",
        "unsupported_format": "\".{ext}\" isn't a format I convert — /formats lists the ones I do.",
        "not_for_this_file": "That one isn't available for this file (an album, or a PDF with no scanned page).",
        "file_too_large_convert": "That file is {size} MB, over the {max_mb} MB limit — can't convert it.",
        "no_target_formats": "No different target format is available for that file.",
        "category_image": "image",
        "category_video": "video",
        "category_audio": "audio",
        "convert_options_prompt": "Got it — {size} {category} file. Convert to:\n\n{price_line}",
        "price_after_format": "What it costs depends on what comes out as well as what went in, so pick a format and I will quote it before anything is charged.",
        "quote_line_sizes": "{in_size} in · about {out_size} out",
        "quote_free": "Free — the whole job stays under {mb} MB.",
        "quote_cost": "Cost: {price} ⚡",
        "quote_fixed": "That price is fixed now. If the result comes out bigger than the estimate, you are not charged more.",
        "quote_balance": "You have {balance} ⚡.",
        "btn_quote_go": "✅ Convert · {price} ⚡",
        "btn_quote_free": "✅ Convert",
        "btn_quote_back": "↩️ Another format",
        "converted_file_caption": "Here's your converted file!",
        "converted_file_caption_free": "Here's your converted file! (free)",
        "conversion_expired": "This conversion session expired — send the file again.",
        "convert_invoice_title": "Convert {src} to {target}",
        "convert_invoice_description": "One-time conversion of your {size_kb} KB file.",
        "convert_invoice_label": "{src}→{target} conversion",
        "topup_needed": 'This one costs {price} ⚡ and you have {balance} ⚡.\n\nInvoice sent for {stars} ⭐, which adds {credited} ⚡ — enough for this conversion, and the rest stays on your balance. /cancel to back out.',
        "credit_policy_note": "⚠️ NOTE: ⚡ is taken when the conversion STARTS. Stop it yourself and it is NOT given back; if it fails on the bot's side, ⚡ goes straight back to your balance. Payments are final — Stars are not refunded.",
        "send_limit_note": "📤 The converted file can be up to {mb} MB. If it comes out bigger, it won't be sent — and its ⚡ goes straight back to your balance.",
        "too_many_megapixels": 'That picture is {mp} megapixels — over the {max} MP I can convert safely. Nothing was charged.',
        "too_big_to_send_note": "📐 {formats} aren't offered: at {mp} MP they would come out over the {limit} MB a bot can send. JPG, WEBP or AVIF keep it sendable.",
        "raw_too_big": "That raw photo is {mp} megapixels. This server converts camera raw photos up to {max}: past that even the half-size decode needs more memory than it has. Nothing was charged.",
        "raw_half_note": "⚠️ NOTE: this raw photo is {mp} MP. It is converted at half size ({out} MP): the full picture needs more memory than this server has.",
        "video_fit_hdr_note": "⚠️ NOTE: this video is {w}×{h} HDR. This server converts it at {fw}×{fh} — the full picture needs more memory than it has. The colours are kept.",
        "video_fit_big_note": "⚠️ NOTE: this video is {w}×{h}. WEBM is made at {fw}×{fh} here; MP4, MKV and MOV keep the full size.",
        "video_fit_huge_note": "⚠️ NOTE: this video is {w}×{h}. This server converts it at {fw}×{fh} — the full picture needs more memory than it has.",
        "nothing_sendable": 'Every format I could offer would come out over the {limit} MB a bot can send for a picture this size. Nothing was charged.',
        "too_big_to_send_alert": 'That format would come out over the {limit} MB a bot can send for this picture.',
        "keep_toggle_off": '📌 Keep file for more formats: off',
        "keep_toggle_on": '📌 Keep file for more formats: on',
        "ocr_lang_toggle": "🔤 Text language: {name} (tap to change)",
        "ocr_lang_set": "Text language is now {name}.",
        "ocr_lang_eng": "English",
        "ocr_lang_rus": "Russian",
        "ocr_lang_uzb": "Uzbek (Latin and Cyrillic)",
        "ocr_too_many_pages_note": "⚠️ NOTE: more than {max} pages of this PDF are pictures without text. Text is recognised on at most {max} such pages here, so TXT gives only the text the other pages already have, and a searchable PDF is not offered.",
        "target_ocrpdf": "Searchable PDF",
        "keep_explained_on": "I'll keep your file for {minutes} minutes after each conversion, so you can convert it to other formats too. /cancel deletes it sooner.",
        "keep_explained_off": 'Your file will be deleted as soon as it has been converted.',
        "storage_full_user": 'You already have {used} MB of files with me, and I hold up to {limit} MB per person. Let a conversion finish or /cancel one, then send it again.',
        "storage_full_global": "I'm short on temporary space right now — send that again in a few minutes.",
        "cancel_item_job_running": 'the {target} conversion of your {size} MB {src} — running',
        "cancel_item_job_queued": 'the {target} conversion of your {size} MB {src} — queued, nothing charged yet',
        "cancel_button_job": '⏹ {target} conversion',
        "queue_full": 'You already have {count} conversions queued or running. Send more when some of them finish.',
        "job_queued": '⏳ {src} → {target} is queued, {ahead} ahead of it. Nothing is taken until it starts.',
        "job_starting": '⏳ {src} → {target}: starting…',
        "job_not_running": 'That conversion has already finished.',
        "payment_kept_as_credit": 'Payment received — +{credited} ⚡, your balance is {balance} ⚡. The file had already expired, so nothing was converted; send it again and it will be paid from your balance.',
        "job_progress": "⏳ Still converting — {elapsed} min so far. I'll write again in {next} min, and in {last} min at the latest you'll hear how it ended.",
        "job_progress_last": '⏳ Still converting — {elapsed} min so far. The last update comes in {last} min: by then it will be done, or stopped with its ⚡ back on your balance.',
        "job_stop_button": '⏹ Stop',
        "job_stop_button_queued": '✖ Remove from queue',
        "job_done": '✅ {src} → {target} done.',
        "balance_left_line": '{balance} ⚡ left on your balance.',
        "job_user_stopped": '⏹ Stopped — the {price} ⚡ it started with stays spent.',
        "job_user_stopped_free": '⏹ Stopped.',
        "job_removed_from_queue": '✖ Removed from the queue — nothing was charged.',
        "job_no_credit": "That conversion couldn't start: it costs {price} ⚡ and your balance is {balance} ⚡. Nothing was charged — /recharge tops it up.",
        "job_refunded_line": 'The {price} ⚡ went back to your balance — you have {balance} ⚡.',
        "job_reason_refused": "I couldn't convert that file: {detail}",
        "job_reason_timeout": 'That conversion ran past its {minutes}-minute limit, so I stopped it. The owner has been told.',
        "job_reason_too_slow": "This one would take about {minutes} minutes to convert, and a conversion here can run for {limit}, so I stopped it now rather than make you wait for the limit. A shorter clip, or a lower resolution, will fit.",
        "job_reason_crash": 'The conversion failed on my side. The owner has been told.',
        "job_reason_no_memory": "This one needs more memory to convert than this server has — that happens with 4K and HDR video. Your file is fine: a version at 1080p or lower will convert.",
        "job_reason_too_large": 'The {target} came out at {mb} MB, more than the {limit} MB I can send back for this conversion — JPG, WEBP or AVIF will be far smaller. The owner has been told.',
        "job_reason_send_failed": 'The file converted, but sending it to you failed. The owner has been told.',
        "job_reason_interrupted": 'I was restarted in the middle of that conversion. Send it again in a minute.',
        "unknown_order": "Unknown order.",
        "unrecognized_message": (
            "That's not a file I can convert — send me one, or /formats to see what "
            "I take. Name a format (\"heic\", \"epub\") and I'll tell you what it "
            "becomes."
        ),
        "unknown_command": "I don't recognize that command. Send /start to see what I can do.",
        # ---- /formats, and the two-level format menu ----
        "category_document": "document",
        "category_data": "data",
        "category_subtitle": "subtitle",
        "category_archive": "archive",
        "formats_title": "📁 What I convert",
        "formats_intro": (
            "{sources} formats in, {targets} out — {pairs} combinations in all. "
            "Send me a file and I'll show you what that one can become; tap a "
            "category below for the whole list."
        ),
        "formats_highlights": (
            "Some of the less obvious ones:\n"
            "• iPhone photo (HEIC) → JPG or PNG\n"
            "• Any picture — or a whole album at once — into one PDF\n"
            "• PDF or EPUB → PNG, JPG or plain text\n"
            "• Word document → PDF, HTML or Markdown\n"
            "• CSV, JSON or XML → an XLSX spreadsheet\n"
            "• Camera raw (CR3, NEF, ARW, DNG…) → JPG, PNG or a 16-bit TIFF\n"
            "• A RAR or 7z your phone cannot open → ZIP\n"
            "• Subtitles: SRT, VTT and ASS between each other, or just the transcript"
        ),
        "formats_reads": "Reads",
        "formats_writes": "Writes",
        "formats_category_title": "📁 {category} files",
        "formats_lookup": ".{ext} is in the {category} category. I convert it to: {targets}",
        "formats_lookup_none": (
            "I can read .{ext}, but there is nothing this host can turn it into."
        ),
        "formats_lookup_unknown": (
            "I don't know a \".{ext}\" — /formats lists every format I do."
        ),
        "btn_more_formats": "⋯ More formats",
        "btn_fewer_formats": "‹ Fewer",
        "btn_what_formats": "📁 What can you convert?",
        "btn_formats_back": "‹ All categories",
        "convert_options_prompt_batch": (
            "Got it — {count} files, {size} in all. Convert to:\n\n{price_line}"
        ),
        "converted_file_caption_zip": "Here you go — {items} files, in a zip.",
        "converted_file_caption_pdf": "Here you go — {items} pictures, one per page.",
        "converted_file_caption_pages": "Here you go — {items} pages, in a zip.",
        "converted_docx_note": (
            "The text is real text you can edit, with headings where the PDF "
            "set a line larger than the rest, bold where it said bold, and the "
            "pictures in the order they appeared. The page layout is not "
            "carried over: columns come out one after another, a table comes "
            "out as its text, and the page breaks are the PDF's."
        ),
        "converted_layout_note": (
            "The headings, emphasis, lists and tables are all there. The page "
            "layout is re-flowed rather than copied exactly — this host has no "
            "Word to ask."
        ),
        "batch_mixed_formats": (
            "Those files aren't all the same format, so send them one at a time "
            "— or a set of pictures, which I can bind into a single PDF."
        ),
        # ---- convert_utils.py pricing-table strings ----
        "price_free": "Free",
        "price_not_supported": "not supported",
        "price_cloud_api_note": "(cloud Bot API's 20 MB download ceiling)",
        "price_from": "from {price} ⚡: most pictures, songs and documents",
        "price_example": "a {mb} MB video to MP4: {price} ⚡",
        "price_at_most": "the biggest file there is, {mb} MB: at most {price} ⚡",
        "price_quoted_first": "The exact price is shown before anything is charged, and it is what you pay.",
    },
    "uz": {
        "animation_too_long_note": "🎞 {formats} taklif qilinmaydi: bu video {seconds} soniyadan uzun, undan o'z vaqtida animatsiya yasab bo'lmaydi. Uni qisqartiring yoki video formatini tanlang.",
        "animation_too_long_alert": "Bu video {seconds} soniyadan uzun, o'z vaqtida animatsiyaga aylantirib bo'lmaydi. Hech narsa yechilmadi. Uni qisqartiring yoki video formatini tanlang.",
        "animation_too_many_frames": "Bu animatsiyada {frames} ta kadr bor, men {max} tadan ko'pini aylantira olmayman. Hech narsa yechilmadi. Qisqaroq birini yuboring.",
        "transfer_waiting": "⏳ Hozir boshqa katta fayl uzatilmoqda. Siznikini u tugashi bilan boshlayman — hech narsa qilishingiz shart emas.",
        "flood_wait": 'Juda tez yuboryapsiz — {seconds} soniyacha kutib, keyin davom eting.',
        "sibling_blurb": 'Oilamizdagi boshqa botlar pastda 👇',
        "donation_nudge": "⚡ Kattaroq fayllarni tez-tez o'girasizmi? /recharge orqali kreditni oldindan to'ldirib qo'yishingiz mumkin.",
        "donate_unknown_currency": '"{currency}" degan valyuta yo\'q — xtr yoki usd deb yozing.',
        "donate_currency_not_configured": "Bu botda hozircha {currency} bilan to'lab bo'lmaydi — Stars'dan foydalaning.",
        "donate_invalid_amount": "Miqdor noto'g'ri — masalan, /recharge 500 deb yozing.",
        "donate_prompt": "Konvertatsiyalar uchun ⚡ kreditingizni to'ldiring. Quyidagi miqdorlardan birini tanlang yoki o'zingiz yozish uchun «Boshqa»ni bosing (/recharge <son> deb ham yuborishingiz mumkin).",
        "donate_custom_button": "✏️ Boshqa {symbol}",
        "donate_too_many_stars": "Bu juda ko'p! Bir martalik to'ldirish {max} ⭐ dan oshmasin.",
        "donate_out_of_range": "{currency} bilan xayriya {lo} dan {hi} {symbol} gacha bo'lishi kerak.",
        "donate_invoice_title": "⚡ kreditni to'ldirish",
        "donate_invoice_description": "Konvertatsiyalar uchun balansingizga {credited} ⚡ kredit qo'shadi.",
        "donate_invoice_label": '⚡ kredit',
        "donate_invoice_description_fiat": 'Server xarajatlari uchun bir martalik ixtiyoriy xayriya. Rahmat!',
        "donate_prompt_credit": "To'lagan har bir ⭐ konvertatsiyalarga sarflanadigan ⚡ kreditga aylanadi. Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×): {rate} ⚡ oddiy kredit, qolgani esa to'lovdan {days} kun o'tgach muddati tugaydigan bonus. ⚡ ni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI.",
        "donate_prompt_credit_base": "To'lagan har bir ⭐ konvertatsiyalarga sarflanadigan {rate} ⚡ kreditga aylanadi. ⚡ ni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI.",
        "donate_invoice_error": "⚠️ Telegram to'lov hisobini yaratmadi: {error}",
        "stars_unit": "Stars (yulduzcha)",
        "donate_custom_ask": "Balansni qancha {unit} ga to'ldirmoqchisiz? Faqat sonni yozib yuboring.",
        "donate_invalid_amount_retry": "Miqdor noto'g'ri — qaytadan urinish uchun /recharge yuboring.",
        "donate_thanks": '🙏 {amount} ⭐ uchun katta rahmat!',
        "topup_thanks": "⚡ Balans to'ldirildi — {stars} ⭐ qabul qilindi. Rahmat!\n\nBalansingizga qo'shildi: +{credited} ⚡\nHozirgi balans: {balance} ⚡",
        "topup_thanks_bonus": '🎁 Ustiga {bonus} ⚡ bonus kredit — muddati {date} kuni tugaydi.',
        "credit_cannot_be_withdrawn": "⚡ — ConvertBot'dagi konvertatsiyalar uchun kredit: uni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. To'lovda muammo bo'lsa: /paysupport",
        "topup_refunded": "⚠️ {stars} ⭐ to'lovingiz o'tdi, lekin men tomondagi muammo tufayli ⚡ balansingizga tushmadi, shuning uchun to'lovni qaytardim: {stars} ⭐ hisobingizga qaytdi. Iltimos, bir necha daqiqadan keyin qayta urinib ko'ring.",
        "topup_failed": "⚠️ {stars} ⭐ to'lovingiz o'tdi, lekin men tomondagi muammo tufayli ⚡ balansingizga tushmadi. Hech narsa yo'qolmaydi: egasiga xabar berildi, u qo'lda qo'shib qo'yadi. Murojaat uchun: /paysupport",
        "paysupport_text": "💳 To'lov bo'yicha yordam\n\nTo'lovlar QAYTARILMAYDI: Stars qaytarib berilmaydi, ⚡ kreditni esa yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. Yagona istisno: bot kreditga aylantira olmagan to'lov sizga avtomatik ravishda to'liq qaytariladi.\n\nTo'lovda xatolik bo'lgan bo'lsa — pul yechilgan-u, kredit tushmagan bo'lsa yoki ikki marta yechilgan bo'lsa — {contact} manziliga to'lov sanasi, miqdori va Telegram ID raqamingizni ({user_id}) yozib yuboring. Tekshirilib, kredit bilan to'g'rilab beriladi.\n\n/balance har bir to'lovni va u qancha kredit qo'shganini ko'rsatadi.",
        "report_button": "🐞 Ma'lumotlarimni qo'shish",
        "problem_logged_note": "Bu muammo bot egasi uchun allaqachon yozib olindi — kodi, vaqti va bot versiyasi. Siz haqingizda hech narsa yo'q.",
        "report_disclaimer": "📨 Bu muammoga o'zingiz haqingizdagi ma'lumotni ham qo'shasizmi?\n\nSizsiz yozib olingani: bot nomi, {code} xato kodi, {incident} hodisa raqami, qachon yuz bergani va bot versiyasi.\n\n\"Yuborish\" tugmasi to'rt narsani qo'shadi: Telegram ID raqamingiz, bo'lsa @username'ingiz, siz tanlagan til va bu suhbat shaxsiymi yoki guruhmi. Yozganingiz ham, yuborgan faylingiz ham emas.\n\nShunda sizning holatingizni loglardan topib bo'ladi — kam uchraydigan muammoni odatda shu tuzatib beradi. To'liq o'zingiz hal qilasiz.",
        "report_send": "📨 Ma'lumotlarimni yuborish",
        "report_cancel": '✖️ Bekor qilish',
        "report_sent": "✅ Rahmat — ma'lumotlaringiz {incident} hodisasiga biriktirildi, endi uni topish oson.",
        "report_already": "Ma'lumotlaringiz {incident} hodisasiga allaqachon biriktirilgan. Boshqa hech narsa yuborilmadi.",
        "report_cancelled": "Bekor qilindi — siz haqingizda hech narsa yuborilmadi. Muammoning o'zi yozib olingan holicha qoladi.",
        "report_failed": "⚠️ Hozir xabarni yuborib bo'lmadi. Keyinroq qayta urinib ko'ring.",
        "report_invalid": 'Bu tugma endi ishlamaydi.',
        "crash_notice": "⚠️ Buni bajarishda bot tomonida xatolik yuz berdi, shuning uchun amal bajarilmadi. Birozdan keyin qayta urinib ko'ring.",
        "sandbox_notice": '🧪 Test rejimi — haqiqiy Stars yechilmadi.',
        "balance_header": "⚡ Balansingiz: {balance}",
        "balance_totals": "Jami {paid} ⭐ to'landi · {credited} ⚡ qo'shildi · {spent} ⚡ sarflandi",
        "balance_rate": 'Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×).',
        "balance_rate_base": '1 ⭐ = {rate} ⚡.',
        "balance_bonus_line": 'Shundan {bonus} ⚡ — bonus kredit; {soon} ⚡ ning muddati {date} kuni tugaydi.',
        "balance_recent": 'Oxirgi amallar:',
        "balance_empty_hint": "/recharge orqali istalgan paytda balansni to'ldirishingiz mumkin.",
        "bot_short_description": (
            "Rasm, kamera raw, video, audio, hujjat, jadval, subtitr va arxivlarni 90 ga yaqin format "
            "orasida o'giradi."
        ),
        "bot_description": "Fayl yuboring va qaysi formatga o'girishni tanlang. Rasm, kamera raw, video, audio, hujjat, jadval, subtitr, arxiv — 90 formatdan 50 formatga.\n\niPhone'dagi HEIC rasm — JPG ga. Bir nechta rasm — bitta PDF ga. PDF — rasm yoki matnga. Hammasi: /formats\n\n3 MB gacha bepul. Kattaroq fayl hajmiga qarab Stars bilan sotib olinadigan ⚡ kreditda to'lanadi, narx oldindan ko'rsatiladi. Bot xato qilsa, ⚡ balansga qaytadi.\n\nIngliz, o'zbek va rus tillarida. Nima saqlanishi: /privacy",
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Maxfiylik",
        "privacy_kept_heading": "Bu bot nimalarni saqlaydi:",
        "privacy_stored": "• Telegram ID raqamingiz va tanlagan tilingiz\n• yuborgan faylingiz — o'girilishi bilanoq o'chiriladi; «faylni saqlash» yoqilgan bo'lsa, boshqa formatlarga ham o'girish uchun har konvertatsiyadan keyin 15 daqiqagacha saqlanadi; nosozlikdan keyin qolib ketgan fayllar muntazam va bot ishga tushganda tozalanadi\n• botdan foydalangan vaqtingiz — bot egasi botdan umuman foydalanilayotganini bilishi uchun\n• har bir tugma necha marta bosilgani — butun bot bo'yicha kunlik umumiy hisob, kim bosgani haqida hech qanday ma'lumotsiz\n• ⚡ kredit balansingiz va har bir to'ldirish, konvertatsiya uchun yechilgan hamda balansga qaytgan ⚡ yozuvi: miqdori, nima uchunligi va pullik to'lovlarda Telegram to'lov raqami\n• bot siz uchun bajarayotgan ish — u tugagunicha",
        "privacy_problems_heading": "Nimadir noto'g'ri ketganda:",
        "privacy_problems": "Bot kimgadir xato ko'rsatsa, uni o'zi yozib qo'yadi: xato kodi, hodisa raqami, qachon yuz bergani va bot versiyasi. Bularda siz haqingizda hech narsa yo'q — ID raqamingiz ham, ismingiz ham, nima yuborganingiz ham. Hech kim xabar bermagan nosozlik ham nosozligicha qoladi, shuning uchun bot so'ralishini kutmaydi.\n\nXato ostidagi \"Ma'lumotlarimni qo'shish\" tugmasi o'sha bitta hodisaga to'rt narsani biriktirishni taklif qiladi: Telegram ID raqamingiz, bo'lsa @username'ingiz, siz tanlagan til va bu suhbat shaxsiymi yoki guruhmi. To'rttasi ham avval ekranda aytiladi va \"Yuborish\"ni bosmasangiz hech narsa yuborilmaydi. Yozganlaringiz va yuborgan fayllaringiz esa bunga hech qachon kirmaydi.\n\nBu to'rttasi 30 kundan keyin o'z-o'zidan o'chiriladi, /deletemydata esa darhol o'chiradi. Xatolik yozuvining o'zi qoladi — u haqiqatan yuz bergan — shunchaki endi kim duch kelgani yozilmaydi. Yozuvlarning o'zi 180 kundan keyin o'chiriladi.",
        "privacy_seen_by_heading": "Yana kim ko'ra oladi:",
        "privacy_seen_by": "• Telegram — barcha xabarlar u orqali o'tadi va u o'z qoidalari asosida ishlaydi\n• bot joylashgan hosting va bot foydalanadigan ma'lumotlar bazasi",
        "privacy_others": "• boshqa hech kim — fayllar botning o'z serverida o'giriladi va hech qanday tashqi xizmatga yuborilmaydi",
        "privacy_kept_for_heading": "Qancha vaqt saqlanadi:",
        "privacy_kept_for": "Sozlamalaringiz va bot siz uchun saqlab turgan narsalar ularni o'chirmaguningizcha yoki botdan foydalanishni to'xtatmaguningizcha turadi. Foydalanish qaydlari taxminan uch oydan keyin o'chiriladi. To'lov yozuvlari va ⚡ balansingiz esa uzoqroq saqlanadi, chunki to'lov bo'yicha nizolar ular asosida hal qilinadi.\n\nBu ma'lumotlar sotilmaydi, ijaraga berilmaydi, reklamada ishlatilmaydi va yuqorida aytilganlardan boshqa hech kimga berilmaydi.",
        "privacy_your_choices": "Nima qilishingiz mumkin:\n/deletemydata — bot siz haqingizda saqlagan ma'lumotlarni o'chirish\n/terms — botdan foydalanish shartlari\n\nBotni Telegramda bloklasangiz, u sizga yozmay qo'yadi, lekin hech narsa o'chmaydi. Ikkalasini ham xohlasangiz, avval /deletemydata yuboring.",
        "terms_heading": "📜 Shartlar",
        "terms_use": "Botdan maqsadiga ko'ra, qonun va Telegram qoidalari doirasida foydalaning. Uni boshqalarni bezovta qilish uchun ishlatmang va belgilangan cheklovlardan oshirib yuklamang — aks holda akkaunt bloklanadi.",
        "terms_specific": "Fayllar va to'lov: faqat o'girishga huquqingiz bor fayllarni o'giring. 3 MB gacha bepul; kattarog'ining narxi Telegram Stars bilan sotib olinadigan ⚡ kreditda bo'lib, format tanlashdan oldin ko'rsatiladi. ⚡ konvertatsiya BOSHLANGANDA yechiladi. Ishlayotgan konvertatsiyani o'zingiz to'xtatsangiz, ⚡ QAYTARILMAYDI; bot tomonidagi xatolik tufayli bajarilmasa, ⚡ avtomatik ravishda balansingizga qaytib tushadi. To'lovlar QAYTARILMAYDI: Stars qaytarib berilmaydi, kreditni esa yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. Yagona istisno: bot kreditga aylantira olmagan to'lov avtomatik ravishda to'liq qaytariladi.",
        "terms_money": "Pul haqida: /recharge orqali konvertatsiyalar uchun ⚡ kredit sotib olasiz: har ⭐ uchun 2 ⚡, umumiy hisobda birinchi 500 ta Stars uchun esa 6 ⚡ dan, keyingi 500 tasi uchun 4 ⚡ dan. 2 ⚡ dan ortig'i bonus kredit bo'lib, to'lovdan 90 kun o'tgach muddati tugaydi. To'lovlar QAYTARILMAYDI: Stars qaytarib berilmaydi, kreditni esa yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. Yagona istisno: bot kreditga aylantira olmagan to'lov avtomatik ravishda to'liq qaytariladi. Har bir to'lovni Telegram o'tkazadi, bot karta raqamingizni ko'rmaydi. To'lovda muammo bo'lsa: /paysupport.",
        "terms_no_warranty": "Kafolat yo'q: botni bir kishi yuritadi va u oldindan ogohlantirmasdan sekinlashishi, xato qilishi yoki butunlay to'xtab qolishi mumkin. Siz uchun muhim narsalarning nusxasini o'zingizda saqlang.",
        "policy_full_text": "To'liq matn: {url}",
        "policy_contact": "Savol, shikoyat yoki ma'lumot so'rovi uchun: {contact}",
        "delete_data_confirm": "⚠️ Bot siz haqingizda saqlagan ma'lumotlar o'chiriladi. Buni ortga qaytarib bo'lmaydi.",
        "delete_data_consequences": "Til sozlamangiz, eslatmalar hisobi va botdan qachon foydalanganingiz haqidagi qaydlar o'chiriladi. Fayllarga bu taalluqli emas: o'girilgan fayllar konvertatsiya tugashi bilanoq o'chirilgan.\n\nTo'lovlaringiz foydalanuvchi nomingizsiz saqlanib qoladi, chunki to'lov bo'yicha nizolar ular asosida hal qilinadi. ⚡ balansingiz ham saqlanadi va /balance uni ko'rsatishda davom etadi.",
        "delete_data_button_yes": "🗑 O'chirilsin",
        "delete_data_button_no": "↩️ Ma'lumotlarim qolsin",
        "delete_data_kept": "Hech narsa o'chirilmadi.",
        "delete_data_done": "🗑 Bajarildi — {rows} ta yozuv o'chirildi.\n\nIstalgan payt /start yuborsangiz, bot sizni yangi foydalanuvchi sifatida kutib oladi.",
        "delete_data_failed": "Hozir o'chirib bo'lmadi — bot tomonida xatolik yuz berdi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "language_set_confirmation": "✅ Til o'zbekchaga o'zgartirildi.",
        "cancel_header": "\u274c Bekor qilindi:",
        "cancel_nothing": "Bekor qilinadigan amal yo'q — hozir hech narsa kutilmayapti.",
        "cancel_ask": 'Qaysi birini bekor qilay? Hozir quyidagilar kutilmoqda:',
        "cancel_kept": "Yaxshi — hech narsa bekor qilinmadi.",
        "cancel_reply_box_freed": "Xabar yozish maydoni endi bo'sh.",
        "cancel_button_all": "❌ Hammasini",
        "cancel_button_none": '↩️ Hech birini, davom etamiz',
        "cancel_button_donation": "💸 To'ldirish miqdori",
        "cancel_item_donation": "kiritilishi kutilayotgan to'ldirish miqdori",
        "cancel_item_stale_prompt": "javobsiz qolgan eski so'rov",
        "feedback_ask": (
            "💬 Bu botlarni yuritayotgan odamga nima demoqchisiz? Taklif, muammo — nima bo'lsa "
            "ham, bitta xabarda, shu xabarga javob qilib yozing.\n"
            "\n"
            "Uni faqat o'sha odam o'qiydi va sizga javob yoza olmaydi: xabar ismingiz va ID "
            "raqamingizsiz yuboriladi. To'xtatish: /cancel"
        ),
        "feedback_placeholder": "Fikringiz",
        "feedback_text_only": "Bu yerga faqat matn qabul qilinadi — fikringizni shu xabarga javob qilib so'z bilan yozing yoki /cancel yuboring.",
        "feedback_thanks": "✅ Rahmat — yuborildi. Uni faqat bu botlarni yuritayotgan odam o'qiydi va javob yoza olmaydi: xabarda kimligingiz ko'rsatilmagan.",
        "feedback_limit": "Bugun /feedback orqali {per_day} ta xabar yubordingiz — bir kunda shuncha qabul qilaman. Rahmat, ertaga yana yozishingiz mumkin.",
        "feedback_failed": "Fikringiz yuborilmadi — bot tomonida nimadir buzildi. Iltimos, bir necha daqiqadan keyin qayta urinib ko'ring.",
        "cancel_button_feedback": "💬 Fikr-mulohaza",
        "cancel_item_feedback": "men so'ragan fikr-mulohaza",
        "help_brief": (
            "Bot qanday ishlaydi:\n"
            "\n"
            "• Menga fayl yuboring — rasm, kamera raw, video, audio, hujjat, jadval, subtitr yoki arxiv — va u nimaga aylanishini tanlang. Barcha formatlar: /formats\n"
            "• 📝 Rasm yoki skanerlangan PDF dagi matn: faylni yuboring va TXT yoki Matnli PDF ni tanlang.\n"
            "• Har bir konvertatsiya ⚡ kredit turadi, narxi esa hech narsa yechilishidan oldin ko'rsatiladi. Kreditni /recharge bilan to'ldirasiz; /balance qancha qolgani va nimaga sarflanganini ko'rsatadi.\n"
            "• Taklif yoki muammo: /feedback — uni faqat bu botlarni yuritayotgan odam o'qiydi, javob qaytmaydi.\n"
            "\n"
            "Asosiy buyruqlar: /convert · /formats · /balance · /recharge · /cancel\n"
            "Barcha buyruqlar va narxlar: to'liq qo'llanmada — pastdagi tugma yoki /fullhelp.\n"
            "\n"
        ),
        "help_full_button": "📖 To'liq qo'llanma",
        "help_brief_button": "↩️ Qisqa variant",
        "help_page_everything": "To'liq qo'llanma: /fullhelp",
        "help_page_gone": "Bu yordam sahifasi endi yo'q. Hozirgilarini ko'rish uchun /help yuboring.",
        "price_free_upto": "0–{mb} MB: ",
        "price_over": "{mb} MB dan ortiq: ",
        "helppage_convert": (
            "🔄 /convert — faylni boshqa formatga o'girish\n"
            "\n"
            "1. Faylni yuboring — yoki avval /convert, keyin faylni. {max_mb} MB gacha.\n"
            "2. Uni qaysi formatlarga o'girish mumkinligini ko'rsataman. Birini bosing; \"⋯ Yana formatlar\" qolganlarini ochadi.\n"
            "3. Bepul konvertatsiya darhol boshlanadi. Aks holda avval narxini ko'rasiz va uni faqat \"✅ O'girish\" tugmasi boshlaydi — undan oldin hech narsa yechilmaydi.\n"
            "\n"
            "⚡ kreditdagi narx:\n"
            "{pricing}\n"
            "\n"
            "• Bepul: yuborgan faylingiz va qaytadigan fayl birgalikda {free_mb} MB dan kichik bo'lsa.\n"
            "• ⚡ navbat kutayotganda emas, konvertatsiya boshlanganda yechiladi. Agar uni bajara olmasam (faylni rad etsam, vaqt yetmasa yoki natijani yuborib bo'lmasa), ⚡ balansingizga qaytadi. O'zingiz to'xtatsangiz, qaytmaydi.\n"
            "• Narx ekrani natija qanchalik katta bo'lishi mumkinligini aytadi. Undan katta chiqsa, yuborilmaydi va ⚡ qaytadi.\n"
            "• Rasmlar albomi uchun hammasiga bitta menyu chiqadi ({batch} tagacha fayl). PDF har bir rasmni alohida sahifaga qo'yadi; boshqa formatlar ZIP bo'lib keladi.\n"
            "• 📝 Rasm yoki skanerlangan PDF'dan matn: TXT (faqat matn) yoki Matnli PDF (o'sha sahifalar, matnini belgilash va qidirish mumkin) ni tanlang. Rasm uchun ikkalasi ham \"⋯ Yana formatlar\" ichida. Matnli PDF {ocr_pages} tagacha skanerlangan sahifadan yasaladi. \"🔤 Matn tili\" inglizcha, ruscha va o'zbekcha orasida almashtiradi. {ocr_free_pages} sahifagacha va fayllar {free_mb} MB dan kichik bo'lsa, matnni o'qish bepul.\n"
            "• \"📌 Faylni boshqa formatlar uchun saqlash\" faylingizni har bir konvertatsiyadan keyin {minutes} daqiqa saqlaydi — qayta yubormasdan yana o'girishingiz mumkin. Dastlab o'chiq: fayl darhol o'chiriladi.\n"
            "• Format tanlashingizni {minutes} daqiqa kutaman.\n"
            "\n"
            "Misol: hisobot.docx ni yuboring va PDF ni bosing. Kichik hujjat darhol va bepul o'giriladi. Kattarog'i avval narxini ko'rsatadi — {least} ⚡ yoki ko'proq — va uni \"✅ O'girish\" boshlaydi."
        ),
        "helppage_recharge": (
            "⭐ /recharge — ⚡ kreditni to'ldirish\n"
            "\n"
            "Konvertatsiyalar ⚡ kredit bilan to'lanadi, ⚡ esa Telegram Stars (⭐) bilan sotib olinadi.\n"
            "1. /recharge yuboring va miqdorni bosing ({options} ⭐) yoki o'z miqdoringiz uchun \"✏️ Boshqa ⭐\". Yoki to'g'ridan-to'g'ri: /recharge 200.\n"
            "2. Telegram to'lovni so'raydi. To'lov kelishi bilan ⚡ balansingizda bo'ladi.\n"
            "\n"
            "Bitta ⭐ qancha ⚡ berishi siz shu paytgacha istalgan botimizda jami qancha Stars to'laganingizga bog'liq:\n"
            "{ladder}\n"
            "{bonus_rule}\n"
            "• Avval bonus kredit sarflanadi, muddati eng tez tugaydigani birinchi, shuning uchun faqat ishlatilmagan bonus yo'qolishi mumkin. Bonusingiz qancha va qachon tugashini /balance ko'rsatadi.\n"
            "• To'lovlar qaytarilmaydi: Stars qaytarilmaydi, ⚡ ni yechib olib yoki Stars'ga aylantirib bo'lmaydi.\n"
            "• /donate — xuddi shu buyruq.\n"
            "\n"
            "{example}"
        ),
        "helppage_recharge_step_first": "  • dastlabki {stars} ⭐: har biri {each} ⚡{split}",
        "helppage_recharge_step_next": "  • keyingi {stars} ⭐: har biri {each} ⚡{split}",
        "helppage_recharge_step_after": "  • undan keyin: har biri {rate} ⚡",
        "helppage_recharge_flat": "  • har bir ⭐ uchun {rate} ⚡",
        "helppage_recharge_bonus_part": " ({rate} + {bonus} bonus)",
        "helppage_recharge_bonus_rule": "Har ⭐ uchun {rate} ⚡ hech qachon tugamaydi. Bonusning muddati to'lovdan {days} kun o'tib tugaydi.",
        "helppage_recharge_example": "Misol: birinchi marta {stars} ⭐ to'lasangiz, {total} ⚡ olasiz: muddati tugamaydigan {base} ⚡ va {bonus} ⚡ bonus.",
        "helppage_recharge_example_flat": "Misol: {stars} ⭐ — {total} ⚡.",
        "helppage_balance": (
            "⚡ /balance — kreditingiz va u qayerga ketgani\n"
            "\n"
            "/balance quyidagilarni ko'rsatadi:\n"
            "• balansingiz va jami: to'lagan Stars, ular bergan ⚡, sarflagan ⚡;\n"
            "• balansning qanchasi bonus, uning qanchasi birinchi bo'lib va qaysi kuni tugashi;\n"
            "• keyingi Stars'ingiz qancha ⚡ berishi;\n"
            "• oxirgi {rows} ta amal, har biri sanasi va bitta so'z bilan:\n"
            "  topup — to'lagan Stars'ingiz uchun ⚡\n"
            "  bonus — to'ldirish bergan qo'shimcha ⚡; to'lovdan {days} kun o'tib tugaydi\n"
            "  grant — sizga berilgan ⚡: sovg'a (ko'rsatilgan kun oxirida tugaydigan bonus) yoki botlarni yuritayotgan odam qo'shgan kredit (u tugamaydi)\n"
            "  spend — konvertatsiya\n"
            "  returned — men bajara olmagan konvertatsiyaning qaytgan ⚡ i\n"
            "  adjustment — botlarni yuritayotgan odamning tuzatishi\n"
            "  expired — ishlatilmay tugagan bonus\n"
            "• Bonus qolganidan oldin sarflanadi, muddati eng tez tugaydigani birinchi.\n"
            "• Hamyon bitta: barcha botlarimizda balans bir xil.\n"
            "\n"
            "{example}"
        ),
        "helppage_balance_example": "Misol: birinchi marta {stars} ⭐ to'lagach \"+{base} ⚡ topup\" va \"+{bonus} ⚡ bonus\" ni ko'rasiz.",
        "helppage_balance_example_one": "Misol: birinchi marta {stars} ⭐ to'lagach \"+{total} ⚡ topup\" ni ko'rasiz.",
        "privacy_feedback_heading": "/feedback yuborsangiz:",
        "privacy_feedback": (
            "Yozganingiz bu botlarni yuritayotgan odamga Telegram xabari sifatida yetib boradi — "
            "qaysi botdan kelgani va tilingiz bilan birga, lekin ismingiz, foydalanuvchi nomingiz "
            "va ID raqamingizsiz. Uni faqat o'sha odam o'qiydi va unga javob yoza olmaydi. "
            "Nusxasi botlar bazasida 30 kun saqlanadi, keyin o'chiriladi."
        ),
        "cancel_item_conversion": "o'girilishi kutilayotgan .{ext} fayli",
        "cancel_button_conversion": "🔄 Konvertatsiyani kutayotgan fayl",
        "start_greeting": "Salom! Menga faylni yuboring va men uni boshqa formatga o'giraman.\n\n",
        "help_text": "Menga fayl yuboring — uni boshqa formatga o'girib beraman: rasm, kamera raw, video, audio, hujjat, jadval, subtitr va arxivlar.\n\nBuyruqlar:\n/convert - konvertatsiyani boshlash (yoki shunchaki fayl yuboring)\n/formats - qo'llab-quvvatlanadigan barcha formatlar\n/balance (yoki /mystars) - ⚡ kreditingiz va u nimaga sarflangani\n/cancel - joriy amalni bekor qilish (bir nechta bo'lsa, qaysi birini so'rayman)\n/feedback - taklif yozish yoki muammo haqida xabar berish; uni faqat botlarni yuritayotgan odam o'qiydi, javob qaytmaydi\n/recharge (yoki /donate) - ⚡ kreditni to'ldirish\n/en, /uz, /rus - tilni almashtirish (yoki /language)\n/paysupport - to'lov bilan muammo\n/privacy - siz haqingizda nimalarni saqlayman\n/terms - botdan foydalanish shartlari\n/deletemydata - siz haqingizdagi hamma narsani o'chirish\n/start - bu bot nima qiladi va qanday boshlash kerak\n/fullhelp - shu to'liq qo'llanma (/help - qisqasi)\n\nNarxlar ⚡ kreditda:\n{pricing}\n\n⚠️ DIQQAT: To'lovlar QAYTARILMAYDI — to'langan Stars qaytarib berilmaydi, ⚡ kreditni esa yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. Konvertatsiya narxi u BOSHLANGANDA yechiladi va faqat bot tomonidagi xatolik tufayli bajarilmasa, ⚡ balansingizga qaytib tushadi.\n\n",
        "convert_start_prompt": "O'girish uchun {max_mb} MB gacha fayl yuboring — rasm, kamera raw, video, audio, hujjat, jadval, subtitr yoki arxiv. Bir nechta rasmni birga yuborsangiz, ularni bitta PDF qilib beraman.\n\nBarcha formatlar: /formats\n\nNarxlar ⚡ kreditda:\n{pricing}\n\nBekor qilish uchun: /cancel",
        "cancelled": "Bekor qilindi.",
        "btn_cancel_conversion": "✖️ Bekor qilish",
        "unknown_extension": "Fayl nomidan formatini aniqlab bo'lmadi — faylni kengaytmasi bilan (masalan, rasm.png) hujjat sifatida yuboring.",
        "file_too_large_download": 'Afsuski, fayl {max_mb} MB dan katta — Telegram botlarga bundan katta fayllarni yuklab olishga ruxsat bermaydi.',
        "file_too_large_in_group": "{limit} MB dan katta fayllarni faqat shaxsiy chatda qabul qila olaman. Bu faylni menga to'g'ridan-to'g'ri yuboring — u yerda {max_mb} MB gacha bo'lishi mumkin.",
        "big_download_unavailable": "Faylingiz yuklab olinmadi: {limit} MB dan katta fayllar menga Telegram bilan alohida ulanish orqali keladi, u hozir javob bermayapti. Bir necha daqiqadan keyin qayta urinib ko'ring yoki {limit} MB dan kichik fayl yuboring.",
        "card_downloading": "📥 Faylingiz yuklab olinmoqda…",
        "card_download_sizes": "{done} / {total}",
        "card_converting": "O'girilmoqda…",
        "card_sending": "Natija yuborilmoqda…",
        "card_elapsed": "{elapsed} o'tdi",
        "card_left": "taxminan {left} qoldi",
        "card_paid": "{price} ⚡ yechildi, balansda {balance} ⚡ qoldi.",
        "card_free": "Bepul konvertatsiya.",
        "download_too_slow": "{minutes} daqiqadan keyin ham to'liq yetib kelmadi",
        "download_stalled": "u kelishdan to'xtab qoldi: {seconds} soniya davomida hech narsa kelmadi",
        "restarting_send_again": '🔄 Bot hozir yangilanmoqda — bir necha soniyadan keyin qaytadan yuboring.',
        "update_soon_try_later": "🔧 Bot tez orada yangilanadi, shuning uchun yangi ishni boshlab bo'lmaydi — taxminan {minutes} daqiqadan keyin qayta urinib ko'ring. Ishga tushgach, o'zim xabar beraman.",
        "update_soon_try_later_soon": "🔧 Bot hozir yangilanmoqda, shuning uchun yangi ishni boshlab bo'lmaydi — birozdan keyin qayta urinib ko'ring. Ishga tushgach, o'zim xabar beraman.",
        "update_will_reset": "🔧 Diqqat: bot yangilanadi va hozir bajarilayotgan ishingiz to'xtab qoladi. Bir necha daqiqadan keyin qaytadan boshlashingiz mumkin.",
        "update_done_try_now": "✅ Yangilanish tugadi — endi qaytadan urinib ko'rishingiz mumkin.",
        "gift_granted": "🎁 Bizda 100 dan ortiq foydalanuvchi bo'ldi, rahmat! Sizga {amount} ⚡ bepul kredit berildi, u {date} gacha amal qiladi. Bu {convert} orqali taxminan {conversions} ta pullik konvertatsiya yoki {download} orqali katta fayllarni yuklab olish uchun yetadi. Balansingiz: /balance",
        "gift_claimed": "🎁 Sovg'a: {amount} ⚡ bepul kredit! Mumu botlarida {users} dan ortiq foydalanuvchi bo'ldi, bu bizning minnatdorchiligimiz.\nU {date} gacha (UTC) amal qiladi va {convert} orqali taxminan {conversions} ta pullik konvertatsiya yoki {download} orqali katta fayllarni yuklab olish uchun yetadi.\nBalansingiz: /balance",
        "download_failed": "Faylni yuklab bo'lmadi: {error}",
        "unsupported_format": '".{ext}" formatini o\'gira olmayman. Qo\'llab-quvvatlanadigan formatlar: /formats',
        "not_for_this_file": "Bu variant bu fayl uchun mavjud emas (albom yoki skanerlangan sahifasi yo'q PDF).",
        "file_too_large_convert": "Fayl hajmi {size} MB — bu {max_mb} MB chegarasidan katta, shuning uchun uni o'girib bo'lmaydi.",
        "no_target_formats": "Bu faylni o'girish mumkin bo'lgan boshqa format yo'q.",
        "category_image": "rasm",
        "category_video": "video",
        "category_audio": "audio",
        "convert_options_prompt": "Qabul qilindi — {size} hajmli {category} fayli. Qaysi formatga o'giray?\n\n{price_line}",
        "price_after_format": "Narx faqat yuborgan faylingizga emas, natijada chiqadigan faylga ham bog'liq. Shuning uchun formatni tanlang — pul yechilishidan oldin narxini aytaman.",
        "quote_line_sizes": "kirish {in_size} · chiqish taxminan {out_size}",
        "quote_free": "Bepul — butun ish {mb} MB dan oshmaydi.",
        "quote_cost": "Narxi: {price} ⚡",
        "quote_fixed": "Bu narx endi o'zgarmaydi. Natija taxmindan kattaroq chiqsa ham, ustiga qo'shimcha olinmaydi.",
        "quote_balance": "Hisobingizda {balance} ⚡ bor.",
        "btn_quote_go": "✅ O'girish · {price} ⚡",
        "btn_quote_free": "✅ O'girish",
        "btn_quote_back": "↩️ Boshqa format",
        "converted_file_caption": 'Mana, faylingiz tayyor!',
        "converted_file_caption_free": 'Mana, faylingiz tayyor! (bepul)',
        "conversion_expired": "Bu konvertatsiyaning vaqti o'tib ketdi — faylni qaytadan yuboring.",
        "convert_invoice_title": '{src} → {target} konvertatsiya',
        "convert_invoice_description": "{size_kb} KB hajmli faylni bir marta o'girish.",
        "convert_invoice_label": "{src}→{target} konvertatsiya",
        "topup_needed": "Bu konvertatsiya {price} ⚡ turadi, balansingizda esa {balance} ⚡.\n\n{stars} ⭐ uchun to'lov hisobi yuborildi: u {credited} ⚡ qo'shadi — bu konvertatsiyaga yetadi, ortgani balansingizda qoladi. Bekor qilish: /cancel",
        "credit_policy_note": "⚠️ DIQQAT: ⚡ konvertatsiya BOSHLANGANDA yechiladi. Uni o'zingiz to'xtatsangiz, ⚡ QAYTARILMAYDI; bot tomonidagi xatolik tufayli bajarilmasa, ⚡ darhol balansingizga qaytib tushadi. To'lovlar qaytarilmaydi — Stars qaytarib berilmaydi.",
        "send_limit_note": "📤 Tayyor fayl {mb} MB gacha bo'lishi mumkin. Bundan katta chiqsa, u yuborilmaydi — bunday holda ⚡ darhol balansingizga qaytib tushadi.",
        "too_many_megapixels": "Bu rasm {mp} megapiksel — xavfsiz o'girish chegarasi {max} MP. Balansdan hech narsa yechilmadi.",
        "too_big_to_send_note": "📐 {formats} formatlari ko'rsatilmadi: {mp} MP rasmda ular bot yubora oladigan {limit} MB dan katta chiqadi. JPG, WEBP yoki AVIF esa yuborsa bo'ladigan hajmda bo'ladi.",
        "raw_too_big": "Bu raw suratning o'lchami {mp} megapiksel. Server raw suratlarni {max} megapikselgacha o'giradi: undan kattasiga yarim o'lchamda ochish uchun ham xotira yetmaydi. Balansdan hech narsa yechilmadi.",
        "raw_half_note": "⚠️ ESLATMA: bu raw surat {mp} MP. U yarim o'lchamda ({out} MP) o'giriladi: to'liq tasvir uchun serverda xotira yetmaydi.",
        "video_fit_hdr_note": "⚠️ ESLATMA: bu video {w}×{h} HDR. Server uni {fw}×{fh} o'lchamda o'giradi — to'liq tasvir uchun serverda xotira yetmaydi. Ranglar saqlanadi.",
        "video_fit_big_note": "⚠️ ESLATMA: bu video {w}×{h}. WEBM bu yerda {fw}×{fh} o'lchamda tayyorlanadi; MP4, MKV va MOV to'liq o'lchamda qoladi.",
        "video_fit_huge_note": "⚠️ ESLATMA: bu video {w}×{h}. Server uni {fw}×{fh} o'lchamda o'giradi — to'liq tasvir uchun serverda xotira yetmaydi.",
        "nothing_sendable": "Bu o'lchamdagi rasm istalgan formatda bot yubora oladigan {limit} MB dan katta chiqadi. Balansdan hech narsa yechilmadi.",
        "too_big_to_send_alert": 'Bu rasm shu formatda bot yubora oladigan {limit} MB dan katta chiqadi.',
        "keep_toggle_off": "📌 Faylni boshqa formatlar uchun saqlash: o'chirilgan",
        "keep_toggle_on": '📌 Faylni boshqa formatlar uchun saqlash: yoqilgan',
        "ocr_lang_toggle": "🔤 Matn tili: {name} (o'zgartirish uchun bosing)",
        "ocr_lang_set": "Endi matn tili: {name}.",
        "ocr_lang_eng": "inglizcha",
        "ocr_lang_rus": "ruscha",
        "ocr_lang_uzb": "o'zbekcha (lotin va kirill)",
        "ocr_too_many_pages_note": "⚠️ ESLATMA: bu PDF ning {max} tadan ortiq sahifasi matnsiz rasm. Bu yerda ko'pi bilan {max} ta shunday sahifadagi matn aniqlanadi, shuning uchun TXT faqat boshqa sahifalarda bor matnni beradi, matnli PDF esa taklif qilinmaydi.",
        "target_ocrpdf": "Matnli PDF",
        "keep_explained_on": "Har bir konvertatsiyadan keyin faylingizni {minutes} daqiqa saqlab turaman — uni boshqa formatlarga ham o'girishingiz mumkin. Ertaroq o'chirish uchun: /cancel",
        "keep_explained_off": "Faylingiz o'girilishi bilanoq o'chiriladi.",
        "storage_full_user": "Fayllaringiz {used} MB joy egallagan, bir kishiga esa {limit} MB gacha ajratiladi. Biror konvertatsiya tugashini kuting yoki /cancel qiling, so'ng qaytadan yuboring.",
        "storage_full_global": 'Hozir vaqtinchalik joy yetishmayapti — bir necha daqiqadan keyin qaytadan yuboring.',
        "cancel_item_job_running": "{size} MB li {src} faylingizni {target} ga o'girish — bajarilmoqda",
        "cancel_item_job_queued": "{size} MB li {src} faylingizni {target} ga o'girish — navbatda, hali hech narsa yechilmagan",
        "cancel_button_job": "⏹ {target} ga o'girish",
        "queue_full": 'Sizda {count} ta konvertatsiya navbatda yoki bajarilmoqda. Ulardan biri tugagach, yana yuboring.',
        "job_queued": '⏳ {src} → {target} navbatda, oldinda {ahead} ta. Boshlanmaguncha hech narsa yechilmaydi.',
        "job_starting": '⏳ {src} → {target}: boshlanmoqda…',
        "job_not_running": 'Bu konvertatsiya allaqachon tugagan.',
        "payment_kept_as_credit": "To'lov qabul qilindi — +{credited} ⚡, balansingiz {balance} ⚡. Lekin faylning saqlanish muddati o'tib ketgani uchun u o'girilmadi: faylni qaytadan yuboring — narxi balansdan yechiladi.",
        "job_progress": "⏳ Konvertatsiya davom etmoqda — {elapsed} daqiqa o'tdi. {next} daqiqadan keyin yana xabar beraman, ko'pi bilan {last} daqiqada esa natijasini bilasiz.",
        "job_progress_last": "⏳ Konvertatsiya hali davom etmoqda — {elapsed} daqiqa o'tdi. Oxirgi xabar {last} daqiqadan keyin keladi: unda konvertatsiya yo tugagan, yo to'xtatilib, ⚡ balansingizga qaytgan bo'ladi.",
        "job_stop_button": "⏹ To'xtatish",
        "job_stop_button_queued": '✖ Navbatdan olib tashlash',
        "job_done": '✅ {src} → {target} tayyor.',
        "balance_left_line": 'Balansingizda {balance} ⚡ qoldi.',
        "job_user_stopped": "⏹ To'xtatildi — boshlanishida yechilgan {price} ⚡ qaytarilmaydi.",
        "job_user_stopped_free": "⏹ To'xtatildi.",
        "job_removed_from_queue": '✖ Navbatdan olib tashlandi — hech narsa yechilmadi.',
        "job_no_credit": "Konvertatsiya boshlanmadi: narxi {price} ⚡, balansingizda esa {balance} ⚡. Hech narsa yechilmadi — balansni /recharge orqali to'ldiring.",
        "job_refunded_line": '{price} ⚡ balansingizga qaytib tushdi — hozir {balance} ⚡.',
        "job_reason_refused": "Faylni o'girib bo'lmadi: {detail}",
        "job_reason_timeout": "Konvertatsiya {minutes} daqiqalik vaqt chegarasidan oshib ketgani uchun to'xtatildi. Bot egasiga xabar berildi.",
        "job_reason_too_slow": "Bu faylni o'girish taxminan {minutes} daqiqa olardi, bu yerda esa bitta konvertatsiya ko'pi bilan {limit} daqiqa ishlaydi. Shuning uchun chegaragacha kuttirib o'tirmay, hozir to'xtatdim. Qisqaroq video yoki pastroq sifat bemalol sig'adi.",
        "job_reason_crash": 'Konvertatsiya bot tomonidagi xatolik tufayli bajarilmadi. Bot egasiga xabar berildi.',
        "job_reason_no_memory": "Bu faylni o'girishga serverning xotirasi yetmadi — 4K va HDR videolarda shunday bo'ladi. Faylingizda muammo yo'q: uning 1080p yoki undan past sifatdagi nusxasi bemalol o'giriladi.",
        "job_reason_too_large": "{target} fayli {mb} MB chiqdi, bu konvertatsiya natijasini esa faqat {limit} MB gacha yubora olaman. JPG, WEBP yoki AVIF ancha kichik bo'ladi. Bot egasiga xabar berildi.",
        "job_reason_send_failed": "Fayl o'girildi, lekin uni sizga yuborib bo'lmadi. Bot egasiga xabar berildi.",
        "job_reason_interrupted": 'Konvertatsiya vaqtida bot qayta ishga tushdi. Bir daqiqadan keyin faylni qaytadan yuboring.',
        "unknown_order": "Noma'lum buyurtma.",
        "unrecognized_message": 'Bu o\'girish mumkin bo\'lgan fayl emas — fayl yuboring yoki qabul qilinadigan formatlarni /formats orqali ko\'ring. Format nomini yozsangiz (masalan, "heic" yoki "epub"), uni nimaga o\'girish mumkinligini aytaman.',
        "unknown_command": "Bunday buyruq yo'q. Bot nimalar qila olishini bilish uchun /start yuboring.",
        # ---- /formats ----
        "category_document": "hujjat",
        "category_data": "ma'lumot",
        "category_subtitle": "subtitr",
        "category_archive": "arxiv",
        "formats_title": "📁 Qaysi formatlarni o'giraman",
        "formats_intro": "{sources} ta formatdan {targets} ta formatga — jami {pairs} xil yo'nalish. Fayl yuborsangiz, uni nimaga o'girish mumkinligini ko'rsataman; to'liq ro'yxat uchun quyidan turkumni tanlang.",
        "formats_highlights": "Ko'pchilik bilmaydigan imkoniyatlar:\n• iPhone rasmi (HEIC) → JPG yoki PNG\n• Istalgan rasm — yoki butun albom — bitta PDF\n• PDF yoki EPUB → PNG, JPG yoki oddiy matn\n• Word hujjati → PDF, HTML yoki Markdown\n• CSV, JSON yoki XML → XLSX jadval\n• Kamera raw (CR3, NEF, ARW, DNG…) → JPG, PNG yoki 16 bitli TIFF\n• Telefon ocha olmaydigan RAR yoki 7z → ZIP\n• Subtitrlar: SRT, VTT va ASS bir-biriga, yoki faqat matn",
        "formats_reads": "O'qiydi",
        "formats_writes": "Yozadi",
        "formats_category_title": "📁 {category} fayllari",
        "formats_lookup": ".{ext} — {category} fayli. Uni quyidagi formatlarga o'girish mumkin: {targets}",
        "formats_lookup_none": ".{ext} faylini o'qiy olaman, lekin bu serverda uni boshqa formatga o'girib bo'lmaydi.",
        "formats_lookup_unknown": '".{ext}" formati notanish — barcha formatlar: /formats',
        "btn_more_formats": "⋯ Yana formatlar",
        "btn_fewer_formats": "‹ Kamroq",
        "btn_what_formats": '📁 Qaysi formatlar bor?',
        "btn_formats_back": "‹ Barcha turkumlar",
        "convert_options_prompt_batch": "Qabul qilindi — {count} ta fayl, jami {size}. Qaysi formatga o'giray?\n\n{price_line}",
        "converted_file_caption_zip": "Mana — {items} ta fayl, zip ichida.",
        "converted_file_caption_pdf": "Mana — {items} ta rasm, har biri alohida sahifada.",
        "converted_file_caption_pages": "Mana — {items} ta sahifa, zip ichida.",
        "converted_docx_note": "Matn haqiqiy matn — tahrirlash mumkin. PDF'da boshqalardan kattaroq yozilgan qatorlar sarlavha bo'ldi, qalin yozilgani qalinligicha qoldi, rasmlar esa o'z tartibida turibdi. Sahifa ko'rinishi ko'chirilmadi: ustunlar ketma-ket chiqadi, jadval o'z matniga aylanadi, sahifa chegaralari esa PDF'dagicha qoladi.",
        "converted_layout_note": "Sarlavhalar, ajratilgan matn, ro'yxat va jadvallar saqlandi. Sahifa ko'rinishi asl nusxadagidek emas, qaytadan joylashtirilgan — bu serverda Word yo'q.",
        "batch_mixed_formats": 'Fayllarning formati har xil, shuning uchun ularni birma-bir yuboring — yoki faqat rasmlar yuboring, ularni bitta PDF qilib beraman.',
        "price_free": "Bepul",
        "price_not_supported": "qo'llab-quvvatlanmaydi",
        "price_cloud_api_note": '(Telegram botlar uchun 20 MB yuklab olish chegarasi)',
        "price_from": "{price} ⚡ dan: ko'pchilik rasm, qo'shiq va hujjatlar",
        "price_example": "{mb} MB video → MP4: {price} ⚡",
        "price_at_most": "eng katta fayl, {mb} MB: ko'pi bilan {price} ⚡",
        "price_quoted_first": "Aniq narx pul yechilishidan oldin ko'rsatiladi — to'laydiganingiz aynan shu.",
    },
    "ru": {
        "animation_too_long_note": "🎞 {formats} не предлагаются: видео длиннее {seconds} секунд, из него не успеть сделать анимацию. Обрежьте его или выберите видеоформат.",
        "animation_too_long_alert": "Это видео длиннее {seconds} секунд, из него не успеть сделать анимацию. Ничего не списано. Обрежьте его или выберите видеоформат.",
        "animation_too_many_frames": "В этой анимации {frames} кадров, а я могу преобразовать не больше {max}. Ничего не списано. Попробуйте покороче.",
        "transfer_waiting": "⏳ Сейчас передаётся другой большой файл. Ваш начнётся сразу после него — ничего делать не нужно.",
        "flood_wait": "Вы отправляете быстрее, чем я успеваю — подождите примерно {seconds} секунд(ы) и продолжайте.",
        "sibling_blurb": "Тоже часть этой семьи ботов, смотрите ниже \U0001f447",
        "donation_nudge": '⚡ Часто конвертируете большие файлы? /recharge пополнит кредит заранее.',
        "donate_unknown_currency": 'Неизвестная валюта "{currency}" — попробуйте xtr или usd.',
        "donate_currency_not_configured": 'Оплата в {currency} на этом боте пока не настроена — используйте Stars.',
        "donate_invalid_amount": 'Это некорректная сумма — попробуйте, например, /recharge 500.',
        "donate_prompt": 'Пополните ⚡ кредит для конвертаций. Выберите сумму ниже или нажмите «Другое», чтобы ввести свою (можно и сразу отправить /recharge <число>).',
        "donate_custom_button": "✏️ Другое {symbol}",
        "donate_too_many_stars": 'Это очень много звёзд! Пусть будет меньше {max} ⭐ за одно пополнение.',
        "donate_out_of_range": "Пожертвования в {currency} должны быть в диапазоне от {lo} до {hi} {symbol}.",
        "donate_invoice_title": 'Пополнение ⚡ кредита',
        "donate_invoice_description": 'Добавляет {credited} ⚡ на ваш баланс для конвертаций.',
        "donate_invoice_label": 'Пополнение ⚡ кредита',
        "donate_invoice_description_fiat": 'Разовое добровольное пожертвование на хостинг. Спасибо!',
        "donate_prompt_credit": 'Оплаченные Stars превращаются в ⚡ кредит на конвертации. Следующие {left} ⭐ дают по {each} ⚡ ({mult}×): {rate} ⚡ обычного кредита и бонус, который сгорает через {days} дней после оплаты. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.',
        "donate_prompt_credit_base": 'Каждая оплаченная ⭐ превращается в {rate} ⚡ кредита на конвертации. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.',
        "donate_invoice_error": "⚠️ Telegram не смог создать этот счёт: {error}",
        "stars_unit": "Stars (звёзды)",
        "donate_custom_ask": 'На сколько {unit} хотите пополнить? Ответьте числом.',
        "donate_invalid_amount_retry": 'Это некорректная сумма — отправьте /recharge, чтобы попробовать снова.',
        "donate_thanks": "🙏 Спасибо за {amount} ⭐ — это по-настоящему ценно!",
        "topup_thanks": '⚡ Баланс пополнен — {stars} ⭐ получено. Спасибо!\n\nНачислено на баланс: +{credited} ⚡\nВаш баланс: {balance} ⚡',
        "topup_thanks_bonus": '🎁 И ещё {bonus} ⚡ бонусного кредита — он сгорит {date}.',
        "credit_cannot_be_withdrawn": '⚡ — это кредит на конвертации в ConvertBot, его НЕЛЬЗЯ вывести или обменять обратно на Stars. Проблема с платежом? /paysupport',
        "topup_refunded": "⚠️ Ваш платёж на {stars} ⭐ прошёл, но из-за проблемы на моей стороне ⚡ не поступили на баланс, поэтому я вернул платёж: {stars} ⭐ снова на вашем счёте. Пожалуйста, попробуйте ещё раз через несколько минут.",
        "topup_failed": "⚠️ Ваш платёж на {stars} ⭐ прошёл, но из-за проблемы на моей стороне ⚡ не поступили на баланс. Ничего не потеряно: владелец уже знает и начислит их вручную. Уточнить: /paysupport",
        "paysupport_text": '💳 Помощь с платежом\n\nПлатежи ОКОНЧАТЕЛЬНЫЕ: Stars НЕ возвращаются, а ⚡ кредит НЕЛЬЗЯ вывести или обменять обратно на Stars. Единственное исключение: платёж, который бот не смог зачислить как кредит, возвращается вам автоматически и полностью.\n\nЕсли с платежом что-то пошло не так — деньги списали, а кредит не пришёл, или списали дважды, — напишите на {contact}: дату, сумму и ваш Telegram ID, {user_id}. Это проверят и исправят кредитом.\n\n/balance показывает каждый платёж и что он добавил.',
        "report_button": '🐞 Добавить мои данные',
        "problem_logged_note": 'Эта проблема уже записана для владельца бота — код, время и версия. О вас там ничего нет.',
        "report_disclaimer": '📨 Добавить к этой проблеме ваши данные?\n\nУже записано, без вас: название бота, код ошибки {code}, номер случая {incident}, когда это произошло и версия бота.\n\nКнопка «Отправить» добавит четыре вещи: ваш Telegram ID, ваш @username, если он есть, выбранный вами язык и то, личный это чат или группа. Ни того, что вы написали, ни отправленного файла.\n\nТогда ваш случай можно будет найти в логах — обычно именно это и позволяет починить редкую проблему. Решать только вам.',
        "report_send": '📨 Отправить мои данные',
        "report_cancel": '✖️ Отмена',
        "report_sent": '✅ Спасибо — ваши данные теперь на случае {incident}, так его легко найти.',
        "report_already": 'Ваши данные уже на случае {incident}. Больше ничего не отправлено.',
        "report_cancelled": 'Отменено — о вас ничего не отправлено. Сама проблема остаётся записанной.',
        "report_failed": '⚠️ Сейчас не удалось отправить сообщение. Попробуйте позже.',
        "report_invalid": 'Эта кнопка больше не работает.',
        "crash_notice": '⚠️ При обработке произошла ошибка на стороне бота, поэтому ничего не сделано. Попробуйте ещё раз чуть позже.',
        "sandbox_notice": "🧪 Тестовый режим — настоящие Stars не списывались.",
        "balance_header": "⚡ Ваш баланс: {balance}",
        "balance_totals": "Всего оплачено {paid} ⭐ · начислено {credited} ⚡ · потрачено {spent} ⚡",
        "balance_rate": 'Следующие {left} ⭐ дают по {each} ⚡ ({mult}×).',
        "balance_rate_base": '1 ⭐ даёт {rate} ⚡.',
        "balance_bonus_line": 'Из них {bonus} ⚡ — бонусный кредит; {soon} ⚡ сгорит {date}.',
        "balance_recent": "Последние операции:",
        "balance_empty_hint": '/recharge пополнит кредит в любой момент.',
        "bot_short_description": (
            "Конвертирует картинки, raw-фото, видео, аудио, документы, таблицы, субтитры и архивы "
            "— около 90 форматов."
        ),
        "bot_description": 'Пришлите файл и выберите, во что его превратить. Картинки, raw-фото, видео, аудио, документы, таблицы, субтитры и архивы — около 90 форматов на вход, 50 на выход.\n\nHEIC с iPhone становится JPG. Набор картинок — одним PDF. PDF — картинками или текстом. Весь список — /formats\n\nВсё меньше 3 МБ — бесплатно. Дороже — цена по размеру файла в ⚡ кредите, купленном за Stars, и видна до выбора. Если конвертация сорвётся по вине бота, кредит вернётся.\n\nАнглийский, узбекский и русский. /privacy — что бот о вас хранит.',
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Конфиденциальность",
        "privacy_kept_heading": "Что бот хранит:",
        "privacy_stored": '• ваш числовой id в Telegram и выбранный язык\n• присланный файл — удаляется сразу после конвертации, или хранится до 15 минут после каждой конвертации, если включить «хранить файл», чтобы конвертировать его и в другие форматы; всё, что осталось после сбоя, подчищается по таймеру и при запуске\n• отметку времени на каждое обращение к боту — чтобы владелец видел, пользуется ли ботом хоть кто-нибудь\n• сколько раз нажимают каждую кнопку — общий счёт за день по всему боту, без сведений о том, кто нажимал\n• ваш баланс ⚡ кредита и запись о каждом пополнении, списании за конвертацию и возврате ⚡ на баланс: сумму, за что и платёжный id Telegram для оплаченного\n• то, что бот в этот момент для вас делает — пока не закончит',
        "privacy_problems_heading": "Когда что-то ломается:",
        "privacy_problems": "Каждую ошибку, которую бот кому-то показывает, он записывает сам: код ошибки, номер случая, когда это произошло и версию бота. О вас там нет ничего — ни вашего id, ни имени, ни того, что вы отправили. Сбой, о котором никто не сообщил, остаётся сбоем, поэтому бот не ждёт, пока его попросят.\n\nКнопка «Добавить мои данные» под ошибкой предлагает привязать к этому случаю четыре вещи: ваш Telegram id, ваш @username, если он есть, выбранный вами язык и то, личный это чат или группа. Все четыре сначала названы на экране, и ничего не отправляется, пока вы не нажмёте «Отправить». Того, что вы написали, и отправленных файлов там не бывает никогда.\n\nЭти четыре стираются сами через 30 дней, а /deletemydata стирает их сразу. Запись об ошибке остаётся — она действительно произошла — просто перестаёт говорить, кто на неё наткнулся. Сами записи удаляются через 180 дней.",
        "privacy_seen_by_heading": "Кто ещё это видит:",
        "privacy_seen_by": (
            "• Telegram — он передаёт каждое сообщение в обе стороны и действует по своим "
            "правилам\n"
            "• хостинг, на котором работает бот, и база данных, в которую он пишет"
        ),
        "privacy_others": (
            "• и больше никто, кроме них — файлы конвертируются на той же машине, где работает бот, и никуда "
            "наружу не передаются"
        ),
        "privacy_kept_for_heading": "Сколько это хранится:",
        "privacy_kept_for": 'Настройки и всё, что бот держит для вас, остаются, пока вы их не сотрёте или не перестанете пользоваться ботом. Отметки об использовании удаляются примерно через три месяца. Записи о платежах и баланс ⚡ хранятся дольше — по ним разбираются споры о платежах.\n\nНичего из этого не продаётся, не сдаётся в аренду и не используется для рекламы, и никому кроме перечисленных выше не передаётся.',
        "privacy_your_choices": (
            "Что можно сделать:\n"
            "/deletemydata — стереть всё, что бот хранит о вас\n"
            "/terms — для чего ботом можно пользоваться\n"
            "\n"
            "Блокировка бота в Telegram остановит его сообщения, но ничего не сотрёт — если "
            "нужно и то и другое, сначала отправьте /deletemydata."
        ),
        "terms_heading": "📜 Условия",
        "terms_use": (
            "Пользуйтесь ботом по назначению, в рамках закона и правил самого Telegram. Не "
            "используйте его, чтобы кого-то донимать, и не нагружайте сверх заданных лимитов — за то "
            "и другое аккаунт блокируется."
        ),
        "terms_specific": 'Файлы и оплата: конвертируйте то, что имеете право конвертировать. Всё меньше 3 МБ бесплатно; дороже — цена в ⚡ кредите, который покупается за Telegram Stars, и показывается до выбора формата. ⚡ списывается, когда конвертация НАЧИНАЕТСЯ. Если остановите идущую конвертацию сами, ⚡ НЕ вернётся; если сбой на стороне бота — ⚡ автоматически вернётся на баланс. Платежи ОКОНЧАТЕЛЬНЫЕ: Stars НЕ возвращаются, а кредит НЕЛЬЗЯ вывести или обменять обратно на Stars. Единственное исключение: платёж, который бот не смог зачислить как кредит, возвращается автоматически и полностью.',
        "terms_money": 'О деньгах: /recharge покупает ⚡ кредит на конвертации: 2 ⚡ за ⭐, а за ваши первые 500 Stars — 6 и за следующие 500 — 4. Всё сверх 2 — бонусный кредит, он сгорает через 90 дней после платежа. Платежи ОКОНЧАТЕЛЬНЫЕ: Stars НЕ возвращаются, а кредит НЕЛЬЗЯ вывести или обменять обратно на Stars. Единственное исключение: платёж, который бот не смог зачислить как кредит, возвращается автоматически и полностью. Все платежи проводит Telegram, бот никогда не видит номер карты. Проблема с платежом — /paysupport.',
        "terms_no_warranty": (
            "Без обещаний: бота ведёт один человек, он бесплатный и может тормозить, ошибаться "
            "или вовсе не работать без предупреждения. Держите свою копию всего, что вам важно."
        ),
        "policy_full_text": "Полный текст: {url}",
        "policy_contact": "Вопросы, жалобы или запрос по данным: {contact}",
        "delete_data_confirm": "⚠️ Это сотрёт всё, что бот хранит о вас. Отменить будет нельзя.",
        "delete_data_consequences": 'Язык, место в напоминании о пополнении и все отметки о том, когда вы пользовались ботом, будут стёрты. Файлы тут ни при чём: то, что вы конвертировали, удалилось сразу после конвертации.\n\nОплаты останутся, без username, потому что по ним разбираются споры о платежах, — и баланс ⚡ тоже: /balance по-прежнему его покажет.',
        "delete_data_button_yes": "🗑 Стереть",
        "delete_data_button_no": "↩️ Оставить мои данные",
        "delete_data_kept": "Ничего не стёрто.",
        "delete_data_done": (
            "🗑 Готово — стёрто записей: {rows}.\n"
            "\n"
            "Отправьте /start когда захотите; бот примет вас как нового."
        ),
        "delete_data_failed": (
            "Сейчас стереть не получилось — что-то сломалось на моей стороне. Попробуйте ещё раз "
            "через несколько минут."
        ),
        "language_set_confirmation": "✅ Язык изменён на русский.",
        "cancel_header": "\u274c \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e:",
        "cancel_nothing": "\u041e\u0442\u043c\u0435\u043d\u044f\u0442\u044c \u043d\u0435\u0447\u0435\u0433\u043e — \u044f \u043d\u0438\u0447\u0435\u0433\u043e \u043e\u0442 \u0432\u0430\u0441 \u043d\u0435 \u0436\u0434\u0430\u043b.",
        "cancel_ask": "Что остановить? Вот что я жду:",
        "cancel_kept": "Хорошо — ничего не отменено.",
        "cancel_reply_box_freed": "Поле ответа снова свободно.",
        "cancel_button_all": "❌ Всё",
        "cancel_button_none": "↩️ Ничего, продолжаем",
        "cancel_button_donation": '💸 Сумма пополнения',
        "cancel_item_donation": 'сумма пополнения, которую я запросил',
        "cancel_item_stale_prompt": "старый запрос, который всё ещё ждал ответа",
        "feedback_ask": (
            "💬 Что вы хотите сказать тому, кто ведёт этих ботов? Идея, проблема, что угодно — "
            "напишите одним сообщением в ответ на это.\n"
            "\n"
            "Его прочитает только он, и ответить вам он не сможет: сообщение уходит без вашего "
            "имени и id. Остановить: /cancel"
        ),
        "feedback_placeholder": "Ваш отзыв",
        "feedback_text_only": "Сюда нужен текст — напишите отзыв словами в ответ на это сообщение или отправьте /cancel.",
        "feedback_thanks": "✅ Спасибо — отправлено. Это прочитает только тот, кто ведёт этих ботов, и ответить он не сможет: в сообщении нет ничего о том, кто вы.",
        "feedback_limit": "Через /feedback сегодня пришло уже {per_day} ваших сообщений — больше за день я не принимаю. Спасибо, напишите завтра.",
        "feedback_failed": "Отзыв не отправлен — что-то сломалось на моей стороне. Попробуйте ещё раз через несколько минут.",
        "cancel_button_feedback": "💬 Отзыв",
        "cancel_item_feedback": "отзыв, который я просил написать",
        "help_brief": (
            "Как это устроено:\n"
            "\n"
            "• Пришлите файл — картинку, raw-фото, видео, аудио, документ, таблицу, субтитры или архив — и выберите, во что его превратить. Все форматы: /formats\n"
            "• 📝 Текст с фото или скана PDF: отправьте файл и выберите TXT или PDF с текстом.\n"
            "• Конвертация стоит ⚡ кредит, и цена видна до того, как что-то списано. Пополнить — /recharge; /balance покажет остаток и на что он ушёл.\n"
            "• Идеи или проблемы: /feedback — это прочитает только тот, кто ведёт этих ботов, ответа не будет.\n"
            "\n"
            "Основные команды: /convert · /formats · /balance · /recharge · /cancel\n"
            "Все команды и цены — в полном руководстве: кнопка ниже или /fullhelp.\n"
            "\n"
        ),
        "help_full_button": "📖 Полное руководство",
        "help_brief_button": "↩️ Кратко",
        "help_page_everything": "Полное руководство: /fullhelp",
        "help_page_gone": "Этой страницы справки больше нет. Отправьте /help, чтобы увидеть актуальные.",
        "price_free_upto": "0–{mb} МБ: ",
        "price_over": "больше {mb} МБ: ",
        "helppage_convert": (
            "🔄 /convert — перевести файл в другой формат\n"
            "\n"
            "1. Пришлите файл — или сначала /convert, потом файл. До {max_mb} МБ.\n"
            "2. Я покажу, во что его можно перевести. Нажмите нужный формат; «⋯ Ещё форматы» покажет остальные.\n"
            "3. Бесплатная конвертация начинается сразу. Иначе вы сначала увидите цену, и запустит её только «✅ Конвертировать» — до этого ничего не списывается.\n"
            "\n"
            "Цена в ⚡ кредите:\n"
            "{pricing}\n"
            "\n"
            "• Бесплатно: присланный файл и готовый вместе меньше {free_mb} МБ.\n"
            "• ⚡ списывается, когда конвертация начинается, а не пока она ждёт очереди. Если я её не выполню (откажу, не уложусь во время или не смогу отправить результат), ⚡ вернётся на баланс. Если остановите её сами — не вернётся.\n"
            "• На экране с ценой сказано, каким большим может быть результат. Если он получится больше, его не отправят, а ⚡ вернётся.\n"
            "• На альбом картинок — одно меню на все (до {batch} файлов). PDF кладёт по картинке на страницу; остальные форматы приходят ZIP-архивом.\n"
            "• 📝 Текст с фото или скана PDF: выберите TXT (только текст) или «PDF с текстом» (те же страницы, текст можно выделять и искать). Для картинки оба варианта — в «⋯ Ещё форматы». PDF с текстом делается максимум из {ocr_pages} отсканированных страниц. «🔤 Язык текста» переключает английский, русский и узбекский. Распознавание бесплатно, если страниц не больше {ocr_free_pages} и файлы меньше {free_mb} МБ.\n"
            "• «📌 Хранить файл для других форматов» хранит файл {minutes} мин после каждой конвертации — можно перевести его ещё раз, не присылая заново. Сначала выключено: файл удаляется сразу.\n"
            "• Выбора формата я жду {minutes} мин.\n"
            "\n"
            "Пример: пришлите otchet.docx и нажмите PDF. Небольшой документ переведётся сразу и бесплатно. Для большего сначала будет цена — от {least} ⚡ — и запустит его «✅ Конвертировать»."
        ),
        "helppage_recharge": (
            "⭐ /recharge — пополнить ⚡ кредит\n"
            "\n"
            "Конвертации оплачиваются ⚡ кредитом, а ⚡ покупается за Telegram Stars (⭐).\n"
            "1. Отправьте /recharge и нажмите сумму ({options} ⭐) или «✏️ Другое ⭐». Или сразу: /recharge 200.\n"
            "2. Telegram попросит оплатить. ⚡ появится на балансе, как только придёт оплата.\n"
            "\n"
            "Сколько ⚡ даёт одна ⭐, зависит от того, сколько Stars вы заплатили всего, за всё время, в любом из наших ботов:\n"
            "{ladder}\n"
            "{bonus_rule}\n"
            "• Сначала тратится бонусный кредит, начиная с того, что сгорает раньше, — поэтому сгореть может только неиспользованный бонус. /balance покажет, сколько у вас бонуса и когда он сгорает.\n"
            "• Платежи окончательны: Stars не возвращаются, а ⚡ нельзя вывести или обратно превратить в Stars.\n"
            "• /donate — та же команда.\n"
            "\n"
            "{example}"
        ),
        "helppage_recharge_step_first": "  • первые {stars} ⭐: по {each} ⚡{split}",
        "helppage_recharge_step_next": "  • следующие {stars} ⭐: по {each} ⚡{split}",
        "helppage_recharge_step_after": "  • дальше: по {rate} ⚡",
        "helppage_recharge_flat": "  • {rate} ⚡ за каждую ⭐",
        "helppage_recharge_bonus_part": " ({rate} + {bonus} бонус)",
        "helppage_recharge_bonus_rule": "{rate} ⚡ за каждую ⭐ не сгорают никогда. Бонус сгорает через {days} дн. после оплаты.",
        "helppage_recharge_example": "Пример: первая оплата {stars} ⭐ даёт {total} ⚡: {base} ⚡, которые не сгорают, и {bonus} ⚡ бонуса.",
        "helppage_recharge_example_flat": "Пример: {stars} ⭐ дают {total} ⚡.",
        "helppage_balance": (
            "⚡ /balance — ваш кредит и куда он ушёл\n"
            "\n"
            "/balance показывает:\n"
            "• баланс и итоги: сколько Stars вы заплатили, сколько ⚡ они дали, сколько ⚡ потрачено;\n"
            "• сколько из баланса — бонус, сколько из него сгорает первым и в какой день;\n"
            "• сколько ⚡ дадут ваши следующие Stars;\n"
            "• последние {rows} операций, у каждой дата и одно слово:\n"
            "  topup — ⚡ за оплаченные Stars\n"
            "  bonus — дополнительные ⚡ от пополнения; сгорают через {days} дн. после оплаты\n"
            "  grant — ⚡, которые вам дали: подарок (бонус, сгорает в конце указанного в нём дня) или кредит от того, кто ведёт этих ботов (не сгорает)\n"
            "  spend — конвертация\n"
            "  returned — ⚡ конвертации вернулись, потому что я её не выполнил\n"
            "  adjustment — исправление от того, кто ведёт этих ботов\n"
            "  expired — бонус, сгоревший неиспользованным\n"
            "• Бонус тратится раньше остального, начиная с того, что сгорает раньше.\n"
            "• Кошелёк один: баланс одинаковый во всех наших ботах.\n"
            "\n"
            "{example}"
        ),
        "helppage_balance_example": "Пример: после первой оплаты {stars} ⭐ вы увидите «+{base} ⚡ topup» и «+{bonus} ⚡ bonus».",
        "helppage_balance_example_one": "Пример: после первой оплаты {stars} ⭐ вы увидите «+{total} ⚡ topup».",
        "privacy_feedback_heading": "Если вы отправляете /feedback:",
        "privacy_feedback": (
            "Написанное уходит тому, кто ведёт этих ботов, сообщением в Telegram — вместе с "
            "названием бота и вашим языком, но без имени, username и id. Копия хранится в базе "
            "ботов 30 дней, потом удаляется. Читает его только он, и ответить на него он не может."
        ),
        "cancel_item_conversion": "\u0444\u0430\u0439\u043b .{ext}, \u043a\u043e\u0442\u043e\u0440\u044b\u0439 \u0432\u044b \u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0438\u0440\u043e\u0432\u0430\u043b\u0438",
        "cancel_button_conversion": "🔄 Файл, ждущий конвертации",
        "start_greeting": "Здравствуйте! Пришлите мне файл, и я преобразую его в другой формат.\n\n",
        "help_text": 'Пришлите мне файл, и я преобразую его в другой формат — картинки, raw-фото, видео, аудио, документы, таблицы, субтитры и архивы.\n\nКоманды:\n/convert - начать конвертацию (или просто отправьте файл напрямую)\n/formats - все форматы, которые я читаю и пишу\n/balance (или /mystars) - ваш ⚡ кредит и на что он потрачен\n/cancel - остановить то, чего я от вас жду (спрошу, что именно)\n/feedback - предложить идею или сообщить о проблеме; это прочитает только тот, кто ведёт этих ботов, ответа не будет\n/recharge (или /donate) - пополнить ⚡ кредит\n/en, /uz, /rus - сменить язык (или /language — он спрашивает)\n/paysupport - проблема с оплатой\n/privacy - что я о вас храню\n/terms - для чего можно пользоваться ботом\n/deletemydata - удалить всё, что я о вас храню\n/start - что этот бот делает и с чего начать\n/fullhelp - это полное руководство (/help - краткое)\n\nЦена в ⚡ кредите:\n{pricing}\n\n⚠️ ВНИМАНИЕ: платежи окончательные — Stars НЕ возвращаются, а ⚡ кредит НЕЛЬЗЯ вывести. ⚡ за конвертацию списывается при СТАРТЕ и возвращается на баланс, только если сбой по вине бота.\n\n',
        "convert_start_prompt": (
            "Пришлите файл для конвертации, до {max_mb} МБ — картинку, видео, звук, "
            "документ, таблицу или файл субтитров. Пришлите несколько картинок "
            "сразу — соберу их в один PDF.\n\n"
            "/formats покажет все форматы, которые я знаю.\n\n"
            "Цена в ⚡ кредите:\n{pricing}\n\n"
            "/cancel, чтобы остановиться."
        ),
        "cancelled": "Отменено.",
        "btn_cancel_conversion": "✖️ Отмена",
        "unknown_extension": (
            "Не удалось определить формат файла по названию — попробуйте отправить "
            "его как документ с обычным расширением (например, photo.png)."
        ),
        "file_too_large_download": "Этот файл больше {max_mb} МБ — боты Telegram не могут скачать ничего крупнее, увы.",
        "file_too_large_in_group": "Файлы больше {limit} МБ я принимаю только в личном чате. Пришлите этот файл мне напрямую — там можно до {max_mb} МБ.",
        "big_download_unavailable": "Файл не скачан: файлы больше {limit} МБ приходят ко мне через отдельное подключение к Telegram, а оно сейчас не отвечает. Попробуйте через несколько минут или пришлите файл меньше {limit} МБ.",
        "card_downloading": "📥 Скачиваю ваш файл…",
        "card_download_sizes": "{done} из {total}",
        "card_converting": "Конвертирую…",
        "card_sending": "Отправляю результат…",
        "card_elapsed": "прошло {elapsed}",
        "card_left": "осталось около {left}",
        "card_paid": "Списано {price} ⚡, на балансе {balance} ⚡.",
        "card_free": "Бесплатная конвертация.",
        "download_too_slow": "он всё ещё не пришёл целиком через {minutes} мин.",
        "download_stalled": "он перестал приходить: {seconds} сек. не пришло ни байта",
        "restarting_send_again": "🔄 Сейчас обновляюсь — подождите несколько секунд и отправьте ещё раз.",
        "update_soon_try_later": '🔧 Сейчас меня обновляют, поэтому я не могу начать ничего нового — попробуйте снова примерно через {minutes} мин. Я напишу, когда вернусь.',
        "update_soon_try_later_soon": '🔧 Сейчас меня обновляют, поэтому я не могу начать ничего нового — попробуйте снова чуть позже. Я напишу, когда вернусь.',
        "update_will_reset": '🔧 Внимание: меня скоро обновят, и то, что вы сейчас начали, будет сброшено. Через несколько минут сможете начать заново.',
        "update_done_try_now": '✅ Обновление завершено — можете пробовать снова.',
        "gift_granted": "🎁 Нас уже больше 100 — спасибо! Вам начислено {amount} ⚡ бесплатного кредита, он действует до {date}. Этого хватит примерно на {conversions} платных конвертаций в {convert} или на скачивание больших файлов в {download}. Ваш баланс: /balance",
        "gift_claimed": "🎁 Подарок: {amount} ⚡ бесплатного кредита! У Mumu ботов уже больше {users} пользователей — это наше спасибо.\nОн действует до {date} (UTC). Этого хватит на платные конвертации (около {conversions}) в {convert} или на скачивание больших файлов в {download}.\nВаш баланс: /balance",
        "download_failed": "Не удалось скачать этот файл: {error}",
        "unsupported_format": "«.{ext}» — не тот формат, который я конвертирую — /formats покажет те, что умею.",
        "not_for_this_file": "Для этого файла такой вариант недоступен (альбом или PDF без отсканированных страниц).",
        "file_too_large_convert": 'Этот файл весит {size} МБ, что больше лимита в {max_mb} МБ — конвертировать его нельзя.',
        "no_target_formats": "Для этого файла нет доступного другого формата.",
        "category_image": "изображение",
        "category_video": "видео",
        "category_audio": "аудио",
        "convert_options_prompt": "Принято — файл {category} на {size}. Во что конвертировать:\n\n{price_line}",
        "price_after_format": "Цена зависит не только от того, что вы прислали, но и от того, что получится. Выберите формат — я назову цену до того, как что-то спишется.",
        "quote_line_sizes": "на входе {in_size} · на выходе примерно {out_size}",
        "quote_free": "Бесплатно — вся работа укладывается в {mb} МБ.",
        "quote_cost": "Стоимость: {price} ⚡",
        "quote_fixed": "Эта цена уже зафиксирована. Если результат окажется больше оценки, доплачивать не придётся.",
        "quote_balance": "У вас {balance} ⚡.",
        "btn_quote_go": "✅ Конвертировать · {price} ⚡",
        "btn_quote_free": "✅ Конвертировать",
        "btn_quote_back": "↩️ Другой формат",
        "converted_file_caption": "Вот ваш сконвертированный файл!",
        "converted_file_caption_free": 'Вот ваш сконвертированный файл! (бесплатно)',
        "conversion_expired": "Срок этой сессии конвертации истёк — отправьте файл заново.",
        "convert_invoice_title": "Конвертировать {src} в {target}",
        "convert_invoice_description": "Разовая конвертация вашего файла на {size_kb} КБ.",
        "convert_invoice_label": "{src}→{target} конвертация",
        "topup_needed": 'Это стоит {price} ⚡, а у вас {balance} ⚡.\n\nОтправлен счёт на {stars} ⭐ — он добавит {credited} ⚡: хватит на эту конвертацию, остаток останется на балансе. /cancel, чтобы отменить.',
        "credit_policy_note": '⚠️ ВНИМАНИЕ: ⚡ списывается, когда конвертация НАЧИНАЕТСЯ. Остановите её сами — ⚡ НЕ вернётся; если сбой на стороне бота, ⚡ сразу вернётся на баланс. Платежи окончательные — Stars не возвращаются.',
        "send_limit_note": '📤 Готовый файл может весить до {mb} МБ. Если он получится больше, отправлен он не будет — тогда ⚡ сразу вернётся на баланс.',
        "too_many_megapixels": 'В этой картинке {mp} мегапикселей — больше {max} МП, которые я могу безопасно конвертировать. Ничего не списано.',
        "too_big_to_send_note": '📐 {formats} не предлагаются: при {mp} МП они выйдут больше {limit} МБ, которые бот может отправить. JPG, WEBP или AVIF поместятся.',
        "raw_too_big": "Это raw-фото — {mp} мегапикселей. Сервер конвертирует raw-фото до {max} МП: для больших не хватает памяти даже на половинный размер. Ничего не списано.",
        "raw_half_note": "⚠️ ВАЖНО: это raw-фото — {mp} МП. Оно конвертируется в половинном размере ({out} МП): для полного кадра серверу не хватает памяти.",
        "video_fit_hdr_note": "⚠️ ВАЖНО: это видео {w}×{h} HDR. Сервер конвертирует его в {fw}×{fh} — для полного кадра ему не хватает памяти. Цвета сохраняются.",
        "video_fit_big_note": "⚠️ ВАЖНО: это видео {w}×{h}. WEBM здесь делается в {fw}×{fh}; MP4, MKV и MOV остаются в полном размере.",
        "video_fit_huge_note": "⚠️ ВАЖНО: это видео {w}×{h}. Сервер конвертирует его в {fw}×{fh} — для полного кадра ему не хватает памяти.",
        "nothing_sendable": 'Любой формат, который я могу предложить, для картинки такого размера выйдет больше {limit} МБ, которые бот может отправить. Ничего не списано.',
        "too_big_to_send_alert": 'Этот формат для такой картинки выйдет больше {limit} МБ, которые бот может отправить.',
        "keep_toggle_off": '📌 Хранить файл для других форматов: выкл',
        "keep_toggle_on": '📌 Хранить файл для других форматов: вкл',
        "ocr_lang_toggle": "🔤 Язык текста: {name} (нажмите, чтобы сменить)",
        "ocr_lang_set": "Теперь язык текста: {name}.",
        "ocr_lang_eng": "английский",
        "ocr_lang_rus": "русский",
        "ocr_lang_uzb": "узбекский (латиница и кириллица)",
        "ocr_too_many_pages_note": "⚠️ ВАЖНО: в этом PDF больше {max} страниц без текста (только картинка). Текст распознаётся максимум на {max} таких страницах, поэтому TXT содержит только текст остальных страниц, а PDF с текстом не предлагается.",
        "target_ocrpdf": "PDF с текстом",
        "keep_explained_on": 'После каждой конвертации я буду хранить файл {minutes} минут, чтобы вы могли конвертировать его и в другие форматы. /cancel удалит его раньше.',
        "keep_explained_off": 'Файл удалится сразу после конвертации.',
        "storage_full_user": 'У меня уже {used} МБ ваших файлов, а на одного человека я храню до {limit} МБ. Дождитесь окончания конвертации или отмените её через /cancel и пришлите снова.',
        "storage_full_global": 'Сейчас не хватает временного места — пришлите это снова через пару минут.',
        "cancel_item_job_running": 'конвертацию {size} МБ {src} в {target} — идёт',
        "cancel_item_job_queued": 'конвертацию {size} МБ {src} в {target} — в очереди, пока ничего не списано',
        "cancel_button_job": '⏹ Конвертация в {target}',
        "queue_full": 'У вас уже {count} конвертаций в очереди или в работе. Пришлите ещё, когда какие-то закончатся.',
        "job_queued": '⏳ {src} → {target} в очереди, перед ней {ahead}. Ничего не списывается, пока она не начнётся.',
        "job_starting": '⏳ {src} → {target}: начинаю…',
        "job_not_running": 'Эта конвертация уже закончилась.',
        "payment_kept_as_credit": 'Оплата получена — +{credited} ⚡, на балансе {balance} ⚡. Файл уже устарел, поэтому ничего не конвертировано; пришлите его снова — оплата пойдёт с баланса.',
        "job_progress": '⏳ Конвертация ещё идёт — прошло {elapsed} мин. Напишу снова через {next} мин., а не позже чем через {last} мин. сообщу, чем всё закончилось.',
        "job_progress_last": '⏳ Конвертация всё ещё идёт — прошло {elapsed} мин. Последнее сообщение — через {last} мин.: к тому времени она закончится или будет остановлена, а ⚡ вернётся на баланс.',
        "job_stop_button": '⏹ Остановить',
        "job_stop_button_queued": '✖ Убрать из очереди',
        "job_done": '✅ {src} → {target} готово.',
        "balance_left_line": 'На балансе осталось {balance} ⚡.',
        "job_user_stopped": '⏹ Остановлено — списанные при старте {price} ⚡ остаются потраченными.',
        "job_user_stopped_free": '⏹ Остановлено.',
        "job_removed_from_queue": '✖ Убрано из очереди — ничего не списано.',
        "job_no_credit": 'Конвертация не началась: она стоит {price} ⚡, а на балансе {balance} ⚡. Ничего не списано — /recharge пополнит баланс.',
        "job_refunded_line": '{price} ⚡ вернулись на баланс — теперь {balance} ⚡.',
        "job_reason_refused": 'Не получилось конвертировать этот файл: {detail}',
        "job_reason_timeout": 'Конвертация не уложилась в {minutes} мин., и я её остановил. Владелец в курсе.',
        "job_reason_too_slow": "На этот файл ушло бы около {minutes} мин., а конвертация здесь может идти не дольше {limit} мин., поэтому я остановил её сразу, а не заставил вас ждать до конца лимита. Подойдёт ролик покороче или в меньшем разрешении.",
        "job_reason_crash": 'Конвертация сломалась на моей стороне. Владелец в курсе.',
        "job_reason_no_memory": "Для этого файла не хватило памяти сервера — так бывает с видео в 4K и HDR. С самим файлом всё в порядке: версия в 1080p или ниже сконвертируется.",
        "job_reason_too_large": '{target} получился на {mb} МБ, а по этой конвертации я могу отправить не больше {limit} МБ. JPG, WEBP или AVIF будут намного меньше. Владелец в курсе.',
        "job_reason_send_failed": 'Файл сконвертирован, но отправить его вам не вышло. Владелец в курсе.',
        "job_reason_interrupted": 'Меня перезапустили посреди конвертации. Пришлите файл снова через минуту.',
        "unknown_order": "Неизвестный заказ.",
        "unrecognized_message": (
            "Это не файл, который я могу конвертировать — пришлите файл или "
            "/formats, чтобы увидеть, что я принимаю. Назовёте формат («heic», "
            "«epub») — скажу, во что он превращается."
        ),
        "unknown_command": "Я не знаю такую команду. Отправьте /start, чтобы увидеть, что я умею.",
        # ---- /formats ----
        "category_document": "документ",
        "category_data": "данные",
        "category_subtitle": "субтитры",
        "category_archive": "архив",
        "formats_title": "📁 Что я конвертирую",
        "formats_intro": (
            "{sources} форматов на вход, {targets} на выход — всего {pairs} "
            "комбинаций. Пришлите файл, и я покажу, во что его можно превратить; "
            "нажмите на категорию ниже, чтобы увидеть весь список."
        ),
        "formats_highlights": (
            "Из менее очевидного:\n"
            "• Фото с iPhone (HEIC) → JPG или PNG\n"
            "• Любая картинка — или целый альбом сразу — в один PDF\n"
            "• PDF или EPUB → PNG, JPG или обычный текст\n"
            "• Документ Word → PDF, HTML или Markdown\n"
            "• CSV, JSON или XML → таблица XLSX\n"
            "• Субтитры: SRT, VTT и ASS между собой или просто их текст"
        ),
        "formats_reads": "Читает",
        "formats_writes": "Пишет",
        "formats_category_title": "📁 Файлы: {category}",
        "formats_lookup": ".{ext} — это файл категории «{category}». Я преобразую его в: {targets}",
        "formats_lookup_none": (
            "Я умею читать .{ext}, но на этом сервере не во что его превратить."
        ),
        "formats_lookup_unknown": (
            "Я не знаю «.{ext}» — /formats покажет все форматы, которые я умею."
        ),
        "btn_more_formats": "⋯ Ещё форматы",
        "btn_fewer_formats": "‹ Меньше",
        "btn_what_formats": "📁 Что вы умеете конвертировать?",
        "btn_formats_back": "‹ Все категории",
        "convert_options_prompt_batch": (
            "Принято — {count} файлов, всего {size}. Во что конвертировать:"
            "\n\n{price_line}"
        ),
        "converted_file_caption_zip": "Готово — {items} файлов, в zip-архиве.",
        "converted_file_caption_pdf": "Готово — {items} картинок, по одной на страницу.",
        "converted_file_caption_pages": "Готово — {items} страниц, в zip-архиве.",
        "converted_docx_note": (
            "Текст — настоящий текст, его можно править: заголовки там, где в "
            "PDF строка была крупнее остальных, полужирный там, где он был, и "
            "картинки в том же порядке. Вёрстка страницы не переносится: "
            "колонки идут одна за другой, таблица превращается в свой текст, а "
            "разбивка на страницы остаётся такой, какой была в PDF."
        ),
        "converted_layout_note": (
            "Заголовки, выделения, списки и таблицы на месте. Вёрстка страницы "
            "не копируется точь-в-точь, а пересобирается заново — Word на этом "
            "сервере нет."
        ),
        "batch_mixed_formats": (
            "Эти файлы разных форматов, так что отправляйте их по одному — или "
            "набор картинок, который я соберу в один PDF."
        ),
        "price_free": "Бесплатно",
        "price_not_supported": "не поддерживается",
        "price_cloud_api_note": "(ограничение облачного Bot API в 20 МБ на скачивание)",
        "price_from": "от {price} ⚡: большинство картинок, песен и документов",
        "price_example": "видео {mb} МБ в MP4: {price} ⚡",
        "price_at_most": "самый большой файл, {mb} МБ: не больше {price} ⚡",
        "price_quoted_first": "Точную цену я назову до того, как что-то спишется, — и спишу именно её.",
    },
}


# Every problem a person can run into ends with its code, and the code is what
# puts a "Report the issue" button under it -- see problems.py. Imported here,
# below the tables, because it is pure data and nothing above needs it.
import problems  # noqa: E402

_BOT = "convert_bot"


def t(lang: str | None, key: str, **kwargs) -> str:
    table = STRINGS.get(lang) or STRINGS["en"]
    template = table.get(key) or STRINGS["en"].get(key, key)
    text = template.format(**kwargs) if kwargs else template
    return text + problems.code_line(problems.code_for(_BOT, key))


# Targets that are not a file extension: `ocrpdf` is a searchable PDF, whose file
# is `<name>.pdf`. Their name is a string of its own, never the pseudo-extension.
_TARGET_NAMES = {"ocrpdf": "target_ocrpdf"}


def target_label(lang: str | None, ext: str | None) -> str:
    """What a target format is called on a button or in a sentence: its
    extension in capitals (PNG), or for a pseudo-target its name."""
    key = _TARGET_NAMES.get(ext or "")
    return t(lang, key) if key else (ext or "?").upper()


async def get_lang(user_id: int, context) -> str:
    """Cached in context.user_data to avoid a DB round-trip on every handler
    call. Falls back to "en" for a user who hasn't chosen a language yet
    (only reachable outside /start's first-run gate, e.g. someone who sends
    a file before ever running /start)."""
    cached = context.user_data.get("lang")
    if cached:
        return cached
    lang = await asyncio.to_thread(db.get_user_language, user_id) or "en"
    context.user_data["lang"] = lang
    return lang


# ---------------------------------------------------------------------------
# The slash menu, in the other two languages
# ---------------------------------------------------------------------------
# English lives in BOT_COMMANDS in bot.py, where the menu can be read by
# reading the file. These are the same commands for a client whose language is
# Uzbek or Russian; shared_features.publish_commands() sends one list per
# language and Telegram picks the matching one.
#
# Deliberately absent: /language, /en, /uz and /rus. Each of the three is
# written in the language it switches TO, and /language is written in all
# three at once, because they are the way back for somebody who chose the
# wrong one. Translating them would make the menu of a Russian client offer
# three lines of Russian, one of which is the only route out.
#
# A command missing from here keeps its English description rather than
# vanishing from that language's menu -- a half-translated menu is a menu
# with commands missing, and a missing command reads as a bot that cannot do
# the thing. Kept honest by tests/test_menu.py, which fails on a command that
# has no entry here and on an entry naming a command that no longer exists.

COMMAND_MENU = {
    "uz": {
        "start": "Bu bot nima qiladi va qanday boshlash kerak",
        "convert": "Faylni o'girish — yoki shunchaki menga yuboring",
        "formats": "Men o'qiydigan va yozadigan barcha formatlar",
        "cancel": "Kutayotgan amalimni to'xtatish",
        "help": "Nimalar qila olaman",
        "feedback": "Taklif yoki muammo haqida yozish",
        "balance": "⚡ kreditingiz va u qayerga sarflangani",
        "recharge": "⚡ kredit to'ldirish",
        "paysupport": "To'lov bilan muammo",
        "privacy": "Siz haqingizda nimalarni saqlayman",
        "terms": "Botdan foydalanish shartlari",
        "deletemydata": "Siz haqingizdagi hamma narsani o'chirish",
    },
    "ru": {
        "start": "Что этот бот делает и с чего начать",
        "convert": "Конвертировать файл — или просто пришлите его",
        "formats": "Все форматы, которые я читаю и пишу",
        "cancel": "Остановить то, чего я жду",
        "help": "Что я умею",
        "feedback": "Предложить идею или сообщить о проблеме",
        "balance": "Ваш ⚡ кредит и куда он ушёл",
        "recharge": "Пополнить ⚡ кредит",
        "paysupport": "Проблема с оплатой",
        "privacy": "Что я о вас храню",
        "terms": "Для чего можно пользоваться ботом",
        "deletemydata": "Удалить всё, что я о вас храню",
    },
}
