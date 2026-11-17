# shared/security

[↑ shared](../README.md)

How the bots keep what they hold about people, and their own keys, out of reach. This page is the public
summary: the notes on these files and the operator's runbooks are kept privately, outside this repository. A
vulnerability is reported privately: [SECURITY.md](../../.github/SECURITY.md).

| File | What it is |
|---|---|
| [vault.py](vault.py) | sealing personal data with the data key: keyed pseudonyms for ids, AES-256-GCM for what must be read back |
| [offline_key.py](offline_key.py) | encrypting backups to an offline key (the standard age format); the bots hold only its public half |
| [reserve.py](reserve.py) | the erasure notes that keep a restored copy from bringing back someone who used `/deletemydata` |
| [sandbox.py](sandbox.py) | the walls around the programs that open strangers' files |
| [redact.py](redact.py) | secrets scrubbed from logs, alerts and problem reports |

**Personal data at rest.** Anything that identifies a person is sealed before it is stored. Ids that are only
compared become keyed pseudonyms (HMAC-SHA256); what has to be read back (an id a message must be delivered to,
a username on a payment, saved conversation state) is encrypted with AES-256-GCM, each kind under its own
purpose. The keys come from one data key, which lives in a file on the server's volume and never in an
environment variable of the hosting platform, so a copy of the database alone shows no names.

**Backups.** Every reserve copy and every backup sent to the operator is encrypted to an offline key (X25519,
in the standard `age` format). The bots hold only the public half, so nothing on the server can open a copy.
In production a backup that cannot be encrypted is not sent, unless the operator explicitly allows plain
backups. No copy older than 30 days is restored, and
the erasure notes make a restore forget again everyone who used `/deletemydata` since the copy was made.

**Strangers' files.** Files people send are opened only by programs walled in for that one job: ffmpeg and
ffprobe, LibreOffice, and the conversion worker. They run with no secrets in their environment, can read only
the system's own folders and the job's folder and write only the latter, and cannot open internet connections
or trace other processes (Landlock and a seccomp filter, on Linux). A conversion that runs past its time limit
is killed. Archives are checked against path tricks and zip bombs before they are opened.

**The running bots.** Tokens, the database address and the keys are taken out of the process environment at
start, so no program the bots start inherits them; the process cannot be read or traced by other processes of
the same user and leaves no core dump. Logs, alerts and problem reports have secrets removed before they are
written or sent. Connections to a database that is not on the same machine must be encrypted (TLS). The image
starts as root only to hand everything to an unprivileged user with no capabilities.

**Fetching links.** DownloaderBot fetches only public addresses: every name is resolved and checked, the
connection goes to the checked address, and every redirect is checked again.

**The repository.** A commit or a CI run that would publish a token, a key or a cookie file is refused
([scripts/scan_secrets.py](../../scripts/scan_secrets.py)); CI pins its actions by commit and audits the
dependencies.
