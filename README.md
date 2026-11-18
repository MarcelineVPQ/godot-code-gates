# godot-code-gates

Two static tools for Godot 4 projects. They read your `.gd` and `.tscn` files as text. They never
run Godot.

- **`godot-hot-loops`** lists every `for` and `while` loop that can run per frame. With `--check`
  it fails when one of those loops has no written verdict. Put it in CI.
- **`godot-callmap`** answers "who calls this?": function call sites, signal emits and connects,
  scene connections, and what breaks if you change a script.

Python 3.11 or newer. No dependencies. Works with stock Godot 4; no plugin or addon needed.

Both tools came out of the dev tools of Neon Debris, a
turn-based tactics game by 42 Pixel Studios.

## Why a loop gate

A loop in `_process` runs 60 times a second. A loop in a function that `_process` calls does too.
Most of these loops are fine. Some walk every node in the scene, or grow with the map, and nobody
notices until the frame rate drops.

The idea is simple: **every loop that runs per frame gets a written reason why it is cheap
enough.** The reasons live in a TSV file next to your code. CI fails on a hot loop without one.
So a new per-frame loop can't slip in unreviewed, and the review is written down.

Each row gets one of three verdicts:

| Verdict | Means |
|---|---|
| `OK` | Cheap enough at its call rate. The reason says why: a bound, a call rate. |
| `FIX` | A known cost to remove. |
| `MEASURE` | The cost grows with content size (map, units, nodes) and nobody has measured it yet. |

`FIX` and `MEASURE` rows pass the gate. They are honest debt, written down. Only a missing row, an
unknown verdict or an empty reason fails.

## Install

```sh
pip install git+https://github.com/MarcelineVPQ/godot-code-gates@v0.1.0
```

This gives you `godot-hot-loops` and `godot-callmap`. You can also skip the install and run the
files directly; they only need each other's folder:

```sh
python3 path/to/godot_code_gates/list_hot_loops.py --check
python3 path/to/godot_code_gates/callmap.py calls take_damage
```

## godot-hot-loops

```sh
godot-hot-loops                  # list hot loops as TSV, to write verdict rows from
godot-hot-loops --check          # exit 1 on a hot loop without a valid verdict
godot-hot-loops --self-check     # prove the gate goes red on planted cases
```

Run it anywhere inside the project. It finds the project root by walking up to `project.godot`.
Use `--root DIR` to point elsewhere.

### What counts as hot

- Every loop inside a per-frame or per-event function. By default these are `_process`,
  `_physics_process`, `_integrate_forces`, `_draw`, `_input`, `_shortcut_input`,
  `_unhandled_input`, `_unhandled_key_input`, `_gui_input`, `_process_modification` and
  `_process_modification_with_delta`.
- Every loop in a function those call, up to 2 calls deep. Calls are found by name:
  - `foo(` means `foo` in the same file if it is defined there, else every `foo` in the project.
  - `x.foo(` means every `foo` in the project.
  - `sig.emit(` means every callback connected to `sig` with `sig.connect(callback)`.
  - A name defined in more than 4 files is too generic to follow, and is skipped.
- Every loop in an **always-hot** folder. Use this for code that runs often but not from a frame
  callback. A turn-based game's rules folder is the typical case: any rules function can run per
  command for every unit.

Name matching finds too much rather than too little, which is the safe side for a gate. It misses
calls through stored `Callable`s, `call("name")`, tweens and timers. Hidden iteration
(`.filter`, `.map`, `.any`, `.sort_custom`) is not counted as a loop, but the listing's `flags`
column points at it.

### The listing

Without `--check`, it prints one TSV row per hot loop:

| Column | Example |
|---|---|
| `key` | `game/hud.gd::_on_moved::for label in get_children():` |
| `where` | `game/hud.gd:11` |
| `function` | `_on_moved` |
| `entry` | `player.gd:_process` (the root it was reached from), or the always-hot folder's name |
| `depth` | `1` (calls from the root) |
| `via` | `_process>_on_moved` |
| `header` | `for label in get_children():` |
| `flags` | `get_node,str_fmt` (things worth a look inside the loop body) |
| `callees` | project functions the loop body calls |

Default flags: `get_node`, `load`, `new`, `alloc`, `duplicate`, `hidden_loop`, `str_fmt`, `emit`.
They are hints only. The gate ignores them.

### The verdict file (TSV)

A header line, then one row per hot loop. Three tab-separated columns: `key`, `verdict`, `reason`.

```tsv
key	verdict	reason
game/hud.gd::_on_moved::for label in get_children():	FIX	runs per frame over every HUD label; cache the labels in _ready
game/player.gd::_update_trail::for p in trail:	MEASURE	trail length grows with play time; not measured yet
game/rules/combat.gd::resolve::for u in units:	OK	at most 8 units, once per attack
game/rules/combat.gd::resolve::for u in units:#2	OK	at most 8 units, once per attack
```

The key is `path::function::loop header`, with whitespace collapsed. It has no line number, so
it survives edits above the loop. A second loop with the same header in the same function gets
`#2`, a third `#3`, and so on. A multi-line loop header is joined into one line.

`--check` prints one line per problem, then a summary:

```text
NO VERDICT	game/player.gd:18	game/player.gd::_update_trail::while trail.size() > 9:
STALE (loop gone or its function renamed)	game/gone.gd::f::for a in b:
list_hot_loops: 5 hot loops (OK 2, FIX 1, MEASURE 1), 1 without a valid verdict, 1 stale row(s).
```

Stale rows (for loops that no longer exist) are printed but don't fail. Delete them in the same
change.

Exit codes: `0` pass, `1` a loop without a valid verdict (or a failed self-check), `2` a usage
error, a config error or a missing verdict file.

To start: run `godot-hot-loops > hot_loops.tsv`, keep only the `key` column, and add a verdict
and reason to each row.

## godot-callmap

```sh
godot-callmap calls <func>       # where a function is defined, called, or connected in scenes
godot-callmap signal <name>      # where a signal is declared, emitted, connected and awaited
godot-callmap file <path.gd>     # what a script defines, emits and connects, and who uses it
godot-callmap impact <path.gd>   # every outside call site of each function the script defines
```

Paths are relative to the project root. `addons/` is skipped unless you pass `--all`. Folders
whose name starts with `.` (`.godot`, `.git`) are always skipped.

It matches by name, so a common name like `update` can match unrelated code. Read the hits.

`signal` also notes a signal that is declared but never emitted, or never connected or awaited.
`impact` ends with public functions that nothing outside the file calls.

## Config reference

Put `godot-code-gates.toml` in the project root (next to `project.godot`), or pass
`--config FILE`. Every key is optional. Paths are prefixes relative to the project root, with
`/` separators. Unknown keys are an error, so a typo can't silently do nothing.

```toml
[hot-loops]
include = [""]                   # scan only paths starting with one of these ("" = everything)
exclude = ["addons/"]            # never scan these (they also don't count as call targets)
always-hot = []                  # every loop under these is hot; `entry` shows the folder name
root-funcs = [                   # per-frame roots; this list replaces the default
    "_process", "_physics_process", "_integrate_forces", "_draw", "_input", "_shortcut_input",
    "_unhandled_input", "_unhandled_key_input", "_gui_input",
    "_process_modification", "_process_modification_with_delta",
]
extra-root-funcs = []            # added to root-funcs (e.g. a custom tick you call per frame)
depth = 2                        # calls followed from a root
max-defs = 4                     # skip names defined in more files than this
verdicts = "hot_loops.tsv"       # the verdict file --check reads when given no path
# flags = { name = 'regex', ... }        replaces the default listing flags (order is kept)
# extra-flags = { name = 'regex', ... }  added after the default flags

[callmap]
exclude = ["addons/"]            # skipped unless --all
```

Command-line flags win over the config:

| Flag | Effect |
|---|---|
| `--root DIR` | Project root. Default: nearest folder with `project.godot` above the current directory. |
| `--config FILE` | Config file. Default: `<root>/godot-code-gates.toml`. |
| `--check [TSV]` | Gate mode. TSV is relative to the root; default from `verdicts`. |
| `--depth N`, `--max-defs N` | Replace `depth`, `max-defs`. |
| `--include P`, `--exclude P`, `--always-hot P` | Replace the config list. Repeatable. |
| `--extra-root-func NAME` | Add a root. Repeatable. |
| `--all` (callmap) | Ignore `exclude`. |

A turn-based game with rules in `scripts/rules/` and test and tool scripts it never wants gated
might use:

```toml
[hot-loops]
include = ["scripts/"]
exclude = ["scripts/tests/", "scripts/tools/"]
always-hot = ["scripts/rules/"]
verdicts = "docs/perf/hot_loops.tsv"
extra-flags = { path = '\b(find_path|reachable)\(' }
```

## GitHub Actions

```yaml
name: CI
on: [push, pull_request]
jobs:
  loop-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install git+https://github.com/MarcelineVPQ/godot-code-gates@v0.1.0
      - name: Loop gate (self-check, then every hot loop has a verdict)
        run: godot-hot-loops --self-check && godot-hot-loops --check
```

## Development

```sh
python3 -m unittest discover -s tests -v
python3 src/godot_code_gates/list_hot_loops.py --self-check
```

The tests run both tools against the small Godot project in `tests/fixtures/basic/`.

## Licence

MIT. See [LICENSE](LICENSE).
