# ManagerBot

[↑ bots](../README.md)

The operator's private bot: status, usage, problem reports, broadcasts, backups, restarts. Settings prefix `MBOT_`, database schema `manager_bot`. It has no texts file: it has one reader.

| File | What it is |
|---|---|
| [bot.py](bot.py) · [notes](bot.md) | the handlers and `main()`: every operator command |
| [db.py](db.py) · [notes](db.md) | ManagerBot's tables |
| [monitoring.py](monitoring.py) · [notes](monitoring.md) | ManagerBot's own logging, crash and activity tracking |
| [family_db.py](family_db.py) · [notes](family_db.md) | the whole database as an archive (`/backup`), and loading one back |
| [reserve_copies.py](reserve_copies.py) · [notes](reserve_copies.md) | the nightly and the volume's reserve copies |

Code every bot uses is in [shared/](../../shared/README.md).
