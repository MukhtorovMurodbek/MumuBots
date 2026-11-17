from __future__ import annotations

import ctypes
import os
import platform
import struct
import sys

SETTING = "MUMU_SANDBOX"

PR_SET_DUMPABLE = 4
PR_SET_SECCOMP = 22
PR_SET_NO_NEW_PRIVS = 38
SECCOMP_MODE_FILTER = 2

_CREATE_RULESET, _ADD_RULE, _RESTRICT_SELF = 444, 445, 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1

_EXECUTE, _WRITE_FILE, _READ_FILE, _READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
_REMOVE_FILE, _MAKE_SOCK = 1 << 5, 1 << 9
_TRUNCATE, _IOCTL_DEV = 1 << 14, 1 << 15
_FILE_RIGHTS = _EXECUTE | _WRITE_FILE | _READ_FILE | _TRUNCATE | _IOCTL_DEV
_NET_BIND_TCP, _NET_CONNECT_TCP = 1 << 0, 1 << 1
_SCOPE_ABSTRACT_UNIX, _SCOPE_SIGNAL = 1 << 0, 1 << 1

SYSTEM_READ = ("/usr", "/lib", "/lib64", "/lib32", "/libx32", "/bin", "/sbin", "/etc", "/opt",
               "/proc", "/sys", "/var/cache", "/var/lib")
DEVICES_READ_WRITE = ("/dev/null", "/dev/zero", "/dev/full")
DEVICES_READ = ("/dev/urandom", "/dev/random")

_SYSCALLS = {
    "x86_64": (0xC000003E, 41, 101, 310, 311),
    "aarch64": (0xC00000B7, 198, 117, 270, 271),
}
_AF_INET, _AF_INET6, _AF_PACKET = 2, 10, 17
_EPERM, _EACCES = 1, 13

_libc = None


def _c():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    return _libc


def enabled(environ=None) -> bool:
    value = str((environ if environ is not None else os.environ).get(SETTING, "")).strip().lower()
    return value not in ("0", "off", "no", "false")


def _prctl(option: int, value) -> bool:
    if sys.platform != "linux":
        return False
    result = _c().prctl(ctypes.c_int(option), ctypes.c_ulong(value) if isinstance(value, int) else value,
                        ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0))
    return result == 0


def landlock_abi() -> int:
    if sys.platform != "linux" or platform.machine().lower() not in _SYSCALLS:
        return 0
    try:
        abi = _c().syscall(ctypes.c_long(_CREATE_RULESET), ctypes.c_void_p(None), ctypes.c_size_t(0),
                           ctypes.c_uint32(_CREATE_RULESET_VERSION))
    except Exception:
        return 0
    return abi if abi > 0 else 0


def _handled_fs(abi: int) -> int:
    if abi >= 5:
        return (1 << 16) - 1
    if abi >= 3:
        return (1 << 15) - 1
    if abi >= 2:
        return (1 << 14) - 1
    return (1 << 13) - 1


def _add_rule(ruleset: int, path: str, access: int) -> bool:
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        if not os.path.isdir(path):
            access &= _FILE_RIGHTS
        attr = ctypes.create_string_buffer(struct.pack("<Qi", access, fd), 12)
        return _c().syscall(ctypes.c_long(_ADD_RULE), ctypes.c_int(ruleset), ctypes.c_int(_RULE_PATH_BENEATH),
                            attr, ctypes.c_uint32(0)) == 0
    finally:
        os.close(fd)


def python_paths() -> "list[str]":
    found = {sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(os.path.realpath(sys.executable)),
             os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))}
    for entry in sys.path:
        if entry and os.path.isdir(entry):
            found.add(entry)
        elif entry and os.path.isfile(entry):
            found.add(os.path.dirname(entry))
    return sorted(path for path in found if path and os.path.isabs(path))


def _landlock(read: "list[str]", read_write: "list[str]", sockets: "list[str]" = ()) -> "str | None":
    abi = landlock_abi()
    if not abi:
        return None
    handled = _handled_fs(abi)
    fields = [handled]
    if abi >= 4:
        fields.append(_NET_BIND_TCP | _NET_CONNECT_TCP)
    if abi >= 6:
        fields.append(_SCOPE_ABSTRACT_UNIX | _SCOPE_SIGNAL)
    attr = ctypes.create_string_buffer(struct.pack("<" + "Q" * len(fields), *fields), 8 * len(fields))
    ruleset = _c().syscall(ctypes.c_long(_CREATE_RULESET), attr, ctypes.c_size_t(8 * len(fields)),
                           ctypes.c_uint32(0))
    if ruleset < 0:
        return None
    try:
        reading = _EXECUTE | _READ_FILE | _READ_DIR
        device = _READ_FILE | _WRITE_FILE | (_TRUNCATE if abi >= 3 else 0) | (_IOCTL_DEV if abi >= 5 else 0)
        for path in read:
            _add_rule(ruleset, path, reading)
        for path in DEVICES_READ:
            _add_rule(ruleset, path, _READ_FILE)
        for path in DEVICES_READ_WRITE:
            _add_rule(ruleset, path, device & handled)
        for path in read_write:
            _add_rule(ruleset, path, handled)
        for path in sockets:
            _add_rule(ruleset, path, (_MAKE_SOCK | _REMOVE_FILE) & handled)
        if not _prctl(PR_SET_NO_NEW_PRIVS, 1):
            return None
        if _c().syscall(ctypes.c_long(_RESTRICT_SELF), ctypes.c_int(ruleset), ctypes.c_uint32(0)) != 0:
            return None
    finally:
        os.close(ruleset)
    return f"Landlock {abi}"


def _filter(arch: int, socket: int, ptrace: int, readv: int, writev: int) -> bytes:
    def op(code, jt, jf, k):
        return struct.pack("<HBBI", code, jt, jf, k)

    load, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06
    allow, refuse = 0x7FFF0000, 0x00050000
    program = [
        op(load, 0, 0, 4),
        op(jeq, 1, 0, arch),
        op(ret, 0, 0, refuse | _EPERM),
        op(load, 0, 0, 0),
        op(jge, 10, 0, 0x40000000),
        op(jeq, 9, 0, ptrace),
        op(jeq, 8, 0, readv),
        op(jeq, 7, 0, writev),
        op(jeq, 1, 0, socket),
        op(ret, 0, 0, allow),
        op(load, 0, 0, 16),
        op(jeq, 3, 0, _AF_INET),
        op(jeq, 2, 0, _AF_INET6),
        op(jeq, 1, 0, _AF_PACKET),
        op(ret, 0, 0, allow),
        op(ret, 0, 0, refuse | _EACCES),
    ]
    return b"".join(program)


def _seccomp() -> "str | None":
    numbers = _SYSCALLS.get(platform.machine().lower())
    if numbers is None or sys.platform != "linux":
        return None
    code = _filter(*numbers)
    filters = ctypes.create_string_buffer(code, len(code))
    program = ctypes.create_string_buffer(struct.pack("<HxxxxxxQ", len(code) // 8, ctypes.addressof(filters)), 16)
    if not _prctl(PR_SET_NO_NEW_PRIVS, 1):
        return None
    if _c().prctl(ctypes.c_int(PR_SET_SECCOMP), ctypes.c_ulong(SECCOMP_MODE_FILTER), program,
                  ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        return None
    return "seccomp"


def no_core_dumps() -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass


def protect_this_process() -> bool:
    no_core_dumps()
    return _prctl(PR_SET_DUMPABLE, 0)


def restrict(read_write=(), read=(), sockets=()) -> "list[str]":
    if not enabled() or sys.platform != "linux":
        return []
    no_core_dumps()
    read_write = sorted({os.path.realpath(p) for p in read_write if p and os.path.exists(p)})
    readable = sorted({p for p in (*SYSTEM_READ, *python_paths(), *read) if p and os.path.exists(p)})
    sockets = sorted({p for p in sockets if p and os.path.isdir(p)})
    walls = []
    for wall in (_landlock(readable, read_write, sockets), _seccomp()):
        if wall:
            walls.append(wall)
    key = os.environ.get("DATA_KEY_FILE")
    if key and walls and walls[0].startswith("Landlock"):
        real = os.path.realpath(key)
        if any(real == p or real.startswith(p.rstrip("/") + "/") for p in (*readable, *read_write)):
            walls.append("but the data key is inside a readable folder")
    return walls


SCRIPT = os.path.realpath((lambda: 0).__code__.co_filename)
_ROOT = next(p for p in (os.path.dirname(SCRIPT), os.path.dirname(os.path.dirname(SCRIPT)),
                         os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT))))
             if os.path.isfile(os.path.join(p, "main.py")))
CODE = (os.path.join(_ROOT, "bots"), os.path.join(_ROOT, "shared"))


def wrap(argv, read_write=(), read=()) -> "list[str]":
    argv = [str(part) for part in argv]
    if not enabled() or sys.platform != "linux" or not os.path.isfile(SCRIPT):
        return argv
    wrapped = [sys.executable, "-I", "-S", SCRIPT]
    for path in read_write:
        wrapped += ["--rw", str(path)]
    for path in read:
        wrapped += ["--read", str(path)]
    return wrapped + ["--", *argv]


def describe() -> str:
    if sys.platform != "linux":
        return "not available on this system"
    if not enabled():
        return f"off ({SETTING}=0)"
    abi = landlock_abi()
    parts = [f"Landlock {abi}" if abi else "no Landlock (kernel)"]
    parts.append("seccomp" if platform.machine().lower() in _SYSCALLS else f"no seccomp ({platform.machine()})")
    return ", ".join(parts)


def main(argv) -> int:
    read_write, read, rest = [], [], list(argv)
    while rest and rest[0] != "--":
        flag = rest.pop(0)
        if flag in ("--rw", "--read") and rest:
            (read_write if flag == "--rw" else read).append(rest.pop(0))
        else:
            print(f"sandbox: unknown argument {flag!r}", file=sys.stderr)
            return 2
    if not rest or len(rest) < 2:
        print("sandbox: nothing to run (… -- program args)", file=sys.stderr)
        return 2
    command = rest[1:]
    restrict(read_write, read)
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print(f"sandbox: could not start {command[0]}: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
