"""The /privacy, /terms and /deletemydata texts, rendered for real.

A missing translation key does not raise here -- i18n.t() falls back to
English and then to the key name itself, so the failure mode is a user in
Tashkent being shown the word "privacy_kept_for" in the middle of a privacy
notice. Nothing in the code notices, and the one place it shows up is the
screen of the person who asked what the bot knows about them.

So this renders both documents in all three languages and asserts that no key
name survived into the output, that every placeholder was filled, and that the
erase command the notice promises actually exists in that bot's db.py.

    python3 tests/test_policies.py <bot>

Run per bot, like test_imports.py, because each bot has its own i18n module
and they cannot share a process. No database and no network: db is stubbed.
"""
import ast
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BOT = sys.argv[1]

sys.path.insert(0, str(HERE / ".mirror" / BOT))
sys.path.insert(0, str(HERE / "stubs"))
os.environ["DATABASE_URL"] = "postgresql://stub"

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


import i18n            # noqa: E402  -- after the path setup above
import shared_features  # noqa: E402

# Every key the two documents are assembled from, in either file.
KEYS = [
    "privacy_heading", "privacy_kept_heading", "privacy_stored",
    "privacy_seen_by_heading", "privacy_seen_by", "privacy_others",
    "privacy_kept_for_heading", "privacy_kept_for", "privacy_your_choices",
    "terms_heading", "terms_use", "terms_specific", "terms_money",
    "terms_no_warranty", "policy_full_text", "policy_contact",
    "delete_data_confirm", "delete_data_consequences",
    "delete_data_button_yes", "delete_data_button_no",
    "delete_data_kept", "delete_data_done", "delete_data_failed",
]


def main() -> int:
    print(f"=== {BOT}: the policy commands ===")

    for lang in i18n.SUPPORTED_LANGUAGES:
        table = i18n.STRINGS[lang]
        missing = [key for key in KEYS if key not in table]
        check(f"{lang}: every policy string is translated", not missing, ", ".join(missing))

    for lang in i18n.SUPPORTED_LANGUAGES:
        for label, text in (("/privacy", shared_features.privacy_text(lang)),
                            ("/terms", shared_features.terms_text(lang))):
            leaked = [key for key in KEYS if key in text]
            check(f"{lang}: {label} reads as prose, not as key names",
                  not leaked, ", ".join(leaked))
            check(f"{lang}: {label} has no unfilled placeholder",
                  "{" not in text, text[max(0, text.find("{") - 30):][:60])
            # Long enough to be a notice rather than a stub. The shortest
            # honest version of either of these is several sentences.
            check(f"{lang}: {label} says something", len(text) > 400, f"{len(text)} chars")

    # The footer lines only appear once somebody has configured them, so they
    # are rendered separately rather than through the two documents above.
    for lang in i18n.SUPPORTED_LANGUAGES:
        url = i18n.t(lang, "policy_full_text", url="https://example.invalid/PRIVACY.md")
        contact = i18n.t(lang, "policy_contact", contact="@somebody")
        check(f"{lang}: the published-text line takes a URL",
              "https://example.invalid/PRIVACY.md" in url and "{" not in url, url)
        check(f"{lang}: the contact line takes a contact",
              "@somebody" in contact and "{" not in contact, contact)
        done = i18n.t(lang, "delete_data_done", rows=7)
        check(f"{lang}: the erase confirmation counts the rows",
              "7" in done and "{" not in done, done)

    # The two profile screens. Telegram enforces these limits by refusing the
    # call, which happens at startup where nobody is looking -- publish_profile
    # logs and carries on, by design, so an over-long description would simply
    # never appear and nothing would say why.
    for lang in i18n.SUPPORTED_LANGUAGES:
        for key, cap in (("bot_short_description", 120), ("bot_description", 512)):
            value = i18n.STRINGS[lang].get(key, "")
            check(f"{lang}: {key} is set", bool(value))
            check(f"{lang}: {key} is within Telegram's {cap} characters",
                  0 < len(value) <= cap, f"{len(value)} characters")
        # The profile text is the one place a claim would be read by somebody
        # who has not started the bot yet, so it is held to the same rule as
        # the notices: nothing the code does not do.
        blurb = (i18n.STRINGS[lang].get("bot_short_description", "")
                 + " " + i18n.STRINGS[lang].get("bot_description", "")).lower()
        for word in ("unlimited", "instant", "secure", "encrypted", "guaranteed",
                     "24/7", "always available", "best "):
            check(f"{lang}: the profile text does not claim {word.strip()!r}",
                  word not in blurb)

    # The notice promises /deletemydata works. Read the real db.py rather than
    # the stub the rest of this file runs against.
    source = (ROOT / BOT / "db.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    erase = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "erase_user"]
    check("db.py has erase_user", bool(erase))
    if erase:
        # Read as text rather than as SQL: the DELETEs are f-strings over a
        # loop of table names, so there is no statement to parse -- and the
        # question here is only whether each table is named at all.
        body = ast.get_source_segment(source, erase[0]) or ""
        # Whatever else a given bot keeps, these two are in every schema, and
        # they are what somebody asking "what do you know about me" means.
        for table in ("donation_prompts", "activity_events"):
            check(f"erase_user clears {table}", f'"{table}"' in body)
        # The ledger is the deliberate exception: kept, minus the sealed username.
        # AnonBot's username is sealed since 2.1.1; the other three bots' is
        # still a plain column until their own sealing lands (the plain form
        # is accepted for them only, never for AnonBot).
        cleared = "star_transactions SET username_sealed = NULL" in body or (
            BOT != "anon_bot" and "star_transactions SET username = NULL" in body)
        check("erase_user keeps the payment ledger and clears the username on it",
              cleared,
              "the privacy notice says those rows stay, without a username")

    # The command menu has to offer them, or nobody finds them.
    menu = (ROOT / BOT / "bot.py").read_text(encoding="utf-8")
    for command in ("privacy", "terms", "deletemydata"):
        check(f"/{command} is in the command menu", f'BotCommand("{command}"' in menu)

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
