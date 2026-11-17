# scripts

[↑ MumuBots](../README.md)

Programs the operator runs by hand. None of them is part of a running bot.

| File | What it is |
|---|---|
| [scan_secrets.py](scan_secrets.py) · [notes](scan_secrets.md) | refuses a commit or a CI run that would publish a secret; `--install` puts it in a clone's pre-commit hook |

[security/](security/README.md): the scripts that handle the keys and the reserve copies.
