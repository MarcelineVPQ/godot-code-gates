#!/usr/bin/env python3
"""Loop gate for Godot 4 projects: every for/while loop that can run per frame (or per turn, in
folders you mark always-hot) carries a written verdict, and --check fails on one without.

HOT means:
  * every loop in an always-hot folder (config `always-hot`, empty by default): code there runs
    often enough that every loop needs a verdict (for example a turn-based game's rules folder);
  * every loop in a per-frame or per-event root function (config `root-funcs`; by default
    _process, _physics_process, _integrate_forces, _draw, _input, _shortcut_input,
    _unhandled_input, _unhandled_key_input, _gui_input, _process_modification and
    _process_modification_with_delta) and in what it calls, `depth` (2) calls deep, found by name:
    bare `foo(` -> foo in the same file if defined there, else every project def of foo; `x.foo(`
    -> every project def of foo (names defined in more than `max-defs` files are skipped);
    `sig.emit(` -> callbacks connected to it. Name matching over-reports, the safe direction for
    a gate; calls through stored Callables, call("name"), tweens and timers are missed.
  Only .gd files under an `include` prefix and not under an `exclude` prefix are scanned.
Hidden iteration (.any/.filter/.map/.sort_custom) is not a loop here; the `flags` column of the
listing points at it inside loop bodies.

The verdict file (config `verdicts`, default hot_loops.tsv): a header line, then one row per loop:
key, verdict, reason (tab-separated). Verdicts: OK (cheap enough at its call rate; the reason
says why), FIX (a known cost to remove), MEASURE (cost grows with content size and nobody has
measured it). `key` = path::function::loop header (whitespace collapsed), plus #n for repeats in
one function: it survives line moves. A new or rewritten loop needs a row; rows for loops that
are gone are printed as stale (not a failure) so the file can be trimmed.

Config: godot-code-gates.toml in the project root, table [hot-loops]. Paths are relative to the
project root (the nearest folder holding project.godot, or --root).
"""
import argparse
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from pathlib import Path

try:
    from ._config import ConfigError, check_keys, find_root, load_section, source_files, starts_with_any
except ImportError:  # run as a plain script: python3 path/to/list_hot_loops.py
    from _config import ConfigError, check_keys, find_root, load_section, source_files, starts_with_any

SECTION = "hot-loops"
VERDICTS = ("OK", "FIX", "MEASURE")
DEFAULT_ROOT_FUNCS = (
    "_process", "_physics_process", "_integrate_forces", "_draw", "_input", "_shortcut_input",
    "_unhandled_input", "_unhandled_key_input", "_gui_input",
    # SkeletonModifier3D: runs every frame per skeleton.
    "_process_modification", "_process_modification_with_delta",
)
# Worth a look inside a hot loop body (listing only; the gate ignores them).
DEFAULT_FLAGS = {
    "get_node": r"\bget_node(_or_null)?\(|\bget_children\(|\bfind_child(ren)?\(",
    "load": r"(?<![\w.])load\(|ResourceLoader\.load",
    "new": r"\.new\(|\binstantiate\(",
    "alloc": r"(=|\(|,|return)\s*(\[|\{)|\b(Array|Dictionary|Packed\w+Array)\(",
    "duplicate": r"\.duplicate\(",
    "hidden_loop": r"\.(filter|map|reduce|any|all|sort|sort_custom)\(",
    "str_fmt": r"\bstr\(|\"\s*%\s*|\.format\(",
    "emit": r"\.emit\(|emit_signal\(",
}
CONFIG_KEYS = {
    "include": list, "exclude": list, "always-hot": list, "root-funcs": list,
    "extra-root-funcs": list, "depth": int, "max-defs": int, "flags": dict, "extra-flags": dict,
    "verdicts": str,
}


@dataclass(frozen=True)
class Settings:
    include: tuple = ("",)
    exclude: tuple = ("addons/",)
    always_hot: tuple = ()
    root_funcs: frozenset = frozenset(DEFAULT_ROOT_FUNCS)
    depth: int = 2
    max_defs: int = 4  # `.foo(` with foo defined in more files than this is too generic to follow
    flags: tuple = field(default_factory=lambda: tuple((k, re.compile(v)) for k, v in DEFAULT_FLAGS.items()))
    verdicts: str = "hot_loops.tsv"


def settings_from(table: dict) -> Settings:
    check_keys(table, CONFIG_KEYS, SECTION)
    s = Settings()
    flags = dict(table["flags"]) if "flags" in table else dict(DEFAULT_FLAGS)
    flags.update(table.get("extra-flags", {}))
    try:
        compiled = tuple((k, re.compile(v)) for k, v in flags.items())
    except re.error as e:
        raise ConfigError(f"[{SECTION}] bad flag regex: {e}") from e
    return replace(
        s,
        include=tuple(table.get("include", s.include)),
        exclude=tuple(table.get("exclude", s.exclude)),
        always_hot=tuple(table.get("always-hot", s.always_hot)),
        root_funcs=frozenset(table.get("root-funcs", DEFAULT_ROOT_FUNCS)) | set(table.get("extra-root-funcs", [])),
        depth=table.get("depth", s.depth),
        max_defs=table.get("max-defs", s.max_defs),
        flags=compiled,
        verdicts=table.get("verdicts", s.verdicts),
    )


RE_FUNC = re.compile(r"^(\s*)(?:static\s+)?func\s+(\w+)\s*\(")
RE_LOOP = re.compile(r"^(\s*)(for|while)\b")
RE_CALL = re.compile(r"(?<![\w.])(\w+)\s*\(")
RE_MCALL = re.compile(r"\.(\w+)\s*\(")
RE_EMIT = re.compile(r"\b(\w+)\.emit\(|emit_signal\(\s*&?[\"'](\w+)[\"']")
RE_CONNECT = re.compile(r"\b(\w+)\.connect\(\s*([^)]*)|\bconnect\(\s*&?[\"'](\w+)[\"']\s*,\s*([^)]*)")
RE_STR = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
GD_KEYWORDS = {
    "if", "elif", "while", "for", "match", "return", "func", "not", "and", "or", "in",
    "await", "assert", "super", "preload", "print", "push_error", "push_warning",
}


def strip_comment(line: str) -> str:
    out, q = [], None
    for i, ch in enumerate(line):
        if q:
            out.append(ch)
            if ch == q and line[i - 1] != "\\":
                q = None
        elif ch in "\"'":
            q = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def indent_of(s: str) -> int:
    return len(s) - len(s.lstrip("\t "))  # one char per level (tabs, Godot's default)


def parse(text: str):
    """-> (funcs by name {name: [(start, end)]}, code {line: code}, loops [(line, func, header, end)])."""
    lines = text.splitlines()
    code = {n: strip_comment(l) for n, l in enumerate(lines, 1)}
    funcs = []  # (name, start, end)
    stack = []  # (name, indent, start)
    for n in range(1, len(lines) + 1):
        c = code[n]
        if not c.strip():
            continue
        ind = indent_of(c)
        if c.strip().startswith(")"):
            continue  # the close of a multi-line signature or call never ends a function
        while stack and ind <= stack[-1][1]:
            name, _, start = stack.pop()
            funcs.append((name, start, last_code_line(code, start, n)))
        m = RE_FUNC.match(c)
        if m:
            stack.append((m.group(2), ind, n))
    while stack:
        name, _, start = stack.pop()
        funcs.append((name, start, last_code_line(code, start, len(lines) + 1)))
    by_name = defaultdict(list)
    for name, start, end in funcs:
        by_name[name].append((start, end))
    loops = []
    for name, start, end in funcs:
        n = start + 1
        while n <= end:
            c = code[n]
            if c.strip() and RE_LOOP.match(c) and owner(funcs, n) == (name, start):
                header, last = c.strip(), n
                while not code[last].rstrip().endswith(":") and last < end:  # multi-line header
                    last += 1
                    header += " " + code[last].strip()
                loops.append((n, name, " ".join(header.split()), block_end(code, last, indent_of(c), end)))
            n += 1
    return by_name, code, loops


def last_code_line(code: dict, start: int, before: int) -> int:
    n = before - 1
    while n > start and not code[n].strip():
        n -= 1
    return n


def block_end(code: dict, n: int, ind: int, limit: int) -> int:
    j, last = n + 1, n
    while j <= limit:
        c = code[j]
        if c.strip():
            if indent_of(c) <= ind:
                break
            last = j
        j += 1
    return last


def owner(funcs: list, n: int):
    """Innermost named function holding line n (a lambda `func(` stays with its parent)."""
    best = None
    for name, start, end in funcs:
        if start <= n <= end and (best is None or start > best[1]):
            best = (name, start)
    return best


def calls_in(code: dict, a: int, b: int, own_defs: dict, all_defs: dict, max_defs: int) -> set:
    out = set()
    for n in range(a, b + 1):
        c = RE_STR.sub('""', code.get(n, ""))  # no calls inside string literals
        for m in RE_CALL.finditer(c):
            nm = m.group(1)
            if nm in GD_KEYWORDS:
                continue
            if nm in own_defs:
                out.add(("local", nm))
            elif nm in all_defs and len(all_defs[nm]) <= max_defs:
                out.add(("global", nm))
        for m in RE_MCALL.finditer(c):
            nm = m.group(1)
            if nm in all_defs and len(all_defs[nm]) <= max_defs:
                out.add(("global", nm))
        for m in RE_EMIT.finditer(c):
            out.add(("signal", m.group(1) or m.group(2)))
    return out


def hot_label(prefix: str) -> str:
    """`entry` column for an always-hot folder: its last path segment (scripts/rules/ -> rules)."""
    return prefix.rstrip("/").rsplit("/", 1)[-1] or "always-hot"


def in_scope(rel: str, s: Settings) -> bool:
    return starts_with_any(rel, s.include) and not starts_with_any(rel, s.exclude)


def scan(sources: dict, s: Settings) -> list:
    """sources: {path from project root: text}. -> hot loop rows (dicts), in path and line order."""
    files = {rel: parse(text) for rel, text in sorted(sources.items()) if in_scope(rel, s)}
    all_defs = defaultdict(set)
    for rel, (by_name, _, _) in files.items():
        for nm in by_name:
            all_defs[nm].add(rel)
    connects = defaultdict(set)  # signal -> callback names
    for _, code, _ in files.values():
        for c in code.values():
            for m in RE_CONNECT.finditer(c):
                sig = m.group(1) or m.group(3)
                arg = ((m.group(2) if m.group(1) else m.group(4)) or "").split(",")[0].strip().split(".bind(")[0]
                cb = re.search(r"(\w+)\s*$", arg)
                if cb and cb.group(1) in all_defs:
                    connects[sig].add(cb.group(1))

    def edges(rel: str, fname: str) -> set:
        by_name, code, _ = files[rel]
        out = set()
        for start, end in by_name.get(fname, []):
            for kind, nm in calls_in(code, start + 1, end, by_name, all_defs, s.max_defs):
                if kind == "local":
                    out.add((rel, nm))
                elif kind == "global":
                    out.update((r, nm) for r in all_defs[nm])
                else:
                    for cb in connects.get(nm, ()):
                        if len(all_defs[cb]) <= s.max_defs:
                            out.update((r, cb) for r in all_defs[cb])
        return out

    reach = {}  # (rel, func) -> (depth, entry label, via)
    roots = [(rel, nm) for rel, (by_name, _, _) in files.items() for nm in by_name if nm in s.root_funcs]
    for root in roots:
        label = f"{root[0].rsplit('/', 1)[-1]}:{root[1]}"
        q = deque([(root, 0, root[1])])
        while q:
            node, d, via = q.popleft()
            if node in reach and reach[node][0] <= d:
                continue
            reach[node] = (d, label, via)
            if d < s.depth:
                for nxt in edges(*node):
                    q.append((nxt, d + 1, f"{via}>{nxt[1]}"))

    rows, seen = [], defaultdict(int)
    for rel, (by_name, code, loops) in files.items():
        hot_dir = next((p for p in s.always_hot if rel.startswith(p)), None)
        for n, fname, header, body_end in loops:
            if hot_dir is not None:
                entry, d, via = hot_label(hot_dir), 0, fname
            elif (rel, fname) in reach:
                d, entry, via = reach[(rel, fname)]
            else:
                continue
            body = "\n".join(code[i] for i in range(n, body_end + 1))
            base = f"{rel}::{fname}::{header}"
            seen[base] += 1
            rows.append({
                "key": base + (f"#{seen[base]}" if seen[base] > 1 else ""), "where": f"{rel}:{n}",
                "function": fname, "entry": entry, "depth": d, "via": via, "header": header,
                "flags": ",".join(f for f, rx in s.flags if rx.search(body)),
                "callees": ",".join(sorted({nm for _, nm in calls_in(code, n, body_end, by_name, all_defs,
                                                                     s.max_defs)})),
            })
    return rows


def read_sources(root: Path, s: Settings) -> dict:
    out = {}
    for p in source_files(root, "gd"):
        rel = p.as_posix()
        if in_scope(rel, s):
            out[rel] = (root / p).read_text(encoding="utf-8", errors="replace")
    return out


def parse_verdicts(text: str) -> dict:
    """key -> (verdict, reason); rows after the header, tab-separated."""
    out = {}
    for line in text.splitlines()[1:]:
        if line.strip():
            cols = line.split("\t")
            out[cols[0]] = (cols[1].strip() if len(cols) > 1 else "", cols[2].strip() if len(cols) > 2 else "")
    return out


def check_rows(rows: list, verdict_text: str) -> tuple:
    """-> (problems, stale keys, counts by verdict)."""
    verdicts = parse_verdicts(verdict_text)
    current = {r["key"] for r in rows}
    problems, counts = [], defaultdict(int)
    for r in rows:
        v, reason = verdicts.get(r["key"], (None, ""))
        if v is None:
            problems.append(f"NO VERDICT\t{r['where']}\t{r['key']}")
        elif v not in VERDICTS:
            problems.append(f"UNKNOWN VERDICT '{v}'\t{r['where']}\t{r['key']}")
        elif not reason:
            problems.append(f"NO REASON\t{r['where']}\t{r['key']}")
        else:
            counts[v] += 1
    return problems, sorted(k for k in verdicts if k not in current), counts


def list_rows(rows: list) -> None:
    cols = ["key", "where", "function", "entry", "depth", "via", "header", "flags", "callees"]
    print("\t".join(cols))
    for r in rows:
        print("\t".join(str(r[c]) for c in cols))


def check(root: Path, verdict_file: str, s: Settings) -> int:
    path = root / verdict_file
    if not path.is_file():
        print(f"list_hot_loops: verdict file not found: {path} (create it with the header line "
              f"'key<TAB>verdict<TAB>reason')")
        return 2
    rows = scan(read_sources(root, s), s)
    problems, stale, counts = check_rows(rows, path.read_text(encoding="utf-8"))
    for p in problems:
        print(p)
    for k in stale:
        print(f"STALE (loop gone or its function renamed)\t{k}")
    summary = ", ".join(f"{v} {counts[v]}" for v in VERDICTS)
    print(f"list_hot_loops: {len(rows)} hot loops ({summary}), {len(problems)} without a valid verdict, "
          f"{len(stale)} stale row(s).")
    return 1 if problems else 0


def self_check() -> int:
    """Planted cases with a fixed layout (scripts/rules/ always hot, scripts/tests/ and
    scripts/tools/ excluded), independent of any project config."""
    s = replace(Settings(), include=("scripts/",), exclude=("scripts/tests/", "scripts/tools/"),
                always_hot=("scripts/rules/",))
    hdr = "key\tverdict\treason\n"
    rules_loop = "extends RefCounted\n\n\nfunc tick(units: Array) -> void:\n\tfor u in units:\n\t\tu.x += 1\n"
    rules_key = "scripts/rules/fake.gd::tick::for u in units:"
    ui = ("extends Control\n\n\nfunc _process(_d: float) -> void:\n\t_redraw()\n\n\n"
          "func _redraw() -> void:\n\tfor i in range(9):\n\t\tpass\n\n\n"
          "func on_click() -> void:\n\tfor j in range(3):\n\t\tpass\n")
    ui_key = "scripts/ui/fake_hud.gd::_redraw::for i in range(9):"
    root_body = "extends Node\n\n\nfunc %s(_e) -> void:\n\twhile busy:\n\t\tpass\n"
    twice = "extends RefCounted\n\n\nfunc f(a: Array) -> void:\n\tfor x in a:\n\t\tpass\n\tfor x in a:\n\t\tpass\n"
    multi = ("extends RefCounted\n\n\nfunc f(a: Array) -> void:\n\twhile (\n\t\ta.size() > 0\n\t\tand ok\n\t):\n"
             "\t\ta.pop_back()\n")
    sig = ("extends RefCounted\n\n\nstatic func f(\n\ta: Array, b: int\n) -> void:\n\tfor x in a:\n\t\tpass\n")
    comment = "extends RefCounted\n\n\nfunc f() -> void:\n\t# for x in a:\n\tvar s := \"for y in b:\"\n"
    emitter = ("extends Node\n\nsignal tick\n\n\nfunc _ready() -> void:\n\ttick.connect(_on_tick)\n\n\n"
               "func _process(_d: float) -> void:\n\ttick.emit()\n\n\n"
               "func _on_tick() -> void:\n\tfor k in 4:\n\t\tpass\n")
    cases = [  # (name, sources, verdict file text, should fail)
        ("rules loop without a row", {"scripts/rules/fake.gd": rules_loop}, hdr, True),
        ("rules loop with OK + reason", {"scripts/rules/fake.gd": rules_loop},
         hdr + f"{rules_key}\tOK\tat most 12 units, once per phase\n", False),
        ("rules loop with MEASURE + reason", {"scripts/rules/fake.gd": rules_loop},
         hdr + f"{rules_key}\tMEASURE\tgrows with hazard cells\n", False),
        ("unknown verdict", {"scripts/rules/fake.gd": rules_loop}, hdr + f"{rules_key}\tMAYBE\tdunno\n", True),
        ("OK without a reason", {"scripts/rules/fake.gd": rules_loop}, hdr + f"{rules_key}\tOK\t\n", True),
        ("OK with no reason column", {"scripts/rules/fake.gd": rules_loop}, hdr + f"{rules_key}\tOK\n", True),
        ("loop in a helper _process calls", {"scripts/ui/fake_hud.gd": ui}, hdr, True),
        ("helper row present; per-click loop is not hot", {"scripts/ui/fake_hud.gd": ui},
         hdr + f"{ui_key}\tOK\t9 cells\n", False),
        ("loop in a callback of a signal _process emits", {"scripts/ui/e.gd": emitter}, hdr, True),
        ("stale row only prints", {"scripts/ui/other.gd": "extends Node\n"},
         hdr + "scripts/ui/gone.gd::f::for a in b:\tOK\tgone\n", False),
        ("per-frame loop in an excluded folder is not hot", {"scripts/tests/t.gd": root_body % "_process"},
         hdr, False),
        ("per-frame loop in another excluded folder is not hot", {"scripts/tools/bot.gd": root_body % "_process"},
         hdr, False),
        ("per-frame loop outside the include prefix is not hot", {"other/x.gd": root_body % "_process"}, hdr, False),
        ("loop under a multi-line signature is found", {"scripts/rules/s.gd": sig}, hdr, True),
        ("loop in a comment or string is not a loop", {"scripts/rules/c.gd": comment}, hdr, False),
        ("repeat header needs its own #2 row", {"scripts/rules/t.gd": twice},
         hdr + "scripts/rules/t.gd::f::for x in a:\tOK\tsmall\n", True),
        ("repeat header with both rows", {"scripts/rules/t.gd": twice},
         hdr + "scripts/rules/t.gd::f::for x in a:\tOK\tsmall\nscripts/rules/t.gd::f::for x in a:#2\tOK\tsmall\n",
         False),
        ("multi-line while is one loop", {"scripts/rules/m.gd": multi},
         hdr + "scripts/rules/m.gd::f::while ( a.size() > 0 and ok ):\tFIX\tdrains one by one\n", False),
    ]
    cases += [(f"{fn} root is hot", {"scripts/input/x.gd": root_body % fn}, hdr, True)
              for fn in DEFAULT_ROOT_FUNCS]
    wrong = 0
    for name, sources, verdict_text, should_fail in cases:
        failed = bool(check_rows(scan(sources, s), verdict_text)[0])
        if failed != should_fail:
            wrong += 1
            print(f"SELF-CHECK WRONG: {name}: expected {'fail' if should_fail else 'pass'}")
    if wrong:
        print(f"SELF-CHECK FAILED: {wrong} of {len(cases)} cases.")
        return 1
    print(f"Self-check OK: {len(cases)} cases, the gate fails exactly the bad ones.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="godot-hot-loops",
        description="List every for/while loop that can run per frame in a Godot 4 project, and fail "
                    "on one without a written verdict.",
        epilog="Without --check or --self-check it prints the hot loops as TSV, to write verdict rows from. "
               "Exit codes: 0 pass, 1 a loop without a valid verdict (or a failed self-check), "
               "2 usage or config error.")
    p.add_argument("--root", help="Godot project folder (default: nearest folder with project.godot "
                                  "at or above the current directory)")
    p.add_argument("--config", help=f"config file (default: <root>/godot-code-gates.toml, table [{SECTION}])")
    p.add_argument("--check", nargs="?", const="", metavar="TSV",
                   help="fail on any hot loop without a valid verdict row; TSV is relative to the "
                        "project root (default: config `verdicts`, else hot_loops.tsv)")
    p.add_argument("--self-check", action="store_true", help="prove the gate goes red on planted cases")
    p.add_argument("--depth", type=int, help="call depth followed from the per-frame roots (default 2)")
    p.add_argument("--max-defs", type=int, help="skip names defined in more files than this (default 4)")
    p.add_argument("--include", action="append", metavar="PREFIX",
                   help="scan only paths starting with PREFIX (repeatable; replaces config `include`)")
    p.add_argument("--exclude", action="append", metavar="PREFIX",
                   help="never scan paths starting with PREFIX (repeatable; replaces config `exclude`)")
    p.add_argument("--always-hot", action="append", metavar="PREFIX",
                   help="every loop under PREFIX is hot (repeatable; replaces config `always-hot`)")
    p.add_argument("--extra-root-func", action="append", metavar="NAME",
                   help="also treat NAME as a per-frame root (repeatable; adds to the configured roots)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_check:
        return self_check()
    root = Path(args.root).resolve() if args.root else find_root(Path.cwd())
    try:
        s = settings_from(load_section(root, SECTION, args.config))
    except ConfigError as e:
        print(f"list_hot_loops: config error: {e}", file=sys.stderr)
        return 2
    s = replace(
        s,
        include=tuple(args.include) if args.include else s.include,
        exclude=tuple(args.exclude) if args.exclude else s.exclude,
        always_hot=tuple(args.always_hot) if args.always_hot else s.always_hot,
        root_funcs=s.root_funcs | set(args.extra_root_func or ()),
        depth=s.depth if args.depth is None else args.depth,
        max_defs=s.max_defs if args.max_defs is None else args.max_defs,
    )
    if args.check is not None:
        return check(root, args.check or s.verdicts, s)
    list_rows(scan(read_sources(root, s), s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
