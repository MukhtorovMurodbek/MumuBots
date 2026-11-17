# scripts/security

[↑ scripts](../README.md)

The scripts that handle the keys and the reserve copies. Each one says how it is run with `--help`; the notes
on them and the runbooks they belong to are kept privately, outside this repository. What the keys are for is
summed up in [shared/security/](../../shared/security/README.md).

| File | What it is |
|---|---|
| [backup_key.py](backup_key.py) | makes the offline key pair for backups, or prints the public half of one |
| [encrypt_personal_data.py](encrypt_personal_data.py) | the one-off migration that sealed personal data already stored in plain form |
| [rotate_data_key.py](rotate_data_key.py) | re-seals everything under a new data key, in one transaction |
| [restore_reserve.py](restore_reserve.py) | restores the database from a reserve copy, and forgets again whoever erased since |
