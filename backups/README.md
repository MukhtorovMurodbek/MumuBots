# Backups

`db_backup.ps1` writes here, and `.gitignore` keeps everything in this folder
out of the repository — these are real user rows, and they are never
committed.

**A version folder holds only the backups taken while working in it.** When
`BotFamily<next>` is cut from this one, the new folder starts with an empty
`backups/` rather than inheriting a copy of everything that came before.
Before v1.2.2R each new version carried its predecessor's dumps forward, so
by 1.2.2 the folder held nine zips of which two were from that version's own
work and one was a duplicate of another under a different name.

Older dumps are not deleted, they simply stay where they were taken. If you
need a backup from before this version, it is in that version's folder.
