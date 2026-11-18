#!/usr/bin/env python3
"""Call and signal map for a Godot 4 project (static text scan; Godot is never run).

It reads .gd and .tscn files only. Folders whose name starts with '.' (.godot, .git) are always
skipped; paths under a config `exclude` prefix (default addons/) are skipped unless --all.

It matches by name, so a common name like `update` can match unrelated code:
read the hits, don't trust them blindly.
"""
import argparse
import re
import sys
from pathlib import Path

try:
    from ._config import ConfigError, check_keys, find_root, load_section, source_files, starts_with_any
except ImportError:  # run as a plain script: python3 path/to/callmap.py
    from _config import ConfigError, check_keys, find_root, load_section, source_files, starts_with_any

SECTION = "callmap"
CONFIG_KEYS = {"exclude": list}
DEFAULT_EXCLUDE = ("addons/",)

RE_FUNC = re.compile(r"^\s*(?:static\s+)?func\s+(\w+)\s*\(")
RE_SIGNAL = re.compile(r"^\s*signal\s+(\w+)")
RE_CLASS = re.compile(r"^\s*class_name\s+(\w+)")
RE_EMIT = re.compile(r"\b(\w+)\.emit\(|emit_signal\(\s*&?[\"'](\w+)[\"']")
RE_CONNECT = re.compile(r"\b(\w+)\.connect\(\s*([^)]*)|\bconnect\(\s*&?[\"'](\w+)[\"']\s*,\s*([^)]*)")
RE_TSCN_CONN = re.compile(
    r'^\[connection signal="(\w+)" from="([^"]*)" to="([^"]*)" method="(\w+)"'
)
KEYWORDS = {
    "if", "elif", "while", "for", "match", "return", "func", "print", "str", "int", "float",
    "len", "range", "await", "preload", "load", "assert", "super", "not", "and", "or",
}


class Project:
    def __init__(self, root: Path, exclude=DEFAULT_EXCLUDE):
        self.root = root
        self.exclude = tuple(exclude)
        self._cache = {}

    def files(self, ext):
        if ext not in self._cache:
            self._cache[ext] = [rel for rel in source_files(self.root, ext)
                                if not starts_with_any(rel.as_posix(), self.exclude)]
        return self._cache[ext]

    def code_lines(self, rel):
        try:
            text = (self.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for n, line in enumerate(text.splitlines(), 1):
            stripped = line.split("#", 1)[0] if '"' not in line and "'" not in line else line
            yield n, line, stripped


def callable_name(arg):
    """Last identifier of a Callable argument, e.g. `_on_hit` from `self._on_hit` or `_on_hit.bind(x)`."""
    arg = arg.split(",")[0].strip().split(".bind(")[0]  # drop flags like CONNECT_ONE_SHOT
    m = re.search(r"(\w+)\s*$", arg)
    return m.group(1) if m else arg


def show(rows, title):
    print(f"{title} ({len(rows)})")
    for r in rows:
        print(f"  {r}")
    if not rows:
        print("  none")


def cmd_calls(pr, name):
    defs, calls = [], []
    call_re = re.compile(rf"(?<![\w]){re.escape(name)}\s*\(|[\"']{re.escape(name)}[\"']|\.{re.escape(name)}\b(?!\s*\()")
    for rel in pr.files("gd"):
        for n, line, code in pr.code_lines(rel):
            m = RE_FUNC.match(code)
            if m and m.group(1) == name:
                defs.append(f"{rel}:{n}")
            elif call_re.search(code):
                calls.append(f"{rel}:{n}: {line.strip()[:110]}")
    tscn = [
        f"{rel}:{n}: {line.strip()[:110]}"
        for rel in pr.files("tscn")
        for n, line, _ in pr.code_lines(rel)
        if (m := RE_TSCN_CONN.match(line)) and m.group(4) == name
    ]
    show(defs, f"defined: {name}()")
    show(calls, "called or referenced in scripts")
    show(tscn, "connected as a method in scenes")


def cmd_signal(pr, name):
    decl, emits, conns, awaits = [], [], [], []
    for rel in pr.files("gd"):
        for n, line, code in pr.code_lines(rel):
            where = f"{rel}:{n}: {line.strip()[:110]}"
            if (m := RE_SIGNAL.match(code)) and m.group(1) == name:
                decl.append(where)
            for m in RE_EMIT.finditer(code):
                if name in (m.group(1), m.group(2)):
                    emits.append(where)
            for m in RE_CONNECT.finditer(code):
                if m.group(1) == name:
                    conns.append(f"{where}  -> {callable_name(m.group(2))}")
                elif m.group(3) == name:
                    conns.append(f"{where}  -> {callable_name(m.group(4))}")
            if re.search(rf"\bawait\b.*\b{re.escape(name)}\s*$", code.rstrip()):
                awaits.append(where)
    tscn = [
        f"{rel}:{n}: from {m.group(2) or '.'} to {m.group(3)} -> {m.group(4)}"
        for rel in pr.files("tscn")
        for n, line, _ in pr.code_lines(rel)
        if (m := RE_TSCN_CONN.match(line)) and m.group(1) == name
    ]
    show(decl, f"declared: signal {name}")
    show(emits, "emitted")
    show(conns, "connected in scripts")
    show(tscn, "connected in scenes")
    show(awaits, "awaited")
    if decl and not (conns or tscn or awaits):
        print("NOTE: declared but never connected or awaited")
    if decl and not emits:
        print("NOTE: declared but never emitted")


def defined_in(pr, rel):
    funcs, signals, cls = [], [], None
    for n, _, code in pr.code_lines(rel):
        if m := RE_FUNC.match(code):
            funcs.append((m.group(1), n))
        elif m := RE_SIGNAL.match(code):
            signals.append((m.group(1), n))
        elif m := RE_CLASS.match(code):
            cls = m.group(1)
    return funcs, signals, cls


def external_calls(pr, rel, funcs):
    """name -> list of call sites outside `rel` (method calls `.name(` and string references only)."""
    out = {name: [] for name, _ in funcs}
    pats = {name: re.compile(rf"\.{re.escape(name)}\s*\(|[\"']{re.escape(name)}[\"']") for name in out}
    for other in pr.files("gd"):
        if other == rel:
            continue
        for n, line, code in pr.code_lines(other):
            for name, pat in pats.items():
                if pat.search(code):
                    out[name].append(f"{other}:{n}: {line.strip()[:100]}")
    res_path = f"res://{rel.as_posix()}"
    scenes = [s for s in pr.files("tscn") if res_path in (pr.root / s).read_text(errors="replace")]
    for s in scenes:
        for n, line, _ in pr.code_lines(s):
            if (m := RE_TSCN_CONN.match(line)) and m.group(4) in out:
                out[m.group(4)].append(f"{s}:{n}: scene connection {m.group(1)} -> {m.group(4)}")
    return out, scenes


def cmd_file(pr, path, impact_only=False):
    rel = Path(path)
    if rel.is_absolute():
        try:
            rel = rel.resolve().relative_to(pr.root)
        except ValueError:
            print(f"not under the project root {pr.root}: {path}", file=sys.stderr)
            return 1
    if not (pr.root / rel).is_file():
        print(f"not found: {rel}", file=sys.stderr)
        return 1
    funcs, signals, cls = defined_in(pr, rel)
    ext, scenes = external_calls(pr, rel, funcs)
    if not impact_only:
        print(f"{rel}" + (f"  (class_name {cls})" if cls else ""))
        show([f"{s}  line {n}" for s, n in signals], "signals declared")
        emits, conns = [], []
        for n, line, code in pr.code_lines(rel):
            for m in RE_EMIT.finditer(code):
                emits.append(f"line {n}: {m.group(1) or m.group(2)}")
            for m in RE_CONNECT.finditer(code):
                sig = m.group(1) or m.group(3)
                cb = callable_name(m.group(2) if m.group(1) else m.group(4))
                if sig not in KEYWORDS:
                    conns.append(f"line {n}: {sig} -> {cb}")
        show(emits, "emits")
        show(conns, "connects")
        show([str(s) for s in scenes], "scenes using this script")
        print(f"functions defined ({len(funcs)}); outside uses:")
        for name, n in funcs:
            print(f"  {name}()  line {n}: {len(ext[name])} outside use(s)")
        return 0
    for name, n in funcs:
        hits = ext[name]
        if hits:
            show(hits, f"{name}()  (line {n})")
    unused = [f"{name}()  line {n}" for name, n in funcs if not ext[name] and not name.startswith("_")]
    show(unused, "public functions with no outside use (may be called internally or by name)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="godot-callmap",
        description="Who calls what in a Godot 4 project: functions, signals and scene connections, "
                    "by a static text scan of .gd and .tscn files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="commands:\n"
               "  calls <func>      who defines and who calls a function\n"
               "  signal <name>     where a signal is declared, emitted, connected, awaited\n"
               "  file <path.gd>    what a script defines, emits and connects, and who uses its functions\n"
               "  impact <path.gd>  every call site outside the file for each function it defines\n\n"
               "Paths are relative to the project root. Matching is by name: read the hits, "
               "don't trust them blindly.")
    p.add_argument("command", choices=("calls", "signal", "file", "impact"))
    p.add_argument("target", help="function name, signal name, or script path")
    p.add_argument("--root", help="Godot project folder (default: nearest folder with project.godot "
                                  "at or above the current directory)")
    p.add_argument("--config", help=f"config file (default: <root>/godot-code-gates.toml, table [{SECTION}])")
    p.add_argument("--all", action="store_true", help="include excluded folders (addons/ by default)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else find_root(Path.cwd())
    try:
        table = load_section(root, SECTION, args.config)
        check_keys(table, CONFIG_KEYS, SECTION)
    except ConfigError as e:
        print(f"callmap: config error: {e}", file=sys.stderr)
        return 2
    pr = Project(root, () if args.all else table.get("exclude", DEFAULT_EXCLUDE))
    if args.command == "calls":
        cmd_calls(pr, args.target)
    elif args.command == "signal":
        cmd_signal(pr, args.target)
    elif args.command == "file":
        return cmd_file(pr, args.target)
    else:
        return cmd_file(pr, args.target, impact_only=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
