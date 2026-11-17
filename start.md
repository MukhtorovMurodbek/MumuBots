# `start.sh`

[↑ MumuBots](README.md) · [code](start.sh)

What the image starts (the Dockerfile's ENTRYPOINT, under tini), before main.py.

Railway mounts a volume as root, and the bots run as their own user, `bots`: as root, a volume holding
the data key and the reserve copies could not be read or written by them. So, when the container starts as
root (this image's default; Railway's too, unless RAILWAY_RUN_UID says otherwise), this hands the volume --
RAILWAY_VOLUME_MOUNT_PATH, which Railway sets, or MUMU_VOLUME_PATH elsewhere -- to `bots`, then starts the
command as `bots` with setpriv: no capabilities, none to gain (no_new_privs), its own groups. `/` is never
handed over, whatever a setting says.

Started as any other user (`--user`, RAILWAY_RUN_UID), it changes nothing and runs the command as that user.
