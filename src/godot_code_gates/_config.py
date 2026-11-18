"""Shared helpers: find the Godot project root, read godot-code-gates.toml, walk source files."""
import os
import tomllib
from pathlib import Path

CONFIG_NAME = "godot-code-gates.toml"


class ConfigError(Exception):
    pass


def find_root(start: Path) -> Path:
    """Nearest folder at or above `start` that holds project.godot; `start` itself if none does."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / "project.godot").is_file():
            return d
    return start


def load_section(root: Path, section: str, config_path: str | None = None) -> dict:
    """The `[section]` table of the config file ({} when there is no file or no table).

    `config_path` is an explicit file (relative to the current directory); without it the tool
    looks for godot-code-gates.toml in the project root.
    """
    path = Path(config_path) if config_path else root / CONFIG_NAME
    if not path.is_file():
        if config_path:
            raise ConfigError(f"config file not found: {path}")
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    table = data.get(section, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [{section}] must be a table")
    return table


def check_keys(table: dict, allowed: dict, section: str) -> None:
    """Reject unknown keys and wrong value types, so a typo never silently does nothing."""
    for key, value in table.items():
        if key not in allowed:
            raise ConfigError(f"[{section}] unknown key '{key}' (known: {', '.join(sorted(allowed))})")
        kind = allowed[key]
        if kind is list:
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif kind is dict:
            ok = isinstance(value, dict) and all(isinstance(v, str) for v in value.values())
        elif kind is int:
            ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else:
            ok = isinstance(value, kind)
        if not ok:
            want = {list: "a list of strings", dict: "a table of strings", int: "a whole number >= 0",
                    str: "a string"}[kind]
            raise ConfigError(f"[{section}] '{key}' must be {want}")


def source_files(root: Path, ext: str) -> list:
    """Every *.ext under root as relative Paths, sorted; folders whose name starts with '.'
    (.godot, .git, .import and the like) are skipped."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in filenames:
            if f.endswith("." + ext):
                out.append((Path(dirpath) / f).relative_to(root))
    return sorted(out)


def starts_with_any(rel: str, prefixes) -> bool:
    return any(rel.startswith(p) for p in prefixes)
