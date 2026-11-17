# `scan_secrets.py`

[↑ scripts](README.md) · [code](scan_secrets.py)

Refuse a commit (or a CI run) that would publish a secret.

```text
python scripts/scan_secrets.py            # every tracked file (CI)
python scripts/scan_secrets.py --staged   # what is about to be committed (the pre-commit hook)
python scripts/scan_secrets.py --install  # put the pre-commit hook in this clone's .git/hooks
```

Looks for bot tokens, passwords in connection strings, age private keys, private-key blocks,
cookie lines, and files that must never be tracked (.env, key files, cookie files, Telegram
sessions). Prints where, never the value. Exit 1 when anything is found.

- `EXAMPLE_TOKENS = {"8123456789:AA"}` — the documentation's made-up example begins this way
