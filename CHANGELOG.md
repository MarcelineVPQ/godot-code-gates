# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-18

First release. Both tools come from the dev tools of Neon Debris (42 Pixel Studios).

### Added

- `godot-hot-loops`: lists every `for`/`while` loop that can run per frame, and
  `--check` fails on one without a verdict row (OK, FIX or MEASURE plus a reason).
  `--self-check` proves the gate goes red on planted cases.
- `godot-callmap`: `calls`, `signal`, `file` and `impact` queries over `.gd` and `.tscn` files.
- `godot-code-gates.toml` config: scanned folders, excluded folders, always-hot folders,
  per-frame root functions, call depth, the name-match limit, listing flags and the verdict file.
- Project root found from `project.godot`, or given with `--root`.
- Both tools run as console scripts or as plain `python3 path/to/script.py`. No dependencies
  beyond Python 3.11.

[0.1.0]: https://github.com/MarcelineVPQ/godot-code-gates/releases/tag/v0.1.0
