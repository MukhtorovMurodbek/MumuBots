import asyncio

import db

SUPPORTED_LANGUAGES = ("en", "uz", "ru")
LANGUAGE_LABELS = {"en": "English 🇬🇧", "uz": "O'zbekcha 🇺🇿", "ru": "Русский 🇷🇺"}

LANGUAGE_PROMPT = (
    "👋 Welcome! / Xush kelibsiz! / Добро пожаловать!\n\n"
    "Please choose a language / Iltimos, tilni tanlang / "
    "Пожалуйста, выберите язык:"
)

STRINGS = {
    "en": {
        "maintenance_note": "🔧 AnonBot is closed for a short repair. Conversations are safe, but nothing sent now is delivered or kept. Please write again later.",
        "flood_wait": "Too fast — wait about {seconds} s and carry on.",
        "sibling_blurb": "Also part of this bot family, see below \U0001f447",
        "donation_nudge": (
            "💙 Find this bot useful? /donate helps pay for hosting. Totally optional."
        ),
        "donate_unknown_currency": 'Unknown currency "{currency}" — try xtr or usd.',
        "donate_currency_not_configured": "{currency} donations aren't set up on this bot yet — try Stars instead.",
        "donate_invalid_amount": "That's not a valid amount — try e.g. /donate 500 or /donate 5 usd.",
        "donate_prompt": (
            "Thanks! It goes toward hosting costs. Pick an amount, or Custom for any other."
        ),
        "donate_custom_button": "✏️ Custom {symbol}",
        "donate_too_many_stars": "That's a lot of stars! Keep it under {max} ⭐ per donation.",
        "donate_out_of_range": "{currency} donations need to be between {lo} and {hi} {symbol}.",
        "donate_invoice_title": "Contribute to hosting",
        "donate_invoice_description": "Helps pay for hosting, and adds {credited} ⚡ of credit for ConvertBot.",
        "donate_invoice_label": "Hosting contribution",
        "donate_invoice_description_fiat": "A one-time voluntary donation towards hosting costs. Thanks!",
        "donate_prompt_credit": "Each ⭐ gives ⚡ credit for ConvertBot. The next {left} ⭐ give {each} ⚡ each ({mult}×): {rate} ⚡ plus a bonus that expires {days} days after payment. ⚡ can NOT be withdrawn or turned back into Stars.",
        "donate_prompt_credit_base": "Each ⭐ gives {rate} ⚡ of credit for ConvertBot. ⚡ can NOT be withdrawn or turned back into Stars.",
        "donate_invoice_error": "⚠️ Telegram wouldn't create that invoice: {error}",
        "stars_unit": "Stars",
        "donate_custom_ask": "How many {unit} to donate? Reply with a number.",
        "donate_invalid_amount_retry": "That's not a valid amount — send /donate to try again.",
        "donate_thanks": "🙏 Thanks for the {amount} ⭐ — genuinely appreciated!",
        "topup_thanks": "🙏 Thanks for {stars} ⭐!\n\n{total} ⚡ of credit has been added in {convert_bot} for conversions. Balance there: {balance} ⚡.",
        "topup_thanks_bonus": 'Of that, {bonus} ⚡ is bonus credit and expires on {date}.',
        "credit_cannot_be_withdrawn": "⚡ is credit for ConvertBot conversions and can NOT be withdrawn or turned back into Stars. Payment problem? /paysupport",
        "topup_refunded": "⚠️ The {stars} ⭐ payment went through, but the ⚡ didn't reach the balance, so the {stars} ⭐ were refunded. Please try again in a few minutes.",
        "topup_failed": "⚠️ The {stars} ⭐ payment went through, but the ⚡ didn't reach the balance. Nothing is lost — it will be added by hand. Follow up: /paysupport",
        "paysupport_text": "💳 Help with a payment\n\nPayments are FINAL: Stars are NOT refunded, and ⚡ can NOT be withdrawn or turned back into Stars. Only a payment that couldn't become credit is refunded, automatically and in full.\n\nCharged with no credit, or charged twice? Write to {contact} with the date, the amount and the Telegram ID, {user_id}. It's put right with credit.\n\n/balance lists the payments.",
        "report_button": "🐞 Send more details",
        "problem_logged_note": "Anonymous technical report saved.",
        "problem_logged_note_optin": "Anonymous technical report saved. More details can be sent, by choice.",
        "report_disclaimer": "📨 Send more details with incident {incident}?\n\nSaved already, with no personal details: the bot, code {code}, the time, the version.\n\nSend adds:\n• the Telegram ID\n• the @username, if there is one\n• the language\n• private chat or group\n\nNever messages or files. Cleared after 30 days, or now with /deletemydata.",
        "report_send": "📨 Send",
        "report_cancel": '✖️ Cancel',
        "report_sent": "✅ Thanks — added to incident {incident}.",
        "report_already": "The details are already on incident {incident}.",
        "report_cancelled": "Cancelled — nothing was sent.",
        "report_failed": "⚠️ The report couldn't be sent right now. Please try again later.",
        "report_invalid": 'This button no longer works.',
        "crash_notice": "⚠️ Something went wrong on the bot's side, so that wasn't done. Please try again.",
        "sandbox_notice": "🧪 Test mode — no real Stars were charged for this.",
        "balance_header": "⚡ Balance: {balance}",
        "balance_totals": "Paid {paid} ⭐ in total · credited {credited} ⚡ · spent {spent} ⚡",
        "balance_rate": "The next {left} ⭐ earn {each} ⚡ each ({mult}×).",
        "balance_rate_base": '1 ⭐ buys {rate} ⚡.',
        "balance_bonus_line": 'Of that, {bonus} ⚡ is bonus credit — {soon} ⚡ of it expires on {date}.',
        "balance_recent": "Recent:",
        "balance_empty_hint": "/donate adds credit at any time.",
        "bot_short_description": (
            "A permanent link that anyone can write to anonymously."
        ),
        "bot_description": (
            "Get a link, post it anywhere, and anyone can write to it anonymously. Answers go in threaded conversations, several at once.\n\nThe bot knows who talks to whom, so replies reach the right thread, but never stores what anyone writes. /privacy\n\nEnglish, Uzbek and Russian."
        ),
        "privacy_heading": "🔒 Privacy",
        "privacy_kept_heading": "What this bot keeps:",
        "privacy_stored": (
            "• the Telegram ID and language\n• the inbox link, if one was made\n• who talks to whom: each conversation as the pair of IDs behind it plus a number, so replies reach the right thread\n• which message belongs to which conversation, by message ID, and when it was relayed\n• who was blocked\n• that a one-time announcement was shown, so it shows only once\n• a timestamp each time the bot is used, to count users\n• daily button-tap counts for the whole bot, not per person\n• daily counts of messages relayed, for the whole bot, not per person\n• donations: the amount and Telegram's payment ID\n\nMessages and reactions are never stored: they pass straight through and live only in the two Telegram chats, where each side can delete their copy. The one exception is /feedback, below.\n\nAnonymous means anonymous to the other side of the conversation: they never see the writer's name, username or ID. The bot itself knows who is paired — that is how replies work."
        ),
        "privacy_problems_heading": "When something goes wrong:",
        "privacy_problems": (
            "Every error is logged without anyone's details: its code, an incident number, the time and the bot's version.\n\nUnder a crash, a failed /deletemydata or /feedback, or an error that keeps happening in one chat, \"Send more details\" attaches the Telegram ID, @username, language and chat type (private or group) to that one incident — only after a tap on Send. Never messages or files. They are cleared after 30 days, or at once with /deletemydata. Error records are deleted after 180 days."
        ),
        "privacy_seen_by_heading": "Who else sees it:",
        "privacy_seen_by": (
            "• Telegram, which carries every message\n• the hosting provider and the database the bot uses"
        ),
        "privacy_others": (
            "• nobody else — messages go through Telegram only, and no outside service is used"
        ),
        "privacy_kept_for_heading": "How long it stays:",
        "privacy_kept_for": "Settings stay until they are erased or the bot is no longer used. Usage times go after about three months; daily counts of what was done, which say nothing about who, after 400 days. Payment records and the ⚡ balance stay longer, for payment disputes. Reserve copies of the database are kept for emergencies only, and never used once older than 30 days. After /deletemydata, an encrypted note of the ID is kept for 30 days, so that restoring a copy erases the data again.\n\nNothing is sold, used for ads, or given to anyone not named above.",
        "privacy_your_choices": (
            "Options:\n/deletemydata — erase everything this bot holds about this account\n/terms — the rules\n\nBlocking the bot erases nothing, so send /deletemydata first."
        ),
        "terms_heading": "📜 Terms",
        "terms_use": (
            "Use it within the law and Telegram's terms. Harassing anyone or pushing past the bot's limits gets an account blocked."
        ),
        "terms_specific": (
            "Messages: being anonymous doesn't make threats, harassment or anything illegal OK. Senders can be blocked for good from the inbox side, and whoever shares an inbox link is responsible for what it invites people to send."
        ),
        "terms_money": "Money: /donate is voluntary and helps pay for hosting. Stars also add ⚡ credit for ConvertBot: 2 ⚡ per ⭐, or 6 for the first 500 Stars and 4 for the next 500; the part above 2 is bonus that expires 90 days after payment. Payments are FINAL: Stars are NOT refunded and credit can NOT be withdrawn — except a payment that couldn't become credit, which is refunded automatically. Telegram handles payments; the bot never sees a card. Problems: /paysupport",
        "terms_no_warranty": (
            "No promises: one person runs this for free, and it can be slow, wrong or down without warning. Keep copies of anything that matters."
        ),
        "policy_full_text": "Full text: {url}",
        "policy_contact": "Questions, complaints or a data request: {contact}",
        "delete_data_confirm": "⚠️ Everything this bot holds about this account will be erased. There is no undo.",
        "delete_data_consequences": "The inbox link stops working for good, open conversations can't be replied to any more, and the block list goes.\n\nSent messages stay in the Telegram chats; they were never stored here.\n\nPayment records stay, without the username, for payment disputes. The ⚡ balance stays too.",
        "delete_data_button_yes": "🗑 Erase it",
        "delete_data_button_no": "↩️ Keep the data",
        "delete_data_kept": "Nothing was erased.",
        "delete_data_done": (
            "🗑 Done — {rows} record(s) erased. Send /start any time to begin fresh."
        ),
        "delete_data_failed": (
            "Couldn't erase it — something went wrong on the bot's side. Please try again in a few minutes."
        ),
        "language_set_confirmation": "✅ Language set to English.",
        "cancel_header": "\u274c Cancelled:",
        "cancel_nothing": "Nothing to cancel.",
        "cancel_ask": "Which one should be stopped? Waiting now:",
        "cancel_kept": "OK — nothing cancelled.",
        "cancel_reply_box_freed": "The reply box is free again.",
        "cancel_button_all": "❌ All of it",
        "cancel_button_none": "↩️ Nothing, keep going",
        "cancel_button_donation": "💸 Donation amount",
        "cancel_item_donation": "the donation amount asked for",
        "cancel_item_stale_prompt": "an old unanswered question",
        "cancel_item_anon_session": "the open anonymous chat — tap the link again to start a new one",
        "cancel_button_anon_session": "💬 The open anonymous chat",
        "feedback_ask": (
            "💬 A message for the operator of these bots: reply to this message.\n\nIt is sent without a name, so no answer can come back. /cancel to stop."
        ),
        "feedback_placeholder": "Feedback",
        "feedback_text_only": "Please send text as a reply to this message, or /cancel.",
        "feedback_thanks": "✅ Thanks — sent anonymously.",
        "feedback_limit": "That's {per_day} today, the daily limit. Thanks — write again tomorrow.",
        "feedback_failed": "Not sent — something went wrong on the bot's side. Please try again later.",
        "cancel_button_feedback": "💬 Feedback",
        "cancel_item_feedback": "the feedback asked for",
        "privacy_feedback_heading": "After /feedback:",
        "privacy_feedback": (
            "It reaches the operator with the bot's name and the chosen language — no name, username or ID. No reply is possible. A copy is kept for 30 days."
        ),
        "start_greeting": "Hey! This bot runs anonymous-question inboxes.\n\n",
        "help_brief": (
            "How it works:\n\n• /link gives a permanent link. Whoever taps it can write here anonymously.\n• To answer, reply to the message (swipe it).\n• Got someone's link? Tap it and write.\n• Reactions reach the other person (standard emoji only).\n• Ideas or problems: /feedback\n\nMain commands: /link · /conversations · /cancel\nEverything else: the full guide, below or /fullhelp.\n\n"
        ),
        "help_full_button": "📖 Full guide",
        "help_brief_button": "↩️ Short version",
        "help_page_everything": "The full guide: /fullhelp",
        "help_page_gone": "That help page is gone. Send /help.",
        "helppage_link": (
            "🔗 /link — the anonymous inbox link\n\nPost it anywhere: whoever opens it can write here anonymously. Each open starts a new conversation. The link stays the same until a new one is made.\nButtons under it:\n• ⏸️ Pause: no new conversations until resumed (/pause, /resume).\n• 🔄 New link: the old one stops starting conversations (/newlink).\n• Counts of guests and conversations (/stats).\nCurrent conversations keep working either way.\n\nExample: send /link and put the link in an Instagram bio."
        ),
        "helppage_which": (
            "❓ /which — which conversation is this message from?\n\nReply to any message here with /which.\n• On the inbox side: the conversation's number is shown.\n• On the guest side: the place where it starts is shown.\n\nExample: \"{example}\""
        ),
        "reactions_update_note": "🆕 Messages here can now get reactions — the other person sees them. /help for more.",
        "gift_ad": "🎉 Mumu bots have passed {users} unique users, so {amount} ⚡ of free credit is being given out! It can be claimed and used in {download} and {convert}.\n\n<i>To claim it, send /gift to either bot, or tap the button it shows. Claim it by {claim_by}; it can be spent until {spend_by}.</i>",
        "gift_teaser": "🎉 Version 2.2.0 is live, and a free ⚡ credit giveaway is coming soon! More at https://t.me/MEaTiRT",
        "reaction_not_passed": "⚠️ The other person won't see that reaction — only standard emoji are passed on.",
        "reaction_one_only": "⚠️ The other person sees only the newest reaction — one per message is passed on.",
        "help_text": "Two ways to use this bot:\n\nA personal inbox — /link gives a permanent link. Post it anywhere (bio, channel, story). Whoever taps it can send a message here anonymously.\nTo answer, reply to the message: swipe it, or press and hold it and choose Reply. Every answer must be a reply, so it reaches the right conversation.\n\nSomeone else's link — tap it and write. The first message goes straight through; after that, reply to the message being answered. Tapping the link again starts a new conversation.\n\nReactions: react to a message and the other person sees the same reaction, shown as the bot's. One per message, only these:\n{reactions}\n⚠️ Telegram Premium (custom emoji) reactions can NOT be passed on.\n\nCommands:\n/link - the inbox link\n/conversations - conversations, open and archived\n/which - reply with it to a message: which conversation is it from?\n/archive - reply with it to a message to archive that conversation\n/newlink, /pause, /resume - also buttons under /link\n/blocked - blocked people\n/stats - counts\n/cancel - stop the current step\n/feedback - write to the operator of these bots (anonymous)\n/donate - help pay for hosting (optional)\n/balance - ⚡ credit\n/paysupport - trouble with a payment\n/en, /uz, /rus - switch language (or /language)\n/privacy - what is kept, and for how long\n/terms - what this bot may be used for\n/deletemydata - delete all stored data\n/start - start over\n/fullhelp - this guide (/help - the short one)\n\n⚠️ Payments are final — Stars are NOT refunded, and ⚡ credit can NOT be withdrawn.\n\n",
        "follow_link_invalid": "That link isn't valid — it may have been reset by whoever shared it. Ask them for a fresh one.",
        "follow_link_own": "That's this account's own link — share it with others: /link",
        "follow_link_blocked": "Messages to this person can't be sent.",
        "follow_link_paused": "This person isn't accepting new anonymous messages right now — try again later.",
        "follow_link_started": (
            "This chat is anonymous — the other side won't see who is writing.\n\nType the first message below. After that, reply to (swipe) the message being answered."
        ),
        "follow_cancel_button": "✖️ Cancel",
        "follow_link_cancelled": "Cancelled.",
        "link_status_paused": "paused — not accepting new conversations",
        "link_status_active": "accepting messages",
        "link_message": (
            "Anonymous inbox link ({status}):\n{url}\n\nPost it anywhere — whoever opens it can write here anonymously."
        ),
        "link_button_pause": "⏸️ Pause",
        "link_button_resume": "▶️ Resume",
        "link_button_newlink": "🔄 New link",
        "anonlink_no_link_yet": "No link yet — send /link first.",
        "anonlink_paused_answer": "Paused.",
        "anonlink_resumed_answer": "Resumed.",
        "anonlink_newlink_answer": "New link made — the old one no longer starts conversations.",
        "newlink_message": "New link (the old one no longer starts new conversations):\n{url}",
        "pause_message": (
            "Paused — no new conversations until /resume. Current ones still work."
        ),
        "resume_message": "Resumed — the link takes new conversations again.",
        "stats_message": "{followers} distinct guest(s) have written here, across {conversations} conversation(s) total.",
        "convs_title": "📥 Inbox",
        "convs_title_archived": "\U0001f5c4 Archived conversations",
        "convs_summary": "{open} open \u00b7 {archived} archived",
        "convs_summary_blocked": "{open} open \u00b7 {archived} archived \u00b7 {blocked} blocked (see /blocked)",
        "convs_empty_open": (
            "Nothing open right now. Share the /link to get messages."
        ),
        "convs_empty_archived": (
            "Nothing archived yet."
        ),
        "convs_row_button": "#{conv_number} \u00b7 {when}",
        "convs_show_archived": "\U0001f5c4 Archived",
        "convs_show_open": "\U0001f4e5 Open",
        "convs_page": "{page}/{pages}",
        "convs_prev": "\u2039",
        "convs_next": "\u203a",
        "conv_card": "Conversation #{conv_number}\n\nOpened {started}\nLast message {when}",
        "conv_card_archived": "Archived {archived}",
        "conv_back_button": "\u2039 Back",
        "conv_jump_button": "\u2934\ufe0f Go to the start",
        "conv_archive_button": "\U0001f5c4 Archive",
        "conv_unarchive_button": "\u21a9\ufe0f Unarchive",
        "conv_archived_answer": "Archived — it comes back if they write again.",
        "conv_unarchived_answer": "Back in the open list.",
        "conv_gone": "That conversation is no longer on record.",
        "jump_bookmark": (
            "📍 Conversation #{conv_number} starts here. Reply to this to write in it."
        ),
        "jump_no_anchor": (
            "The start of conversation #{conv_number} can't be found any more — it was silent for over {days} days."
        ),
        "which_needs_reply": (
            "Reply to a message with /which to see which conversation it's from."
        ),
        "which_unknown": (
            "That message isn't in an open conversation (it may be one sent from here, or archived)."
        ),
        "which_owner": "That message is in conversation #{conv_number}.",
        "which_follower": "That message is in the conversation that starts here.",
        "which_follower_no_anchor": "That message is in a conversation opened {started}.",
        "rel_now": "just now",
        "rel_minutes": "{n} min ago",
        "rel_hours": "{n} h ago",
        "rel_days": "{n} d ago",
        "blocked_none": "Nobody is blocked.",
        "blocked_header": "Blocked guests:",
        "blocked_guest_line": "  \U0001f6ab Guest from conversation(s) {conversations}",
        "blocked_guest_line_unknown": "  \U0001f6ab Guest (no conversation on record)",
        "blocked_unblock_button": "↩️ Unblock (conv. {conversations})",
        "blocked_unblock_button_unknown": "↩️ Unblock",
        "reply_button": "↩️ How to reply",
        "block_button": "\U0001f6ab Block",
        "not_your_conversation": "This conversation belongs to another account.",
        "blocked_answer": "Blocked.",
        "unblock_button_labelled": "↩️ Unblock conversation #{conv_number}",
        "too_fast": "Too fast — wait {seconds} s and try again.",
        "edit_not_relayed": (
            "Edits don't go through — the other side still sees the original message. Send the correction as a new reply."
        ),
        "unblocked_answer": "Unblocked.",
        "edit_relayed": "✏️ The edit has been passed on.",
        "edit_not_relayed_parts": "This message was passed on in several parts, so the edit could not be carried over: the other side still has the first version. Send the correction as a reply to it.",
        "delete_how": "To delete a message on both sides, send /delete as a reply to it.",
        "delete_only_own": "Only messages written in this chat can be deleted this way, not the ones received.",
        "delete_not_relayed": "That message was not passed on, so there is nothing to delete on the other side.",
        "delete_too_old": "That message is older than 48 hours, and Telegram no longer allows deleting it on the other side.",
        "delete_failed": "The message could not be deleted on the other side.",
        "deleted_both": "🗑 Deleted on both sides.",
        "delete_marker": "🗑 A message here was deleted by its sender.",
        "help_edits": "Edits and deletions: an edit to a message already sent is carried over to the other side. To delete a message on both sides, send /delete as a reply to it (within 48 hours). When a guest deletes a message, a short note stays in its place, so the guest can still be blocked.",
        "delivery_blocked": "The message couldn't be delivered.",
        "inbox_gone": "This inbox no longer exists.",
        "incoming_header": "\U0001f4e9 Conversation #{conv_number}",
        "incoming_header_follower": "\U0001f4ec New message",
        "delivery_forbidden": (
            "Not sent — this person hasn't started the bot yet."
        ),
        "delivery_failed": "Couldn't deliver that message: {error}",
        "sent_confirmation": "Sent ✅",
        "reply_no_match": "The message was not sent — that reply couldn't be matched to a conversation.",
        "must_reply": (
            "Not sent. Reply to the message above (swipe it) to send it there."
        ),
        "reply_button_hint": (
            "Swipe this message to the right, or press and hold it and choose Reply."
        ),
        "must_reply_opening": (
            "Not sent yet. Reply to the message above (swipe it) to make this the first message."
        ),
        "must_reply_ambiguous": (
            "Not sent — several conversations are open here. Reply to the message being answered (swipe it)."
        ),
        "restarting_send_again": (
            "🔄 The bot is being updated — send that again in a few seconds."
        ),
        "update_soon_try_later": "🔧 The bot is about to be updated — please try again in about {minutes} min. A message will follow once it's back.",
        "update_soon_try_later_soon": "🔧 The bot is being updated — please try again shortly. A message will follow once it's back.",
        "update_will_reset": "🔧 The bot is about to be updated, and the current step will be reset. It can be started again in a few minutes.",
        "update_done_try_now": "✅ Update done — try again now.",
        "gift_granted": "🎁 Mumu bots passed 100 users — thanks! {amount} ⚡ of free credit has been added, good until {date}: about {conversions} paid conversions in {convert} or big downloads in {download}. /balance",
        "credit_updated": "🎁 The balance has been updated from {before} ⚡ to {after} ⚡!",
        "reply_forbidden": "Couldn't deliver the reply — the other side may have blocked or left the bot.",
        "reply_failed": "Couldn't deliver the reply: {error}",
        "delivered_confirmation": "Delivered ↩️",
        "reply_stale": (
            "Not sent — that message isn't in an open conversation. Reply to a message the bot delivered here."
        ),
        "generic_nudge": "Got a link from someone? Tap it first. For a personal inbox: /link",
        "export_removed": "📦 /export has been removed: the bot now keeps less about conversations. Messages stay in the Telegram chat.",
        "archive_needs_reply": (
            "Reply to a message from the conversation with /archive, or pick one in /conversations."
        ),
        "archive_not_owner": (
            "Conversations can be archived from the inbox side only."
        ),
        "archive_already": "Conversation #{conv_number} is already archived.",
        "archive_confirm": (
            "Archive conversation #{conv_number}?\n\nNothing is deleted and they aren't told. It comes back if they write again."
        ),
        "archive_button_yes": "🗂 Archive #{conv_number}",
        "archive_button_no": "Keep it open",
        "archive_done": "🗂 Conversation #{conv_number} archived. /conversations to see it.",
        "archive_kept": "Left open.",
        "unknown_command": "That command isn't recognized. Send /help to see what this bot can do.",
    },
    "uz": {
        "maintenance_note": "🔧 AnonBot qisqa ta'mirda. Suhbatlar joyida, lekin hozir yuborilgan narsa yetkazilmaydi va saqlanmaydi. Keyinroq yana yozing.",
        "flood_wait": "Juda tez — taxminan {seconds} soniya kutib, davom eting.",
        "sibling_blurb": 'Oilamizdagi boshqa botlar pastda 👇',
        "donation_nudge": "💙 Bot foydali bo'ldimi? /donate server xarajatlariga yordam beradi. Mutlaqo ixtiyoriy.",
        "donate_unknown_currency": '"{currency}" degan valyuta yo\'q — xtr yoki usd deb yozing.',
        "donate_currency_not_configured": "Bu botda hozircha {currency} bilan xayriya qilib bo'lmaydi — Stars'dan foydalaning.",
        "donate_invalid_amount": "Miqdor noto'g'ri — masalan, /donate 500 yoki /donate 5 usd deb yozing.",
        "donate_prompt": "Rahmat! Bu server xarajatlariga ketadi. Miqdorni tanlang yoki boshqa miqdor uchun «Boshqa»ni bosing.",
        "donate_custom_button": "✏️ Boshqa {symbol}",
        "donate_too_many_stars": "Bu juda ko'p! Bir martalik xayriya {max} ⭐ dan oshmasin.",
        "donate_out_of_range": "{currency} bilan xayriya {lo} dan {hi} {symbol} gacha bo'lishi kerak.",
        "donate_invoice_title": 'Server xarajatlariga hissa',
        "donate_invoice_description": "Server xarajatlariga yordam beradi va ConvertBot uchun {credited} ⚡ kredit qo'shadi.",
        "donate_invoice_label": 'Xayriya',
        "donate_invoice_description_fiat": 'Server xarajatlari uchun bir martalik ixtiyoriy xayriya. Rahmat!',
        "donate_prompt_credit": "Har bir ⭐ ConvertBot uchun ⚡ kredit beradi. Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×): {rate} ⚡ va to'lovdan {days} kun o'tgach yo'qoladigan bonus. ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI.",
        "donate_prompt_credit_base": "Har bir ⭐ ConvertBot uchun {rate} ⚡ kredit beradi. ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI.",
        "donate_invoice_error": "⚠️ Telegram to'lov hisobini yaratmadi: {error}",
        "stars_unit": "Stars (yulduzcha)",
        "donate_custom_ask": "Qancha {unit} xayriya qilinadi? Faqat sonni yozib yuboring.",
        "donate_invalid_amount_retry": "Miqdor noto'g'ri — qaytadan urinish uchun /donate yuboring.",
        "donate_thanks": '🙏 {amount} ⭐ uchun katta rahmat!',
        "topup_thanks": "🙏 {stars} ⭐ uchun rahmat!\n\n{convert_bot} da konvertatsiyalar uchun {total} ⚡ kredit qo'shildi. U yerdagi balans: {balance} ⚡.",
        "topup_thanks_bonus": 'Shundan {bonus} ⚡ — bonus kredit, uning muddati {date} kuni tugaydi.',
        "credit_cannot_be_withdrawn": "⚡ — ConvertBot'dagi konvertatsiyalar uchun kredit, uni yechib olib yoki Stars'ga aylantirib BO'LMAYDI. To'lov bilan muammo? /paysupport",
        "topup_refunded": "⚠️ {stars} ⭐ to'lov o'tdi, lekin ⚡ balansga tushmadi, shuning uchun {stars} ⭐ qaytarildi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "topup_failed": "⚠️ {stars} ⭐ to'lov o'tdi, lekin ⚡ balansga tushmadi. Hech narsa yo'qolmaydi — u qo'lda qo'shib qo'yiladi. Kuzatish: /paysupport",
        "paysupport_text": "💳 To'lov bo'yicha yordam\n\nTo'lovlar QAYTARILMAYDI: Stars qaytarilMAYDI, ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI. Faqat kreditga aylanmagan to'lov avtomatik ravishda to'liq qaytariladi.\n\nPul yechildi-yu kredit kelmadimi yoki ikki marta yechildimi? {contact} ga sana, miqdor va Telegram ID raqamini ({user_id}) yozing. Kredit bilan to'g'rilab beriladi.\n\n/balance to'lovlarni ko'rsatadi.",
        "report_button": "🐞 Qo'shimcha ma'lumot yuborish",
        "problem_logged_note": "Anonim texnik hisobot saqlandi.",
        "problem_logged_note_optin": "Anonim texnik hisobot saqlandi. Ixtiyoriy ravishda qo'shimcha ma'lumot yuborish mumkin.",
        "report_disclaimer": "📨 {incident} hodisasiga qo'shimcha ma'lumot yuborilsinmi?\n\nAllaqachon saqlangan, shaxsiy ma'lumotlarsiz: bot, {code} kodi, vaqt, versiya.\n\n«Yuborish» qo'shadi:\n• Telegram ID raqami\n• bo'lsa, @username\n• til\n• shaxsiy suhbatmi yoki guruh\n\nXabarlar va fayllar hech qachon kirmaydi. 30 kundan keyin o'chiriladi, /deletemydata bilan esa darhol.",
        "report_send": "📨 Yuborish",
        "report_cancel": '✖️ Bekor qilish',
        "report_sent": "✅ Rahmat — {incident} hodisasiga qo'shildi.",
        "report_already": "Ma'lumotlar {incident} hodisasida allaqachon bor.",
        "report_cancelled": "Bekor qilindi — hech narsa yuborilmadi.",
        "report_failed": "⚠️ Hozir xabarni yuborib bo'lmadi. Keyinroq qayta urinib ko'ring.",
        "report_invalid": 'Bu tugma endi ishlamaydi.',
        "crash_notice": "⚠️ Bot tomonida xatolik yuz berdi, shuning uchun bajarilmadi. Qaytadan urinib ko'ring.",
        "sandbox_notice": '🧪 Test rejimi — haqiqiy Stars yechilmadi.',
        "balance_header": "⚡ Balans: {balance}",
        "balance_totals": "Jami {paid} ⭐ to'landi · {credited} ⚡ qo'shildi · {spent} ⚡ sarflandi",
        "balance_rate": 'Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×).',
        "balance_rate_base": '1 ⭐ = {rate} ⚡.',
        "balance_bonus_line": 'Shundan {bonus} ⚡ — bonus kredit; {soon} ⚡ ning muddati {date} kuni tugaydi.',
        "balance_recent": 'Oxirgi amallar:',
        "balance_empty_hint": "/donate orqali istalgan paytda kredit qo'shiladi.",
        "bot_short_description": (
            "Istalgan odam anonim yoza oladigan doimiy havola."
        ),
        "bot_description": "Havola oling, uni istalgan joyga joylang — istalgan odam unga anonim yoza oladi. Javoblar alohida suhbatlarda, bir nechtasida birdaniga beriladi.\n\nBot javoblar to'g'ri suhbatga borishi uchun kim kim bilan gaplashayotganini biladi, lekin yozilgan narsani hech qachon saqlamaydi. /privacy\n\nO'zbek, ingliz va rus tillarida.",
        "privacy_heading": "🔒 Maxfiylik",
        "privacy_kept_heading": "Bu bot nimalarni saqlaydi:",
        "privacy_stored": "• Telegram ID raqami va til\n• quti havolasi, agar yaratilgan bo'lsa\n• kim kim bilan gaplashayotgani: har bir suhbat ortidagi ikki ID va raqam sifatida — javoblar to'g'ri suhbatga borishi uchun\n• qaysi xabar qaysi suhbatga tegishli (xabar ID si bo'yicha) va qachon yetkazilgani\n• kimlar bloklangani\n• bir martalik e'lon ko'rsatilgani — u faqat bir marta ko'rsatilishi uchun\n• botdan har safar foydalanilgan vaqt — foydalanuvchilarni sanash uchun\n• butun bot bo'yicha kunlik tugma bosishlar soni, odamlar bo'yicha emas\n• butun bot bo'yicha kunlik yetkazilgan xabarlar soni, odamlar bo'yicha emas\n• xayriyalar: miqdor va Telegram to'lov ID si\n\nXabarlar va reaksiyalar hech qachon saqlanmaydi: ular to'g'ridan-to'g'ri o'tadi va faqat ikki Telegram chatida qoladi, har kim o'z nusxasini o'chira oladi. Yagona istisno — /feedback, quyida.\n\nAnonim — suhbatning ikkinchi tomoni uchun anonim degani: u yozuvchining ismi, username yoki ID raqamini hech qachon ko'rmaydi. Botning o'zi esa kim kim bilan bog'langanini biladi — javoblar shunday ishlaydi.",
        "privacy_problems_heading": "Nimadir noto'g'ri ketganda:",
        "privacy_problems": "Har bir xato hech kimning ma'lumotisiz yoziladi: kodi, hodisa raqami, vaqti va bot versiyasi.\n\nIshdan chiqish, bajarilmagan /deletemydata yoki /feedback, yoki bitta chatda takror-takror yuz berayotgan xato ostidagi «Qo'shimcha ma'lumot yuborish» o'sha hodisaga Telegram ID, @username, til va chat turini (shaxsiy yoki guruh) qo'shadi — faqat «Yuborish» bosilgandan keyin. Xabarlar va fayllar hech qachon qo'shilmaydi. Ular 30 kundan keyin yoki /deletemydata bilan darhol o'chiriladi. Xato yozuvlari 180 kundan keyin o'chiriladi.",
        "privacy_seen_by_heading": "Yana kim ko'ra oladi:",
        "privacy_seen_by": "• Telegram — barcha xabarlarni u yetkazadi\n• hosting provayderi va bot ishlatadigan ma'lumotlar bazasi",
        "privacy_others": "• boshqa hech kim — xabarlar faqat Telegram orqali o'tadi, tashqi xizmatlar ishlatilmaydi",
        "privacy_kept_for_heading": "Qancha vaqt saqlanadi:",
        "privacy_kept_for": "Sozlamalar o'chirilguncha yoki botdan foydalanish to'xtaguncha saqlanadi. Foydalanish vaqtlari taxminan uch oydan keyin, qilingan ishlarning kunlik hisobi esa (unda kimligi yo'q) 400 kundan keyin o'chiriladi. To'lov yozuvlari va ⚡ balans to'lov nizolari uchun uzoqroq saqlanadi. Ma'lumotlar bazasining zaxira nusxalari faqat favqulodda holatlar uchun saqlanadi va 30 kundan eskisi hech qachon ishlatilmaydi. /deletemydata dan keyin ID raqamining shifrlangan qaydi 30 kun saqlanadi, shunda nusxa tiklansa ham ma'lumotlar yana o'chiriladi.\n\nHech narsa sotilmaydi, reklamaga ishlatilmaydi va yuqorida aytilmaganlarga berilmaydi.",
        "privacy_your_choices": "Imkoniyatlar:\n/deletemydata — bot ushbu hisob haqida saqlagan hamma narsani o'chirish\n/terms — qoidalar\n\nBotni bloklash hech narsani o'chirmaydi, shuning uchun avval /deletemydata yuboring.",
        "terms_heading": "📜 Shartlar",
        "terms_use": "Botdan qonun va Telegram shartlari doirasida foydalaning. Kimnidir bezovta qilgan yoki bot chegaralarini buzgan akkaunt bloklanadi.",
        "terms_specific": "Xabarlar: anonimlik tahdid, bezovtalik yoki noqonuniy narsalarni oqlamaydi. Quti tomonidan yuboruvchini butunlay bloklash mumkin; quti havolasini ulashgan kishi odamlarni nima yuborishga chaqirgani uchun o'zi javob beradi.",
        "terms_money": "Pul: /donate ixtiyoriy va server xarajatlariga yordam beradi. Stars ConvertBot uchun ⚡ kredit ham beradi: har ⭐ uchun 2 ⚡, ilk 500 Stars uchun 6 va keyingi 500 uchun 4; 2 dan ortig'i bonus bo'lib, to'lovdan 90 kun o'tgach yo'qoladi. To'lovlar QAYTARILMAYDI: Stars qaytarilMAYDI, kreditni yechib olib BO'LMAYDI — kreditga aylanmagan to'lov bundan mustasno, u avtomatik qaytariladi. To'lovlarni Telegram amalga oshiradi; bot karta raqamini ko'rmaydi. Muammo: /paysupport",
        "terms_no_warranty": "Kafolat yo'q: buni bir kishi bepul yuritadi, u sekin ishlashi, xato qilishi yoki ogohlantirishsiz to'xtashi mumkin. Muhim narsalarning nusxasini alohida saqlang.",
        "policy_full_text": "To'liq matn: {url}",
        "policy_contact": "Savol, shikoyat yoki ma'lumot so'rovi uchun: {contact}",
        "delete_data_confirm": "⚠️ Bot ushbu hisob haqida saqlagan barcha ma'lumotlar o'chiriladi. Buni ortga qaytarib bo'lmaydi.",
        "delete_data_consequences": "Quti havolasi butunlay ishlamay qoladi, ochiq suhbatlarga endi javob yozib bo'lmaydi, bloklar ro'yxati ham o'chadi.\n\nYuborilgan xabarlar Telegram chatlarida qoladi; ular bu yerda hech qachon saqlanmagan.\n\nTo'lov yozuvlari to'lov nizolari uchun username'siz qoladi. ⚡ balans ham qoladi.",
        "delete_data_button_yes": "🗑 O'chirilsin",
        "delete_data_button_no": "↩️ Ma'lumotlar qolsin",
        "delete_data_kept": "Hech narsa o'chirilmadi.",
        "delete_data_done": "🗑 Tayyor — {rows} ta yozuv o'chirildi. Yangidan boshlash uchun istalgan vaqt /start yuboring.",
        "delete_data_failed": "O'chirib bo'lmadi — bot tomonida xatolik yuz berdi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "language_set_confirmation": "✅ Til o'zbekchaga o'zgartirildi.",
        "cancel_header": "\u274c Bekor qilindi:",
        "cancel_nothing": "Bekor qilinadigan narsa yo'q.",
        "cancel_ask": "Qaysi biri bekor qilinsin? Hozir quyidagilar kutilmoqda:",
        "cancel_kept": "Yaxshi — hech narsa bekor qilinmadi.",
        "cancel_reply_box_freed": "Xabar yozish maydoni endi bo'sh.",
        "cancel_button_all": "❌ Hammasini",
        "cancel_button_none": "↩️ Hech biri, davom etilsin",
        "cancel_button_donation": "💸 Xayriya miqdori",
        "cancel_item_donation": 'kiritilishi kutilayotgan xayriya miqdori',
        "cancel_item_stale_prompt": "javobsiz qolgan eski savol",
        "feedback_ask": (
            "💬 Botlar operatoriga xabar: shu xabarga javob qilib yozing.\n\nXabar ismsiz yuboriladi, shuning uchun javob qaytmaydi. To'xtatish: /cancel"
        ),
        "feedback_placeholder": "Fikr-mulohaza",
        "feedback_text_only": "Shu xabarga javob qilib matn yuboring yoki /cancel.",
        "feedback_thanks": "✅ Rahmat — anonim yuborildi.",
        "feedback_limit": "Bugun {per_day} ta bo'ldi, bu kunlik chegara. Rahmat — ertaga yana yozing.",
        "feedback_failed": "Yuborilmadi — bot tomonida xatolik yuz berdi. Keyinroq qayta urinib ko'ring.",
        "cancel_button_feedback": "💬 Fikr-mulohaza",
        "cancel_item_feedback": "so'ralgan fikr-mulohaza",
        "privacy_feedback_heading": "/feedback yuborilganda:",
        "privacy_feedback": (
            "U operatorga bot nomi va tanlangan til bilan yetib boradi — ism, username yoki ID raqamisiz. Javob yozib bo'lmaydi. Nusxasi 30 kun saqlanadi."
        ),
        "cancel_item_anon_session": "ochiq turgan anonim suhbat — yangisini boshlash uchun havolani qayta bosing",
        "cancel_button_anon_session": "💬 Ochiq anonim suhbat",
        "start_greeting": "Salom! Bu bot anonim savol-javob qutilarini boshqaradi.\n\n",
        "help_brief": (
            "Bot qanday ishlaydi:\n\n• /link doimiy havola beradi. Uni bosgan har kim shu yerga anonim yoza oladi.\n• Javob berish uchun xabarga javob qiling (o'ngga suring).\n• Birovning havolasi keldimi? Uni bosing va yozing.\n• Reaksiyalar ikkinchi tomonga yetib boradi (faqat oddiy emoji).\n• Taklif yoki muammo: /feedback\n\nAsosiy buyruqlar: /link · /conversations · /cancel\nQolgani: to'liq qo'llanma, pastda yoki /fullhelp.\n\n"
        ),
        "help_full_button": "📖 To'liq qo'llanma",
        "help_brief_button": "↩️ Qisqa variant",
        "help_page_everything": "To'liq qo'llanma: /fullhelp",
        "help_page_gone": "Bu yordam sahifasi endi yo'q. /help yuboring.",
        "helppage_link": (
            "🔗 /link — anonim quti havolasi\n\nUni istalgan joyga joylang: ochgan har kim shu yerga anonim yoza oladi. Har ochilishda yangi suhbat boshlanadi. Yangisi yaratilmaguncha havola o'zgarmaydi.\nOstidagi tugmalar:\n• ⏸️ To'xtatish: qayta yoqilmaguncha yangi suhbat yo'q (/pause, /resume).\n• 🔄 Yangi havola: eskisi endi suhbat boshlamaydi (/newlink).\n• Mehmonlar va suhbatlar soni (/stats).\nBoshlangan suhbatlar har holda ishlayveradi.\n\nMisol: /link yuboring va havolani Instagram bio'siga qo'ying."
        ),
        "helppage_which": (
            "❓ /which — bu xabar qaysi suhbatdan?\n\nShu yerdagi istalgan xabarga /which bilan javob qiling.\n• Quti tomonida: suhbat raqami ko'rsatiladi.\n• Mehmon tomonida: suhbat boshlangan joy ko'rsatiladi.\n\nMisol: \"{example}\""
        ),
        "reactions_update_note": "🆕 Endi bu yerda xabarlarga reaksiya qoldirish mumkin — ikkinchi tomon uni ko'radi. Batafsil: /help",
        "gift_ad": "🎉 Mumu botlarida {users} dan ortiq foydalanuvchi bo'ldi, shuning uchun hammaga {amount} ⚡ bepul kredit beriladi! Uni {download} va {convert} botlarida olib, ishlatish mumkin.\n\n<i>Olish uchun ikki botdan biriga /gift yuboring yoki u ko'rsatgan tugmani bosing. Uni {claim_by} gacha oling; uni {spend_by} gacha ishlatish mumkin.</i>",
        "gift_teaser": "🎉 2.2.0 versiyasi ishga tushdi, tez orada esa bepul ⚡ kredit tarqatiladi! Batafsil: https://t.me/MEaTiRT",
        "reaction_not_passed": "⚠️ Suhbatdosh bu reaksiyani ko'rmaydi — faqat oddiy emojilar yetkaziladi.",
        "reaction_one_only": "⚠️ Suhbatdosh faqat oxirgi reaksiyani ko'radi — har bir xabarga bittasi yetkaziladi.",
        "help_text": "Botdan ikki xil foydalanish mumkin:\n\nShaxsiy quti — /link doimiy havola beradi. Uni istalgan joyga joylang (bio, kanal, story). Uni bosgan har kim shu yerga anonim yoza oladi.\nJavob berish uchun xabarga javob qiling: o'ngga suring yoki bosib turib «Javob berish»ni tanlang. Har bir javob shunday yozilishi shart, shunda u to'g'ri suhbatga boradi.\n\nBirovning havolasi — uni bosing va yozing. Birinchi xabar darhol yetib boradi; keyin javob berilayotgan xabarga javob qiling. Havola yana bosilsa, yangi suhbat boshlanadi.\n\nReaksiyalar: xabarga reaksiya qoldirilsa, ikkinchi tomon ham xuddi shu reaksiyani bot nomidan ko'radi. Har bir xabarga bittadan, faqat quyidagilar:\n{reactions}\n⚠️ Telegram Premium (maxsus emoji) reaksiyalari o'tkazilMAYDI.\n\nBuyruqlar:\n/link - quti havolasi\n/conversations - suhbatlar, ochiq va arxivdagi\n/which - xabarga javob qilib yuboring: u qaysi suhbatdan?\n/archive - xabarga javob qilib yuboring: o'sha suhbat arxivlanadi\n/newlink, /pause, /resume - /link ostidagi tugmalar ham bor\n/blocked - bloklanganlar\n/stats - hisoblar\n/cancel - joriy amalni to'xtatish\n/feedback - botlar operatoriga yozish (anonim)\n/donate - server xarajatlariga yordam (ixtiyoriy)\n/balance - ⚡ kredit\n/paysupport - to'lov bilan muammo\n/en, /uz, /rus - tilni almashtirish (yoki /language)\n/privacy - nimalar saqlanadi\n/terms - botdan nima uchun foydalanish mumkin\n/deletemydata - saqlangan barcha ma'lumotlarni o'chirish\n/start - boshidan boshlash\n/fullhelp - shu qo'llanma (/help - qisqasi)\n\n⚠️ To'lovlar qaytarilmaydi — Stars qaytarilMAYDI, ⚡ kreditni yechib olib BO'LMAYDI.\n\n",
        "follow_link_invalid": "Bu havola yaroqsiz — uni ulashgan kishi qayta tiklagan bo'lishi mumkin. Ulardan yangisini so'rang.",
        "follow_link_own": "Bu shu hisobning o'z havolasi — uni boshqalarga ulashing: /link",
        "follow_link_blocked": "Bu odamga xabar yuborib bo'lmaydi.",
        "follow_link_paused": "Bu odam hozircha yangi anonim xabarlar qabul qilmayapti — keyinroq urinib ko'ring.",
        "follow_link_started": "Bu suhbat anonim — ikkinchi tomon kim yozayotganini ko'rmaydi.\n\nBirinchi xabarni pastga yozing. Keyin javob berilayotgan xabarga javob qiling (o'ngga suring).",
        "follow_cancel_button": "✖️ Bekor qilish",
        "follow_link_cancelled": "Bekor qilindi.",
        "link_status_paused": "to'xtatilgan — yangi suhbatlar qabul qilinmaydi",
        "link_status_active": 'xabarlar qabul qilinmoqda',
        "link_message": "Anonim quti havolasi ({status}):\n{url}\n\nUni istalgan joyga joylang — ochgan har kim shu yerga anonim yoza oladi.",
        "link_button_pause": "⏸️ To'xtatish",
        "link_button_resume": '▶️ Qayta yoqish',
        "link_button_newlink": "🔄 Yangi havola",
        "anonlink_no_link_yet": "Hali havola yo'q — avval /link yuboring.",
        "anonlink_paused_answer": "To'xtatildi.",
        "anonlink_resumed_answer": 'Qayta yoqildi.',
        "anonlink_newlink_answer": "Yangi havola yaratildi — eskisi endi suhbat boshlamaydi.",
        "newlink_message": "Yangi havola (eskisi endi yangi suhbat boshlamaydi):\n{url}",
        "pause_message": "To'xtatildi — /resume yubormaguningizcha yangi suhbat boshlanmaydi. Boshlanganlari ishlayveradi.",
        "resume_message": "Qayta yoqildi — havola yana yangi suhbatlarni qabul qiladi.",
        "stats_message": "Bu yerga {followers} nafar mehmon yozgan, jami {conversations} ta suhbat.",
        "convs_title": "📥 Quti",
        "convs_title_archived": "\U0001f5c4 Arxivlangan suhbatlar",
        "convs_summary": "{open} ta ochiq \u00b7 {archived} ta arxivda",
        "convs_summary_blocked": "{open} ta ochiq \u00b7 {archived} ta arxivda \u00b7 {blocked} ta bloklangan (/blocked)",
        "convs_empty_open": "Hozircha ochiq suhbat yo'q. Xabar olish uchun /link havolasini ulashing.",
        "convs_empty_archived": (
            "Arxivda hali hech narsa yo'q."
        ),
        "convs_row_button": "#{conv_number} \u00b7 {when}",
        "convs_show_archived": "\U0001f5c4 Arxiv",
        "convs_show_open": "\U0001f4e5 Ochiq",
        "convs_page": "{page}/{pages}",
        "convs_prev": "\u2039",
        "convs_next": "\u203a",
        "conv_card": "#{conv_number}-suhbat\n\nOchilgan: {started}\nOxirgi xabar: {when}",
        "conv_card_archived": "Arxivlangan: {archived}",
        "conv_back_button": "\u2039 Orqaga",
        "conv_jump_button": "\u2934\ufe0f Boshiga o'tish",
        "conv_archive_button": "\U0001f5c4 Arxivga",
        "conv_unarchive_button": "\u21a9\ufe0f Arxivdan chiqarish",
        "conv_archived_answer": "Arxivlandi — yana yozishsa, qaytib keladi.",
        "conv_unarchived_answer": "Ochiqlar ro'yxatiga qaytdi.",
        "conv_gone": "Bu suhbat haqida endi ma'lumot yo'q.",
        "jump_bookmark": (
            "📍 #{conv_number}-suhbat shu yerdan boshlanadi. Unda yozish uchun shu xabarga javob qiling."
        ),
        "jump_no_anchor": "#{conv_number}-suhbat boshini endi topib bo'lmaydi — u {days} kundan ortiq jim turgan.",
        "which_needs_reply": (
            "Xabarga /which bilan javob qiling — u qaysi suhbatdan ekani ko'rsatiladi."
        ),
        "which_unknown": (
            "Bu xabar ochiq suhbatlarning hech birida yo'q (bu yerdan yuborilgan yoki arxivdagi bo'lishi mumkin)."
        ),
        "which_owner": "Bu xabar #{conv_number}-suhbatga tegishli.",
        "which_follower": "Bu xabar shu yerdan boshlanadigan suhbatga tegishli.",
        "which_follower_no_anchor": "Bu xabar {started} ochilgan suhbatga tegishli.",
        "rel_now": "hozir",
        "rel_minutes": "{n} daq. oldin",
        "rel_hours": "{n} soat oldin",
        "rel_days": "{n} kun oldin",
        "blocked_none": "Hech kim bloklanmagan.",
        "blocked_header": "Bloklangan mehmonlar:",
        "blocked_guest_line": "  \U0001f6ab {conversations}-suhbatdagi mehmon",
        "blocked_guest_line_unknown": "  \U0001f6ab Mehmon (suhbat qayd etilmagan)",
        "blocked_unblock_button": "↩️ Blokdan chiqarish ({conversations}-suhbat)",
        "blocked_unblock_button_unknown": "↩️ Blokdan chiqarish",
        "reply_button": "↩️ Qanday javob beriladi?",
        "block_button": "\U0001f6ab Bloklash",
        "not_your_conversation": "Bu suhbat boshqa hisobga tegishli.",
        "blocked_answer": "Bloklandi.",
        "unblock_button_labelled": "↩️ #{conv_number}-suhbatni blokdan chiqarish",
        "too_fast": "Juda tez — {seconds} soniya kutib, qayta urinib ko'ring.",
        "edit_not_relayed": "Tahrirlar yetib bormaydi — suhbatdosh asl xabarni ko'radi. Tuzatishni yangi javob qilib yuboring.",
        "unblocked_answer": "Blokdan chiqarildi.",
        "edit_relayed": "✏️ Tahrir qarshi tomonga yetkazildi.",
        "edit_not_relayed_parts": "Bu xabar bir necha qism bo'lib yetkazilgan, shuning uchun tahrirni o'tkazib bo'lmadi: qarshi tomonda birinchi variant turibdi. Tuzatishni o'sha xabarga javob qilib yuboring.",
        "delete_how": "Xabarni ikkala tomonda o'chirish uchun unga javob qilib /delete yuboring.",
        "delete_only_own": "Shu usulda faqat shu chatda yozilgan xabarlarni o'chirish mumkin, kelganlarini emas.",
        "delete_not_relayed": "Bu xabar hech kimga yetkazilmagan, qarshi tomonda o'chiradigan narsa yo'q.",
        "delete_too_old": "Bu xabar 48 soatdan eski, Telegram uni qarshi tomonda o'chirishga endi ruxsat bermaydi.",
        "delete_failed": "Xabarni qarshi tomonda o'chirib bo'lmadi.",
        "deleted_both": "🗑 Ikkala tomonda o'chirildi.",
        "delete_marker": "🗑 Bu yerdagi xabar yuboruvchi tomonidan o'chirildi.",
        "help_edits": "Tahrir va o'chirish: yuborilgan xabarga kiritilgan tahrir qarshi tomonga ham o'tadi. Xabarni ikkala tomonda o'chirish uchun unga javob qilib /delete yuboring (48 soat ichida). Mehmon xabarini o'chirsa, uning o'rnida qisqa izoh qoladi, shuning uchun mehmonni baribir bloklash mumkin.",
        "delivery_blocked": "Xabar yetkazilmadi.",
        "inbox_gone": "Bu quti endi mavjud emas.",
        "incoming_header": "\U0001f4e9 #{conv_number}-suhbat",
        "incoming_header_follower": "\U0001f4ec Yangi xabar",
        "delivery_forbidden": "Yuborilmadi — bu odam hali botni ishga tushirmagan.",
        "delivery_failed": "Bu xabarni yetkazib bo'lmadi: {error}",
        "sent_confirmation": "Yuborildi ✅",
        "reply_no_match": "Xabar yuborilmadi — bu javobni biror suhbatga moslab bo'lmadi.",
        "must_reply": "Yuborilmadi. Yuqoridagi xabarga javob qiling (o'ngga suring), shunda u o'sha yerga boradi.",
        "reply_button_hint": "Bu xabarni o'ngga suring yoki bosib turib «Javob berish»ni tanlang.",
        "must_reply_opening": "Hali yuborilmadi. Yuqoridagi xabarga javob qiling (o'ngga suring) — bu birinchi xabar bo'ladi.",
        "must_reply_ambiguous": "Yuborilmadi — bu yerda bir nechta suhbat ochiq. Javob berilayotgan xabarga javob qiling (o'ngga suring).",
        "restarting_send_again": "🔄 Bot yangilanmoqda — bir necha soniyadan keyin qayta yuboring.",
        "update_soon_try_later": "🔧 Bot hozir yangilanadi — taxminan {minutes} daqiqadan keyin qayta urinib ko'ring. Ishga tushgach, xabar yuboriladi.",
        "update_soon_try_later_soon": "🔧 Bot yangilanmoqda — birozdan keyin qayta urinib ko'ring. Ishga tushgach, xabar yuboriladi.",
        "update_will_reset": "🔧 Bot hozir yangilanadi va joriy amal to'xtaydi. Bir necha daqiqadan keyin qaytadan boshlash mumkin.",
        "update_done_try_now": "✅ Yangilanish tugadi — endi qayta urinib ko'ring.",
        "gift_granted": "🎁 Mumu botlari 100 foydalanuvchidan oshdi — rahmat! {amount} ⚡ bepul kredit qo'shildi, {date} gacha amal qiladi: {convert} da taxminan {conversions} ta pullik konvertatsiya yoki {download} da katta fayllar. /balance",
        "credit_updated": "🎁 Balans {before} ⚡ dan {after} ⚡ ga yangilandi!",
        "reply_forbidden": "Javobni yetkazib bo'lmadi — ikkinchi tomon bloklagan yoki botni tark etgan bo'lishi mumkin.",
        "reply_failed": "Javobni yetkazib bo'lmadi: {error}",
        "delivered_confirmation": "Yetkazildi ↩️",
        "reply_stale": "Yuborilmadi — bu xabar ochiq suhbatda emas. Bot shu yerga yetkazgan xabarlardan biriga javob qiling.",
        "generic_nudge": "Kimdandir havola keldimi? Avval uni bosing. Shaxsiy quti uchun: /link",
        "export_removed": "📦 /export olib tashlandi: bot endi suhbatlar haqida kamroq ma'lumot saqlaydi. Xabarlar Telegram chatida qoladi.",
        "archive_needs_reply": (
            "Suhbatdagi xabarga /archive bilan javob qiling yoki /conversations dan tanlang."
        ),
        "archive_not_owner": "Suhbatlarni faqat quti tomonidan arxivlash mumkin.",
        "archive_already": "#{conv_number}-suhbat allaqachon arxivlangan.",
        "archive_confirm": (
            "#{conv_number}-suhbat arxivlansinmi?\n\nHech narsa o'chirilmaydi va ularga aytilmaydi. Yana yozishsa, qaytib keladi."
        ),
        "archive_button_yes": "🗂 #{conv_number} ni arxivlash",
        "archive_button_no": "Ochiq qolsin",
        "archive_done": "🗂 #{conv_number}-suhbat arxivlandi. Ko'rish uchun /conversations.",
        "archive_kept": "Ochiq qoldirildi.",
        "unknown_command": "Bunday buyruq yo'q. Bot nimalar qila olishini bilish uchun /help yuboring.",
    },
    "ru": {
        "maintenance_note": "🔧 AnonBot ненадолго закрыт на ремонт. Беседы в сохранности, но отправленное сейчас не доставляется и не сохраняется. Напишите позже.",
        "flood_wait": "Слишком быстро — подождите около {seconds} с и продолжайте.",
        "sibling_blurb": "Тоже часть этой семьи ботов, смотрите ниже \U0001f447",
        "donation_nudge": (
            "💙 Бот полезен? /donate помогает оплачивать хостинг. Совершенно необязательно."
        ),
        "donate_unknown_currency": 'Неизвестная валюта "{currency}" — попробуйте xtr или usd.',
        "donate_currency_not_configured": "Пожертвования в {currency} на этом боте пока не настроены — попробуйте Stars.",
        "donate_invalid_amount": "Это некорректная сумма — попробуйте, например, /donate 500 или /donate 5 usd.",
        "donate_prompt": (
            "Спасибо! Это идёт на хостинг. Выберите сумму или «Другое», чтобы ввести свою."
        ),
        "donate_custom_button": "✏️ Другое {symbol}",
        "donate_too_many_stars": "Это очень много звёзд! Пусть будет меньше {max} ⭐ за одно пожертвование.",
        "donate_out_of_range": "Пожертвования в {currency} должны быть в диапазоне от {lo} до {hi} {symbol}.",
        "donate_invoice_title": "Поддержать хостинг",
        "donate_invoice_description": "Помогает оплачивать хостинг и добавляет {credited} ⚡ кредита для ConvertBot.",
        "donate_invoice_label": "Вклад в хостинг",
        "donate_invoice_description_fiat": 'Разовое добровольное пожертвование на хостинг. Спасибо!',
        "donate_prompt_credit": "Каждая ⭐ даёт ⚡ кредит для ConvertBot. Следующие {left} ⭐ дают по {each} ⚡ ({mult}×): {rate} ⚡ плюс бонус, который сгорает через {days} дней после оплаты. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.",
        "donate_prompt_credit_base": "Каждая ⭐ даёт {rate} ⚡ кредита для ConvertBot. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.",
        "donate_invoice_error": "⚠️ Telegram не смог создать этот счёт: {error}",
        "stars_unit": "Stars (звёзды)",
        "donate_custom_ask": "Сколько {unit} пожертвовать? Ответьте числом.",
        "donate_invalid_amount_retry": "Это некорректная сумма — отправьте /donate, чтобы попробовать снова.",
        "donate_thanks": "🙏 Спасибо за {amount} ⭐ — это по-настоящему ценно!",
        "topup_thanks": "🙏 Спасибо за {stars} ⭐!\n\nНачислено {total} ⚡ кредита в {convert_bot} на конвертации. Баланс там: {balance} ⚡.",
        "topup_thanks_bonus": 'Из них {bonus} ⚡ — бонусный кредит, он сгорит {date}.',
        "credit_cannot_be_withdrawn": "⚡ — кредит на конвертации в ConvertBot, его НЕЛЬЗЯ вывести или обменять обратно на Stars. Проблема с оплатой? /paysupport",
        "topup_refunded": "⚠️ Платёж на {stars} ⭐ прошёл, но ⚡ не дошёл до баланса, поэтому {stars} ⭐ вернули. Попробуйте снова через несколько минут.",
        "topup_failed": "⚠️ Платёж на {stars} ⭐ прошёл, но ⚡ не дошёл до баланса. Ничего не потеряно — он будет добавлен вручную. Уточнить: /paysupport",
        "paysupport_text": "💳 Помощь с оплатой\n\nПлатежи окончательны: Stars НЕ возвращаются, ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars. Возвращается только платёж, который не удалось превратить в кредит, — автоматически и полностью.\n\nДеньги списаны, а кредита нет, или списали дважды? Напишите на {contact}: дата, сумма и Telegram ID, {user_id}. Всё будет исправлено кредитом.\n\n/balance показывает платежи.",
        "report_button": "🐞 Отправить подробности",
        "problem_logged_note": "Анонимный технический отчёт сохранён.",
        "problem_logged_note_optin": "Анонимный технический отчёт сохранён. По желанию можно отправить подробности.",
        "report_disclaimer": "📨 Отправить подробности к инциденту {incident}?\n\nУже сохранено, без личных данных: бот, код {code}, время, версия.\n\n«Отправить» добавит:\n• Telegram ID\n• @username, если есть\n• язык\n• личный чат или группа\n\nСообщения и файлы — никогда. Стирается через 30 дней или сразу через /deletemydata.",
        "report_send": "📨 Отправить",
        "report_cancel": '✖️ Отмена',
        "report_sent": "✅ Спасибо — добавлено к инциденту {incident}.",
        "report_already": "Данные уже есть в инциденте {incident}.",
        "report_cancelled": "Отменено — ничего не отправлено.",
        "report_failed": '⚠️ Сейчас не удалось отправить сообщение. Попробуйте позже.',
        "report_invalid": 'Эта кнопка больше не работает.',
        "crash_notice": "⚠️ Что-то пошло не так на стороне бота, поэтому это не сделано. Попробуйте ещё раз.",
        "sandbox_notice": "🧪 Тестовый режим — настоящие Stars не списывались.",
        "balance_header": "⚡ Баланс: {balance}",
        "balance_totals": "Всего оплачено {paid} ⭐ · начислено {credited} ⚡ · потрачено {spent} ⚡",
        "balance_rate": 'Следующие {left} ⭐ дают по {each} ⚡ ({mult}×).',
        "balance_rate_base": '1 ⭐ даёт {rate} ⚡.',
        "balance_bonus_line": 'Из них {bonus} ⚡ — бонусный кредит; {soon} ⚡ сгорит {date}.',
        "balance_recent": "Последние операции:",
        "balance_empty_hint": "/donate добавит кредит в любой момент.",
        "bot_short_description": (
            "Постоянная ссылка, по которой пишут анонимно."
        ),
        "bot_description": (
            "Получите ссылку, разместите её где угодно — и по ней смогут писать анонимно. Ответы идут в отдельных беседах, сразу в нескольких.\n\nБот знает, кто с кем говорит, чтобы ответы попадали в нужную беседу, но никогда не хранит написанное. /privacy\n\nНа русском, английском и узбекском."
        ),
        "privacy_heading": "🔒 Конфиденциальность",
        "privacy_kept_heading": "Что бот хранит:",
        "privacy_stored": (
            "• Telegram ID и язык\n• ссылка на ящик, если она создана\n• кто с кем общается: каждая беседа — пара ID и номер, чтобы ответы попадали в нужную беседу\n• какое сообщение к какой беседе относится (по ID сообщения) и когда оно передано\n• кто заблокирован\n• что разовое объявление уже показано — чтобы оно показывалось один раз\n• время каждого использования бота — чтобы считать пользователей\n• дневное число нажатий кнопок по всему боту, не по людям\n• дневное число пересланных сообщений по всему боту, не по людям\n• пожертвования: сумма и ID платежа в Telegram\n\nСообщения и реакции никогда не хранятся: они проходят насквозь и остаются только в двух чатах Telegram, где каждый может удалить свою копию. Единственное исключение — /feedback, ниже.\n\nАнонимно — значит анонимно для другой стороны беседы: она никогда не видит имя, username или ID пишущего. Сам бот знает, кто с кем связан, — так работают ответы."
        ),
        "privacy_problems_heading": "Когда что-то ломается:",
        "privacy_problems": "Каждая ошибка записывается без чьих-либо данных: код, номер инцидента, время и версия бота.\n\nПод сбоем, неудавшимся /deletemydata или /feedback, или ошибкой, которая повторяется в одном чате снова и снова, «Отправить подробности» прикрепляет к этому инциденту Telegram ID, @username, язык и тип чата (личный или группа) — только после нажатия «Отправить». Сообщения и файлы — никогда. Это стирается через 30 дней или сразу через /deletemydata. Записи об ошибках удаляются через 180 дней.",
        "privacy_seen_by_heading": "Кто ещё это видит:",
        "privacy_seen_by": (
            "• Telegram, через который идут все сообщения\n• хостинг-провайдер и база данных бота"
        ),
        "privacy_others": (
            "• больше никто — сообщения идут только через Telegram, внешние сервисы не используются"
        ),
        "privacy_kept_for_heading": "Сколько это хранится:",
        "privacy_kept_for": "Настройки хранятся до удаления или до прекращения пользования ботом. Время использования удаляется примерно через три месяца, дневные счётчики сделанного (в них нет ничего о том, кто) — через 400 дней. Платёжные записи и ⚡ баланс хранятся дольше — на случай споров об оплате. Резервные копии базы хранятся только на случай аварии, и копия старше 30 дней никогда не используется. После /deletemydata зашифрованная отметка с ID хранится 30 дней, чтобы при восстановлении из копии данные снова были удалены.\n\nНичего не продаётся, не используется для рекламы и не передаётся никому, кроме названных выше.",
        "privacy_your_choices": (
            "Возможности:\n/deletemydata — удалить всё, что бот хранит об этом аккаунте\n/terms — правила\n\nБлокировка бота ничего не удаляет, поэтому сначала отправьте /deletemydata."
        ),
        "terms_heading": "📜 Условия",
        "terms_use": (
            "Пользуйтесь ботом в рамках закона и правил Telegram. За травлю или обход ограничений бота аккаунт блокируется."
        ),
        "terms_specific": (
            "Сообщения: анонимность не делает угрозы, травлю или что-то незаконное допустимыми. Со стороны ящика отправителя можно заблокировать навсегда; тот, кто делится ссылкой на ящик, сам отвечает за то, что предлагает присылать."
        ),
        "terms_money": "Деньги: /donate добровольный и помогает оплачивать хостинг. Stars также дают ⚡ кредит для ConvertBot: 2 ⚡ за ⭐, или 6 за первые 500 Stars и 4 за следующие 500; всё сверх 2 — бонус, который сгорает через 90 дней после оплаты. Платежи окончательны: Stars НЕ возвращаются, кредит НЕЛЬЗЯ вывести — кроме платежа, который не удалось превратить в кредит, он возвращается автоматически. Платежи проводит Telegram; бот не видит номер карты. Проблемы: /paysupport",
        "terms_no_warranty": (
            "Без гарантий: бота бесплатно ведёт один человек, он может тормозить, ошибаться или отключиться без предупреждения. Храните копии важного у себя."
        ),
        "policy_full_text": "Полный текст: {url}",
        "policy_contact": "Вопросы, жалобы или запрос по данным: {contact}",
        "delete_data_confirm": "⚠️ Будет стёрто всё, что бот хранит об этом аккаунте. Отменить будет нельзя.",
        "delete_data_consequences": "Ссылка на ящик перестанет работать навсегда, на открытые беседы больше нельзя будет ответить, список блокировок удалится.\n\nОтправленные сообщения останутся в чатах Telegram; здесь они никогда не хранились.\n\nПлатёжные записи останутся без username — на случай споров об оплате. ⚡ баланс тоже останется.",
        "delete_data_button_yes": "🗑 Стереть",
        "delete_data_button_no": "↩️ Оставить данные",
        "delete_data_kept": "Ничего не стёрто.",
        "delete_data_done": (
            "🗑 Готово — удалено записей: {rows}. Отправьте /start, когда захотите начать заново."
        ),
        "delete_data_failed": (
            "Не удалось удалить — что-то пошло не так на стороне бота. Попробуйте через несколько минут."
        ),
        "language_set_confirmation": "✅ Язык изменён на русский.",
        "cancel_header": "\u274c \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e:",
        "cancel_nothing": "Отменять нечего.",
        "cancel_ask": "Что остановить? Сейчас ожидается:",
        "cancel_kept": "Хорошо — ничего не отменено.",
        "cancel_reply_box_freed": "Поле ответа снова свободно.",
        "cancel_button_all": "❌ Всё",
        "cancel_button_none": "↩️ Ничего, продолжить",
        "cancel_button_donation": "💸 Сумма пожертвования",
        "cancel_item_donation": "запрошенная сумма пожертвования",
        "cancel_item_stale_prompt": "старый вопрос без ответа",
        "feedback_ask": (
            "💬 Сообщение для оператора этих ботов: ответьте на это сообщение.\n\nОно уходит без имени, поэтому ответа не будет. Остановить: /cancel"
        ),
        "feedback_placeholder": "Отзыв",
        "feedback_text_only": "Отправьте текст в ответ на это сообщение или /cancel.",
        "feedback_thanks": "✅ Спасибо — отправлено анонимно.",
        "feedback_limit": "Сегодня уже {per_day} — это дневной предел. Спасибо — напишите завтра.",
        "feedback_failed": "Не отправлено — что-то пошло не так на стороне бота. Попробуйте позже.",
        "cancel_button_feedback": "💬 Отзыв",
        "cancel_item_feedback": "запрошенный отзыв",
        "privacy_feedback_heading": "После /feedback:",
        "privacy_feedback": (
            "Оно приходит оператору с названием бота и выбранным языком — без имени, username и ID. Ответить на него нельзя. Копия хранится 30 дней."
        ),
        "cancel_item_anon_session": "\u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0439 \u0430\u043d\u043e\u043d\u0438\u043c\u043d\u044b\u0439 \u0447\u0430\u0442 — \u043d\u0430\u0436\u043c\u0438\u0442\u0435 \u0441\u0441\u044b\u043b\u043a\u0443 \u0441\u043d\u043e\u0432\u0430, \u0447\u0442\u043e\u0431\u044b \u043d\u0430\u0447\u0430\u0442\u044c \u043d\u043e\u0432\u044b\u0439",
        "cancel_button_anon_session": "💬 Открытый анонимный чат",
        "start_greeting": "Здравствуйте! Этот бот управляет анонимными почтовыми ящиками для вопросов.\n\n",
        "help_brief": (
            "Как это устроено:\n\n• /link даёт постоянную ссылку. Любой, кто по ней перейдёт, сможет анонимно написать сюда.\n• Чтобы ответить, ответьте на сообщение (смахните его).\n• Прислали чужую ссылку? Перейдите по ней и напишите.\n• Реакции доходят до собеседника (только обычные эмодзи).\n• Идеи или проблемы: /feedback\n\nОсновные команды: /link · /conversations · /cancel\nВсё остальное: полное руководство, ниже или /fullhelp.\n\n"
        ),
        "help_full_button": "📖 Полное руководство",
        "help_brief_button": "↩️ Кратко",
        "help_page_everything": "Полное руководство: /fullhelp",
        "help_page_gone": "Этой страницы справки больше нет. Отправьте /help.",
        "helppage_link": (
            "🔗 /link — ссылка на анонимный ящик\n\nРазместите её где угодно: любой, кто её откроет, сможет анонимно написать сюда. Каждый переход начинает новую беседу. Ссылка не меняется, пока не создана новая.\nКнопки под ней:\n• ⏸️ Пауза: новых бесед нет до возобновления (/pause, /resume).\n• 🔄 Новая ссылка: старая перестаёт начинать беседы (/newlink).\n• Число гостей и бесед (/stats).\nТекущие беседы в любом случае продолжают работать.\n\nПример: отправьте /link и поставьте ссылку в био Instagram."
        ),
        "helppage_which": (
            "❓ /which — из какой беседы это сообщение?\n\nОтветьте на любое сообщение здесь командой /which.\n• Со стороны ящика: показывается номер беседы.\n• Со стороны гостя: показывается, где она начинается.\n\nПример: \"{example}\""
        ),
        "reactions_update_note": "🆕 Теперь здесь можно ставить реакции на сообщения — собеседник их увидит. Подробнее: /help",
        "gift_ad": "🎉 У Mumu ботов уже больше {users} уникальных пользователей, поэтому раздаётся {amount} ⚡ бесплатного кредита! Получить и потратить его можно в {download} и {convert}.\n\n<i>Чтобы получить подарок, отправьте /gift любому из этих двух ботов или нажмите кнопку, которую он покажет. Получите его до {claim_by}; потратить его можно до {spend_by}.</i>",
        "gift_teaser": "🎉 Вышла версия 2.2.0, а скоро будет раздача бесплатного ⚡ кредита! Подробнее: https://t.me/MEaTiRT",
        "reaction_not_passed": "⚠️ Собеседник не увидит эту реакцию — передаются только обычные эмодзи.",
        "reaction_one_only": "⚠️ Собеседник видит только последнюю реакцию — на сообщение передаётся одна.",
        "help_text": "Ботом можно пользоваться двумя способами:\n\nСвой ящик — /link даёт постоянную ссылку. Разместите её где угодно (био, канал, сторис). Любой, кто по ней перейдёт, сможет анонимно написать сюда.\nЧтобы ответить, ответьте на сообщение: смахните его или нажмите и удерживайте, затем «Ответить». Каждый ответ должен быть ответом на сообщение — так он попадёт в нужную беседу.\n\nЧужая ссылка — перейдите по ней и напишите. Первое сообщение уйдёт сразу; дальше отвечайте на то сообщение, на которое нужен ответ. Повторный переход по ссылке начинает новую беседу.\n\nРеакции: поставьте реакцию — собеседник увидит такую же, от имени бота. Одна на сообщение, только эти:\n{reactions}\n⚠️ Реакции Telegram Premium (свои эмодзи) НЕ передаются.\n\nКоманды:\n/link - ссылка на ящик\n/conversations - беседы, открытые и в архиве\n/which - ответьте ею на сообщение: из какой оно беседы?\n/archive - ответьте ею на сообщение, чтобы убрать беседу в архив\n/newlink, /pause, /resume - также кнопки под /link\n/blocked - заблокированные\n/stats - статистика\n/cancel - остановить текущее действие\n/feedback - написать оператору ботов (анонимно)\n/donate - помочь с хостингом (необязательно)\n/balance - ⚡ кредит\n/paysupport - проблема с оплатой\n/en, /uz, /rus - сменить язык (или /language)\n/privacy - что хранится\n/terms - для чего можно использовать бота\n/deletemydata - удалить все сохранённые данные\n/start - начать сначала\n/fullhelp - это руководство (/help - краткое)\n\n⚠️ Платежи окончательны — Stars НЕ возвращаются, ⚡ кредит НЕЛЬЗЯ вывести.\n\n",
        "follow_link_invalid": "Эта ссылка недействительна — возможно, тот, кто ею поделился, сбросил её. Попросите у него новую.",
        "follow_link_own": "Это собственная ссылка этого аккаунта — поделитесь ею с другими: /link",
        "follow_link_blocked": "Этому человеку написать нельзя.",
        "follow_link_paused": "Этот человек сейчас не принимает новые анонимные сообщения — попробуйте позже.",
        "follow_link_started": (
            "Эта переписка анонимна — собеседник не увидит, кто пишет.\n\nНапишите первое сообщение ниже. Дальше отвечайте на то сообщение, на которое нужен ответ (смахните его)."
        ),
        "follow_cancel_button": "✖️ Отмена",
        "follow_link_cancelled": "Отменено.",
        "link_status_paused": "на паузе — новые беседы не принимаются",
        "link_status_active": "принимает сообщения",
        "link_message": (
            "Ссылка на анонимный ящик ({status}):\n{url}\n\nРазместите её где угодно — любой, кто её откроет, сможет анонимно написать сюда."
        ),
        "link_button_pause": "⏸️ Пауза",
        "link_button_resume": "▶️ Возобновить",
        "link_button_newlink": "🔄 Новая ссылка",
        "anonlink_no_link_yet": "Ссылки пока нет — сначала отправьте /link.",
        "anonlink_paused_answer": "На паузе.",
        "anonlink_resumed_answer": "Возобновлено.",
        "anonlink_newlink_answer": "Новая ссылка создана — старая больше не начинает беседы.",
        "newlink_message": "Новая ссылка (старая больше не начинает новые беседы):\n{url}",
        "pause_message": (
            "На паузе — новых бесед не будет до /resume. Текущие продолжают работать."
        ),
        "resume_message": "Возобновлено — ссылка снова принимает новые беседы.",
        "stats_message": "Сюда писали {followers} разных гостей, всего {conversations} беседы(бесед).",
        "convs_title": "📥 Беседы",
        "convs_title_archived": "\U0001f5c4 Беседы в архиве",
        "convs_summary": "{open} открытых \u00b7 {archived} в архиве",
        "convs_summary_blocked": "{open} открытых \u00b7 {archived} в архиве \u00b7 {blocked} заблокировано (см. /blocked)",
        "convs_empty_open": (
            "Открытых бесед нет. Поделитесь ссылкой из /link, чтобы получать сообщения."
        ),
        "convs_empty_archived": (
            "В архиве пока пусто."
        ),
        "convs_row_button": "#{conv_number} \u00b7 {when}",
        "convs_show_archived": "\U0001f5c4 Архив",
        "convs_show_open": "\U0001f4e5 Открытые",
        "convs_page": "{page}/{pages}",
        "convs_prev": "\u2039",
        "convs_next": "\u203a",
        "conv_card": "Беседа #{conv_number}\n\nНачата: {started}\nПоследнее сообщение: {when}",
        "conv_card_archived": "В архиве с: {archived}",
        "conv_back_button": "\u2039 Назад",
        "conv_jump_button": "\u2934\ufe0f К началу",
        "conv_archive_button": "\U0001f5c4 В архив",
        "conv_unarchive_button": "\u21a9\ufe0f Из архива",
        "conv_archived_answer": "В архиве — вернётся, если снова придёт сообщение.",
        "conv_unarchived_answer": "Снова в списке открытых.",
        "conv_gone": "Такой беседы больше нет в записях.",
        "jump_bookmark": (
            "📍 Здесь начинается беседа #{conv_number}. Ответьте на это сообщение, чтобы написать в неё."
        ),
        "jump_no_anchor": (
            "Начало беседы #{conv_number} больше не найти — в ней было тихо больше {days} дней."
        ),
        "which_needs_reply": (
            "Ответьте на сообщение командой /which — будет показано, из какой оно беседы."
        ),
        "which_unknown": (
            "Этого сообщения нет в открытых беседах (возможно, оно отправлено отсюда или в архиве)."
        ),
        "which_owner": "Это сообщение относится к беседе #{conv_number}.",
        "which_follower": "Это сообщение относится к беседе, которая начинается здесь.",
        "which_follower_no_anchor": "Это сообщение относится к беседе, открытой {started}.",
        "rel_now": "только что",
        "rel_minutes": "{n} мин. назад",
        "rel_hours": "{n} ч. назад",
        "rel_days": "{n} дн. назад",
        "blocked_none": "Никто не заблокирован.",
        "blocked_header": "Заблокированные гости:",
        "blocked_guest_line": "  \U0001f6ab Гость из бесед(ы) {conversations}",
        "blocked_guest_line_unknown": "  \U0001f6ab Гость (бесед не найдено)",
        "blocked_unblock_button": "↩️ Разблокировать (беседа {conversations})",
        "blocked_unblock_button_unknown": "↩️ Разблокировать",
        "reply_button": "↩️ Как ответить",
        "block_button": "\U0001f6ab Заблокировать",
        "not_your_conversation": "Эта беседа принадлежит другому аккаунту.",
        "blocked_answer": "Заблокировано.",
        "unblock_button_labelled": "↩️ Разблокировать беседу #{conv_number}",
        "too_fast": "Слишком быстро — подождите {seconds} с и попробуйте снова.",
        "edit_not_relayed": (
            "Правки не передаются — собеседник видит исходное сообщение. Отправьте исправление новым ответом."
        ),
        "unblocked_answer": "Разблокирован.",
        "edit_relayed": "✏️ Правка передана.",
        "edit_not_relayed_parts": "Это сообщение было передано несколькими частями, поэтому правку перенести не удалось: у собеседника первый вариант. Отправьте исправление ответом на него.",
        "delete_how": "Чтобы удалить сообщение у обеих сторон, отправьте /delete ответом на него.",
        "delete_only_own": "Так можно удалить только сообщения, написанные в этом чате, а не полученные.",
        "delete_not_relayed": "Это сообщение никому не передавалось, у собеседника удалять нечего.",
        "delete_too_old": "Этому сообщению больше 48 часов, и Telegram уже не позволяет удалить его у собеседника.",
        "delete_failed": "Не удалось удалить сообщение у собеседника.",
        "deleted_both": "🗑 Удалено у обеих сторон.",
        "delete_marker": "🗑 Сообщение здесь удалено отправителем.",
        "help_edits": "Правки и удаление: правка уже отправленного сообщения переносится и к собеседнику. Чтобы удалить сообщение у обеих сторон, отправьте /delete ответом на него (в течение 48 часов). Если гость удаляет сообщение, на его месте остаётся короткая пометка, так что гостя по-прежнему можно заблокировать.",
        "delivery_blocked": "Сообщение не удалось доставить.",
        "inbox_gone": "Этого ящика больше не существует.",
        "incoming_header": "\U0001f4e9 Беседа #{conv_number}",
        "incoming_header_follower": "\U0001f4ec Новое сообщение",
        "delivery_forbidden": (
            "Не отправлено — этот человек ещё не запускал бота."
        ),
        "delivery_failed": "Не удалось доставить это сообщение: {error}",
        "sent_confirmation": "Отправлено ✅",
        "reply_no_match": "Сообщение не отправлено — не удалось сопоставить этот ответ с какой-либо беседой.",
        "must_reply": (
            "Не отправлено. Ответьте на сообщение выше (смахните его), чтобы отправить туда."
        ),
        "reply_button_hint": (
            "Смахните это сообщение вправо или нажмите и удерживайте его, затем «Ответить»."
        ),
        "must_reply_opening": (
            "Ещё не отправлено. Ответьте на сообщение выше (смахните его) — это станет первым сообщением."
        ),
        "must_reply_ambiguous": (
            "Не отправлено — здесь открыто несколько бесед. Ответьте на то сообщение, на которое нужен ответ (смахните его)."
        ),
        "restarting_send_again": (
            "🔄 Бот обновляется — отправьте это снова через несколько секунд."
        ),
        "update_soon_try_later": "🔧 Бот сейчас обновится — попробуйте снова примерно через {minutes} мин. Когда он вернётся, придёт сообщение.",
        "update_soon_try_later_soon": "🔧 Бот обновляется — попробуйте чуть позже. Когда он вернётся, придёт сообщение.",
        "update_will_reset": "🔧 Бот сейчас обновится, и текущее действие сбросится. Начать заново можно через несколько минут.",
        "update_done_try_now": "✅ Обновление готово — попробуйте снова.",
        "gift_granted": "🎁 У Mumu-ботов уже больше 100 пользователей — спасибо! Начислено {amount} ⚡ бесплатного кредита до {date}: это около {conversions} платных конвертаций в {convert} или больших загрузок в {download}. /balance",
        "credit_updated": "🎁 Баланс обновлён: было {before} ⚡, стало {after} ⚡!",
        "reply_forbidden": "Не удалось доставить ответ — возможно, собеседник заблокировал отправителя или покинул бота.",
        "reply_failed": "Не удалось доставить ответ: {error}",
        "delivered_confirmation": "Доставлено ↩️",
        "reply_stale": (
            "Не отправлено — этого сообщения нет в открытых беседах. Ответьте на одно из сообщений, которые бот сюда доставил."
        ),
        "generic_nudge": "Пришла чья-то ссылка? Сначала перейдите по ней. Свой ящик: /link",
        "export_removed": "📦 Команда /export удалена: теперь бот хранит меньше данных о переписках. Сообщения остаются в чате Telegram.",
        "archive_needs_reply": (
            "Ответьте на сообщение из беседы командой /archive или выберите её в /conversations."
        ),
        "archive_not_owner": (
            "Архивировать беседы можно только со стороны ящика."
        ),
        "archive_already": "Беседа #{conv_number} уже в архиве.",
        "archive_confirm": (
            "Убрать беседу #{conv_number} в архив?\n\nНичего не удаляется, и собеседнику не сообщается. Если он снова напишет, беседа вернётся."
        ),
        "archive_button_yes": "🗂 В архив #{conv_number}",
        "archive_button_no": "Оставить открытой",
        "archive_done": "🗂 Беседа #{conv_number} в архиве. Посмотреть — /conversations.",
        "archive_kept": "Оставил открытой.",
        "unknown_command": "Такой команды нет. Отправьте /help, чтобы увидеть, что умеет бот.",
    },
}


import problems  # noqa: E402

_BOT = "anon_bot"


def t(lang: str | None, key: str, **kwargs) -> str:
    table = STRINGS.get(lang) or STRINGS["en"]
    template = table.get(key) or STRINGS["en"].get(key, key)
    text = template.format(**kwargs) if kwargs else template
    return text + problems.code_line(problems.code_for(_BOT, key))


async def get_lang(user_id: int, context) -> str:
    cached = context.user_data.get("lang")
    if cached:
        return cached
    lang = await asyncio.to_thread(db.get_user_language, user_id) or "en"
    context.user_data["lang"] = lang
    return lang


COMMAND_MENU = {
    "uz": {
        "start": "Bu bot nima qiladi va qanday boshlash kerak",
        "link": "Quti havolasi va sozlamalari",
        "conversations": "Suhbatlar",
        "which": "Bu qaysi suhbat? — xabarga javob qilib yuboring",
        "blocked": "Bloklanganlar",
        "cancel": "Joriy amalni to'xtatish",
        "help": "Bot nimalar qila oladi",
        "feedback": "Taklif yoki muammo haqida yozish",
        "balance": "⚡ kredit balansi",
        "donate": "Server xarajatlariga hissa qo'shish",
        "paysupport": "To'lov bilan muammo",
        "privacy": "Nimalar saqlanadi",
        "terms": "Botdan foydalanish shartlari",
        "deletemydata": "Saqlangan barcha ma'lumotlarni o'chirish",
        "delete": "Xabarni ikkala tomonda o'chirish — unga javob qilib",
    },
    "ru": {
        "start": "Что этот бот делает и с чего начать",
        "link": "Ссылка на ящик и её настройки",
        "conversations": "Беседы",
        "which": "Какой это разговор? — ответьте на сообщение",
        "blocked": "Заблокированные",
        "cancel": "Остановить текущее действие",
        "help": "Что умеет бот",
        "feedback": "Предложить идею или сообщить о проблеме",
        "balance": "Баланс ⚡ кредита",
        "donate": "Помочь с расходами на хостинг",
        "paysupport": "Проблема с оплатой",
        "privacy": "Что хранится",
        "terms": "Для чего можно пользоваться ботом",
        "deletemydata": "Удалить все сохранённые данные",
        "delete": "Удалить сообщение у обеих сторон — ответом на него",
    },
}
