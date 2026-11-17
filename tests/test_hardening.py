import asyncio
import json
import logging
import os

import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from conftest import ROOT, load

import main

sandbox = main.own_module("sandbox")
redact = main.own_module("redact")
LINUX = sys.platform == "linux"
LANDLOCK = LINUX and sandbox.landlock_abi() > 0
SECCOMP = LINUX and sandbox.describe().find("seccomp") >= 0 and "no seccomp" not in sandbox.describe()

PROBE = textwrap.dedent("""
    import json, os, socket, sys
    out = {}
    def attempt(name, fn):
        try:
            fn(); out[name] = "allowed"
        except Exception as exc:
            out[name] = type(exc).__name__
    attempt("secret", lambda: open(sys.argv[1]).read())
    attempt("own", lambda: open(os.path.join(sys.argv[2], "out.txt"), "w").write("ok"))
    attempt("parent_environ", lambda: open("/proc/%d/environ" % os.getppid(), "rb").read())
    attempt("tcp", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).close())
    attempt("udp", lambda: socket.socket(socket.AF_INET6, socket.SOCK_DGRAM).close())
    attempt("unix", lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).close())
    print(json.dumps(out))
""")


@pytest.fixture
def places(tmp_path):
    secret_dir, job = tmp_path / "volume", tmp_path / "job"
    secret_dir.mkdir()
    job.mkdir()
    secret = secret_dir / "data_key"
    secret.write_text("not for strangers\n")
    return secret, job


def _walled(secret, job):
    argv = sandbox.wrap([sys.executable, "-c", PROBE, str(secret), str(job)], [str(job)])
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not LANDLOCK, reason="this kernel has no Landlock")
def test_a_walled_in_program_keeps_to_its_own_folder(places):
    secret, job = places
    seen = _walled(secret, job)
    assert seen["own"] == "allowed" and (job / "out.txt").read_text() == "ok"
    assert seen["secret"] == "PermissionError"
    assert seen["parent_environ"] == "PermissionError"


@pytest.mark.skipif(not SECCOMP, reason="no seccomp filter for this machine")
def test_a_walled_in_program_has_no_internet_sockets(places):
    seen = _walled(*places)
    assert seen["tcp"] == "PermissionError" and seen["udp"] == "PermissionError"
    assert seen["unix"] == "allowed"


def test_the_walls_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("MUMU_SANDBOX", "0")
    assert sandbox.wrap(["ffprobe", "x"], ["/tmp"]) == ["ffprobe", "x"]
    assert sandbox.restrict(["/tmp"]) == []


@pytest.mark.skipif(not LINUX, reason="Linux only")
def test_a_protected_process_hides_its_environment_from_its_own_children():
    code = textwrap.dedent(f"""
        import os, subprocess, sys
        sys.path.insert(0, {str(ROOT / "shared" / "security")!r})
        import sandbox
        assert sandbox.protect_this_process()
        child = "import os\\ntry:\\n    open('/proc/%d/environ' % os.getppid(), 'rb').read(); print('read')\\nexcept PermissionError:\\n    print('refused')"
        print(subprocess.run([sys.executable, "-c", child], capture_output=True, text=True).stdout.strip())
    """)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, MUMU_TEST_SECRET="x" * 20))
    assert done.stdout.strip() == "refused", done.stderr


def test_secrets_leave_the_environment_but_still_reach_the_bots(monkeypatch):
    monkeypatch.setattr(main, "_SECRETS", {})
    monkeypatch.setenv("SBOT_TOKEN", "123456:" + "A" * 35)
    monkeypatch.setenv("CBOT_PAYMENT_PROVIDER_TOKEN_USD", "provider-token-123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "f" * 32)
    monkeypatch.setenv("DBOT_YT_PROXY", "http://user:pass@proxy.example:8080")
    monkeypatch.setenv("FLOOD_UPDATES_PER_MINUTE", "40")
    real = os.environ["DATABASE_URL"]
    try:
        main._hold_secrets()
        for name in ("SBOT_TOKEN", "CBOT_PAYMENT_PROVIDER_TOKEN_USD", "TELEGRAM_API_HASH",
                     "DBOT_YT_PROXY", "DATABASE_URL"):
            assert name not in os.environ
        assert os.environ["FLOOD_UPDATES_PER_MINUTE"] == "40"
        bot = next(b for b in main.discover()[0] if b.folder == "sticker_bot")
        assert main.setting(bot, "SBOT_TOKEN") == "123456:" + "A" * 35
        assert main.setting(bot, "DATABASE_URL") == real
        child = subprocess.run([sys.executable, "-c", "import os; print(sorted(os.environ))"],
                               capture_output=True, text=True).stdout
        assert "SBOT_TOKEN" not in child and "DATABASE_URL" not in child
        command, env = main.child_command([bot])
        assert env["SBOT_TOKEN"] == "123456:" + "A" * 35
    finally:
        os.environ["DATABASE_URL"] = real


def test_secret_settings_are_told_apart():
    for name in ("SBOT_TOKEN", "DATABASE_URL", "LOCAL_DATABASE_URL", "TELEGRAM_API_HASH", "DBOT_COBALT_API_KEY",
                 "DBOT_PROXY", "DBOT_YT_PROXY", "SBOT_PAYMENT_PROVIDER_TOKEN_USD", "DATA_KEY", "DATA_KEY_INIT"):
        assert main.is_secret_setting(name), name
    for name in ("SBOT_USERNAME", "ADMIN_ID", "BACKUP_PUBLIC_KEY", "DATA_KEY_FILE", "DBOT_COOKIES_FILE",
                 "TELEGRAM_API_ID", "FLOOD_UPDATES_PER_MINUTE"):
        assert not main.is_secret_setting(name), name


def test_a_remote_database_is_reached_over_tls(monkeypatch):
    monkeypatch.delenv("DATABASE_SSLMODE", raising=False)
    monkeypatch.delenv("DATABASE_SSLROOTCERT", raising=False)
    remote = "postgresql://postgres.ref:pw@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
    assert main.with_tls(remote) == remote + "?sslmode=require"
    assert main.with_tls(remote + "?application_name=x") == remote + "?application_name=x&sslmode=require"
    assert main.with_tls(remote + "?sslmode=disable") == remote + "?sslmode=disable"
    for local in ("postgresql://postgres@localhost:5433/postgres", "postgresql://p:p@127.0.0.1/db"):
        assert main.with_tls(local) == local
    monkeypatch.setenv("DATABASE_SSLROOTCERT", "/etc/ssl/supabase.crt")
    assert main.with_tls(remote) == remote + "?sslmode=verify-full&sslrootcert=%2Fetc%2Fssl%2Fsupabase.crt"
    monkeypatch.setenv("DATABASE_SSLMODE", "prefer")
    monkeypatch.delenv("DATABASE_SSLROOTCERT")
    assert main.with_tls(remote) == remote + "?sslmode=prefer"


def test_secrets_are_taken_out_of_text(monkeypatch):
    monkeypatch.setattr(main.FAMILY_PROCESS, "secret_values", frozenset({"cookie-value-0123456789"}), raising=False)
    tail = "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    said = (f"GET https://api.telegram.org/bot123456789:{tail}/getMe failed; "
            "postgresql://postgres.ref:hunter2hunter2@db.example:5432/postgres; api_hash=0123456789abcdef; "
            "AGE-SECRET-KEY-1" + "Q" * 58 + "; cookie cookie-value-0123456789")
    cleaned = redact.redact(said)
    for secret in (tail, "hunter2hunter2", "0123456789abcdef",
                   "AGE-SECRET-KEY-1", "cookie-value-0123456789"):
        assert secret not in cleaned, secret
    assert "db.example:5432" in cleaned and "api.telegram.org" in cleaned


def test_the_log_filter_cleans_a_traceback_too():
    try:
        raise RuntimeError("connect to postgresql://u:topsecretpw@db.example/x failed")
    except RuntimeError:
        record = logging.getLogger("t").makeRecord("t", logging.ERROR, __file__, 1, "token %s",
                                                   ("123456789:" + "B" * 35,), sys.exc_info())
    assert redact.RedactingFilter().filter(record)
    text = logging.Formatter().format(record)
    assert "topsecretpw" not in text and "B" * 35 not in text and "[redacted]" in text


def test_strangers_stop_at_managerbots_outer_wall(monkeypatch):
    bot = load("manager_bot", "bot")
    from telegram.ext import ApplicationHandlerStop
    monkeypatch.setattr(bot, "ADMIN_IDS", {42})
    guarded = []

    async def guard(update, context):
        guarded.append(update.effective_user.id)
        return False

    monkeypatch.setattr(bot, "guard", guard)
    answered = []

    async def answer(*args, **kwargs):
        answered.append(True)

    operator = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=object(), callback_query=None)
    assert asyncio.run(bot.strangers_stop_here(operator, None)) is None
    for update in (SimpleNamespace(effective_user=SimpleNamespace(id=7), message=object(), callback_query=None),
                   SimpleNamespace(effective_user=SimpleNamespace(id=7), message=None,
                                   callback_query=SimpleNamespace(answer=answer)),
                   SimpleNamespace(effective_user=None, message=None, callback_query=None)):
        with pytest.raises(ApplicationHandlerStop):
            asyncio.run(bot.strangers_stop_here(update, None))
    assert guarded == [7] and answered == [True]


def test_no_plain_copy_of_the_database_goes_to_telegram_in_production(monkeypatch):
    bot = load("manager_bot", "bot")
    offline_key = load("manager_bot", "offline_key")
    monkeypatch.delenv("BACKUP_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(offline_key.OfflineKeyError):
        bot._for_telegram(b"PK...", {"size": 5}, "copy.zip")
    monkeypatch.setenv("MBOT_ALLOW_PLAIN_BACKUPS", "1")
    data, _, name, line = bot._for_telegram(b"PK...", {"size": 5}, "copy.zip")
    assert data == b"PK..." and name == "copy.zip" and "Not encrypted" in line


@pytest.mark.skipif(not LANDLOCK, reason="this kernel has no Landlock")
def test_the_walled_in_worker_still_converts_office_files(tmp_path):
    office = load("convert_bot", "office_convert")
    if not office.soffice_path():
        pytest.skip("LibreOffice is not installed")
    from docx import Document
    document = Document()
    document.add_paragraph("Walls up, and the document still converts.")
    source = tmp_path / "in.docx"
    document.save(source)
    job = {"in_paths": [str(source)], "src_ext": "docx", "target_ext": "pdf", "work_dir": str(tmp_path),
           "stem": "walls", "max_pixels": 50_000_000, "max_memory_mb": 0}
    done = subprocess.run([sys.executable, "-u", str(ROOT / "main.py"), "--module", "convert_bot.convert_worker",
                           json.dumps(job)], capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    outcome = json.loads([line for line in done.stdout.splitlines() if line.strip()][-1])
    assert outcome["ok"], (outcome, done.stderr[-2000:])
    assert open(outcome["path"], "rb").read(5) == b"%PDF-"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("walls-") and p.name.endswith("-contents")]
