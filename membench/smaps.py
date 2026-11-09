import sys, re, collections
pid = sys.argv[1]
groups = collections.Counter(); counts = collections.Counter()
cur = None
for line in open(f"/proc/{pid}/smaps"):
    m = re.match(r"^([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)$", line)
    if m:
        size = (int(m.group(2), 16) - int(m.group(1), 16)) // 1024
        name = m.group(4).strip()
        if name == "[heap]": kind = "[heap] (main malloc arena)"
        elif name.startswith("["): kind = name
        elif name: kind = "file-backed"
        elif size == 8192 + 0 or size == 8188 or size == 8192: kind = "8 MB maps (thread stacks)"
        elif size in (65536, 65532) or (60000 < size <= 65536): kind = "64 MB maps (glibc thread arenas)"
        elif size == 1024: kind = "1 MB maps (pymalloc arenas)"
        else: kind = f"other anonymous ({'<1MB' if size < 1024 else '>=1MB'})"
        cur = kind
        counts[kind] += 1
        continue
    if line.startswith("Rss:") and cur:
        groups[cur] += int(line.split()[1])
for k, v in groups.most_common():
    print(f"{v/1024:8.1f} MB  in {counts[k]:4d} maps  {k}")
