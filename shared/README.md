# shared

[↑ MumuBots](../README.md)

Code more than one bot uses. Each bot gets its own copy of every module here when it loads (main.py's
`_BotFinder`), with its own globals and state, and imports it by bare name (`import family_link`).
ManagerBot does not get `shared_features` (`NOT_FOR` in main.py).

| File | What it is |
|---|---|
| [family_link.py](family_link.py) · [notes](family_link.md) | the family bus: commands and events between ManagerBot and the others, the ⚡ wallet, heartbeats, usage |
| [shared_features.py](shared_features.py) · [notes](shared_features.md) | what every public bot does the same way: `/donate`, `/balance`, `/privacy`, `/feedback`, the flood limit, monitoring |
| [lifecycle.py](lifecycle.py) · [notes](lifecycle.md) | surviving a redeploy: saving and handing over work in progress |
| [problems.py](problems.py) · [notes](problems.md) | every problem a person can run into, by code, with its text and what the operator is told |
| [live_message.py](live_message.py) · [notes](live_message.md) | one progress message that keeps updating while it is the last thing in the chat |
| [big_files.py](big_files.py) · [notes](big_files.md) | files past the Bot API's limits, moved over MTProto |
| [pricing.py](pricing.py) · [notes](pricing.md) | what a job costs the machine, and what it is sold for in ⚡ |
| [resources.py](resources.py) · [notes](resources.md) | what the machine allows, and what each bot may count on |

[security/](security/README.md): the data key, the offline key for backups, the erasure notes, the walls around
programs that open strangers' files, and the scrubbing of secrets from logs.
