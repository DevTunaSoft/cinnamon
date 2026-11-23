#!/usr/bin/env python3
"""Static memory-risk audit for the Cinnamon tree.

Usage: python3 tools/memory-audit.py [root] [out.json]

Produces a reproducible baseline: counts of allocation/lifetime patterns per
source file plus file:line evidence for growth candidates. This measures
source-level risk density -- it is NOT runtime profiling.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "memory-audit-report.json")

PATTERNS = {
    "js_unbounded_push": (r"\.push\s*\(", {".js"}),
    "js_signal_connect": (r"\.connect\s*\(", {".js"}),
    "js_timeout_add": (r"(?:Mainloop|GLib)\.timeout_add", {".js"}),
    "js_idle_add": (r"(?:Mainloop|GLib)\.idle_add", {".js"}),
    "js_full_file_read": (r"(?:load_contents|file_get_contents|read_link|\.read\s*\(\s*\))", {".js"}),
    "js_json_parse": (r"JSON\.parse\s*\(", {".js"}),
    "js_pixbuf_load": (r"(?:Pixbuf\.new_from_file|new_from_file_at_scale|TextureCache|get_texture)", {".js"}),
    "js_disconnect": (r"\.disconnect\s*\(", {".js"}),
    "js_remove_timeout": (r"remove_timeout|source_remove", {".js"}),
    "c_malloc": (r"\b(?:g_malloc|g_malloc0|g_new|g_new0|malloc|calloc|realloc)\s*\(", {".c", ".h"}),
    "c_strdup": (r"\bg_strdup\s*\(", {".c", ".h"}),
    "c_object_ref": (r"\bg_object_ref\s*\(", {".c", ".h"}),
    "c_signal_connect": (r"\bg_signal_connect\w*\s*\(", {".c", ".h"}),
    "c_hash_new": (r"\bg_hash_table_new\w*\s*\(", {".c", ".h"}),
    "c_array_new": (r"\bg_(?:ptr|byte|array)_new\w*\s*\(", {".c", ".h"}),
    "c_list_append": (r"\bg_(?:list|slist)_append\s*\(", {".c", ".h"}),
    "c_image_surface": (r"\bcairo_image_surface_create\w*\s*\(", {".c", ".h"}),
    "c_file_get_contents": (r"\bg_file_get_contents\s*\(", {".c", ".h"}),
    "py_whole_read": (r"\.read\s*\(\s*\)|load_contents|file_get_contents", {".py"}),
    "py_subprocess": (r"subprocess\.(?:Popen|check_output|run)", {".py"}),
}
COMPILED = {k: (re.compile(v[0]), v[1]) for k, v in PATTERNS.items()}

evidence = defaultdict(list)
counts = defaultdict(int)
per_file = defaultdict(lambda: defaultdict(int))
files_scanned = defaultdict(int)

for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts:
        continue
    ext = path.suffix.lower()
    if ext not in {".js", ".c", ".h", ".py"}:
        continue
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        continue
    files_scanned[ext] += 1
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    for name, (rx, exts) in COMPILED.items():
        if ext not in exts:
            continue
        for i, line in enumerate(lines, 1):
            for _ in rx.finditer(line):
                counts[name] += 1
                per_file[rel][name] += 1
                if len(evidence[name]) < 40:
                    evidence[name].append({"file": rel, "line": i, "text": line.strip()[:160]})

report = {
    "root": str(ROOT),
    "files_scanned": dict(files_scanned),
    "totals": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    "top_files": {
        name: sorted([(f, d[name]) for f, d in per_file.items() if d.get(name)],
                     key=lambda kv: -kv[1])[:12]
        for name in counts
    },
    "evidence": dict(evidence),
}
OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"files_scanned": report["files_scanned"], "totals": report["totals"]}, indent=2))
print(f"\nFull report: {OUT}")
