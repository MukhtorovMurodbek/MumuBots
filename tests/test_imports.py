"""Import every bot module for real (telegram and the database stubbed out).
Catches what pyflakes cannot: anything that only fails when the module body
actually runs."""
import importlib, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = sys.argv[1]
sys.path.insert(0, os.path.join(HERE, ".mirror", BOT))
sys.path.insert(0, os.path.join(HERE, "stubs"))

for var in ("SBOT", "CBOT", "DBOT", "ABOT", "MBOT"):
    os.environ[f"{var}_TOKEN"] = "test"
    os.environ[f"{var}_USERNAME"] = "test"
    os.environ[f"{var}_ADMIN_ID"] = "1"
os.environ["DATABASE_URL"] = "postgresql://stub"
os.environ["MBOT_REGISTRY"] = ""

names = [p[:-3] for p in sorted(os.listdir(os.path.join(HERE, ".mirror", BOT))) if p.endswith(".py")]
skip = {"db", "db_merge"}          # db is stubbed; db_merge is an offline tool
bad = []
for name in names:
    if name in skip:
        continue
    try:
        importlib.import_module(name)
        print(f"  ok  {BOT}/{name}.py")
    except Exception as exc:
        bad.append(f"{BOT}/{name}.py: {type(exc).__name__}: {exc}")
        print(f"  !!  {BOT}/{name}.py: {type(exc).__name__}: {exc}")
sys.exit(1 if bad else 0)
