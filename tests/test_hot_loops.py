import os
import subprocess
import sys
import unittest

from _helpers import FIXTURE, SRC, fixture_copy, run

from godot_code_gates import list_hot_loops as hl

ROOT = ["--root", str(FIXTURE)]
HOT_KEYS = [
    "game/hud.gd::_on_moved::for label in get_children():",
    "game/player.gd::_update_trail::for p in trail:",
    "game/rules/combat.gd::resolve::for u in units:",
    "game/rules/combat.gd::resolve::for u in units:#2",
]
DEEP_KEY = "game/deep.gd::step_three::while i < 10:"


def rows_of(out):
    lines = out.splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


class ListTest(unittest.TestCase):
    def test_lists_exactly_the_hot_loops(self):
        code, out, _ = run(hl.main, *ROOT)
        self.assertEqual(code, 0)
        rows = rows_of(out)
        self.assertEqual([r["key"] for r in rows], HOT_KEYS)
        by_key = {r["key"]: r for r in rows}
        # Reached through a signal _process emits.
        self.assertEqual(by_key[HOT_KEYS[0]]["via"], "_process>_on_moved")
        self.assertEqual(by_key[HOT_KEYS[0]]["entry"], "player.gd:_process")
        self.assertIn("get_node", by_key[HOT_KEYS[0]]["flags"])
        # Always-hot folder: entry is the folder's last segment.
        self.assertEqual(by_key[HOT_KEYS[2]]["entry"], "rules")
        self.assertEqual(by_key[HOT_KEYS[2]]["depth"], "0")
        # extra-flags from the config.
        self.assertIn("sqrt", by_key[HOT_KEYS[3]]["flags"])

    def test_per_click_addon_and_test_loops_are_not_hot(self):
        _, out, _ = run(hl.main, *ROOT)
        self.assertNotIn("on_click", out)
        self.assertNotIn("addons/", out)
        self.assertNotIn("tests/", out)

    def test_depth_flag(self):
        _, out, _ = run(hl.main, *ROOT, "--depth", "3")
        self.assertIn(DEEP_KEY, [r["key"] for r in rows_of(out)])
        _, out, _ = run(hl.main, *ROOT, "--depth", "0")
        keys = [r["key"] for r in rows_of(out)]
        self.assertNotIn(HOT_KEYS[1], keys)  # _update_trail is one call deep
        self.assertIn(HOT_KEYS[2], keys)  # always-hot ignores depth

    def test_cli_overrides(self):
        _, out, _ = run(hl.main, *ROOT, "--exclude", "tests/")
        self.assertIn("addons/fancy_plugin/plugin.gd::_process::for n in 5:", out)
        _, out, _ = run(hl.main, *ROOT, "--always-hot", "game/deep")
        self.assertIn(DEEP_KEY, out)
        _, out, _ = run(hl.main, *ROOT, "--include", "game/rules/")
        self.assertEqual([r["key"] for r in rows_of(out)], HOT_KEYS[2:])
        _, out, _ = run(hl.main, *ROOT, "--extra-root-func", "on_click")
        self.assertIn("game/player.gd::on_click::for i in range(3):", out)

    def test_defaults_without_config(self):
        with fixture_copy() as d:
            (d / "godot-code-gates.toml").unlink()
            _, out, _ = run(hl.main, "--root", str(d))
            keys = [r["key"] for r in rows_of(out)]
        self.assertIn(HOT_KEYS[0], keys)
        self.assertIn("tests/test_runner.gd::_process::for n in 100:", keys)  # only addons/ excluded
        self.assertNotIn(HOT_KEYS[2], keys)  # nothing is always hot by default
        self.assertFalse(any(k.startswith("addons/") for k in keys))

    def test_finds_root_from_a_subfolder(self):
        r = subprocess.run([sys.executable, "-B", str(SRC / "godot_code_gates" / "list_hot_loops.py"), "--check"],
                           cwd=FIXTURE / "game" / "rules", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class CheckTest(unittest.TestCase):
    def test_passes_on_full_verdicts(self):
        code, out, _ = run(hl.main, *ROOT, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("4 hot loops (OK 2, FIX 1, MEASURE 1), 0 without a valid verdict, 0 stale", out)

    def test_explicit_tsv_path(self):
        code, _, _ = run(hl.main, *ROOT, "--check", "perf/hot_loops.tsv")
        self.assertEqual(code, 0)

    def edited(self, edit):
        with fixture_copy() as d:
            tsv = d / "perf" / "hot_loops.tsv"
            tsv.write_text(edit(tsv.read_text()))
            return run(hl.main, "--root", str(d), "--check")

    def test_missing_row_fails(self):
        code, out, _ = self.edited(lambda t: "\n".join(l for l in t.splitlines() if "#2" not in l) + "\n")
        self.assertEqual(code, 1)
        self.assertIn("NO VERDICT\tgame/rules/combat.gd:8\t" + HOT_KEYS[3], out)

    def test_bad_verdict_and_empty_reason_fail(self):
        code, out, _ = self.edited(lambda t: t.replace("\tFIX\t", "\tLATER\t"))
        self.assertEqual(code, 1)
        self.assertIn("UNKNOWN VERDICT 'LATER'", out)
        code, out, _ = self.edited(lambda t: t.replace("\tMEASURE\ttrail length grows with play time; not measured yet",
                                                      "\tMEASURE\t "))
        self.assertEqual(code, 1)
        self.assertIn("NO REASON", out)

    def test_stale_row_prints_but_passes(self):
        code, out, _ = self.edited(lambda t: t + "game/gone.gd::f::for a in b:\tOK\tgone\n")
        self.assertEqual(code, 0)
        self.assertIn("STALE (loop gone or its function renamed)\tgame/gone.gd::f::for a in b:", out)

    def test_new_loop_fails(self):
        with fixture_copy() as d:
            p = d / "game" / "player.gd"
            p.write_text(p.read_text().replace("\tp.age += 1\n", "\tp.age += 1\n\twhile trail.size() > 9:\n\t\ttrail.pop_front()\n"))
            code, out, _ = run(hl.main, "--root", str(d), "--check")
        self.assertEqual(code, 1)
        self.assertIn("game/player.gd::_update_trail::while trail.size() > 9:", out)

    def test_missing_verdict_file(self):
        code, out, _ = run(hl.main, *ROOT, "--check", "nope.tsv")
        self.assertEqual(code, 2)
        self.assertIn("verdict file not found", out)


class ConfigTest(unittest.TestCase):
    def with_config(self, text, *argv):
        with fixture_copy() as d:
            (d / "godot-code-gates.toml").write_text(text)
            return run(hl.main, "--root", str(d), *argv)

    def test_unknown_key_is_an_error(self):
        code, _, err = self.with_config("[hot-loops]\nalways_hot = ['game/rules/']\n")
        self.assertEqual(code, 2)
        self.assertIn("unknown key 'always_hot'", err)

    def test_wrong_type_is_an_error(self):
        code, _, err = self.with_config("[hot-loops]\ndepth = '2'\n")
        self.assertEqual(code, 2)
        self.assertIn("'depth' must be", err)

    def test_bad_regex_is_an_error(self):
        code, _, err = self.with_config("[hot-loops]\nextra-flags = { x = '(' }\n")
        self.assertEqual(code, 2)
        self.assertIn("bad flag regex", err)

    def test_root_funcs_replace_and_flags_replace(self):
        code, out, _ = self.with_config(
            "[hot-loops]\nroot-funcs = ['_physics_process']\ndepth = 3\nflags = { only = 'i \\+= 1' }\n")
        self.assertEqual(code, 0)
        rows = rows_of(out)
        self.assertEqual([r["key"] for r in rows], [DEEP_KEY])
        self.assertEqual(rows[0]["flags"], "only")
        self.assertEqual(rows[0]["via"], "_physics_process>step_one>step_two>step_three")

    def test_explicit_config_file(self):
        with fixture_copy() as d:
            cfg = d / "other.toml"
            cfg.write_text("[hot-loops]\ninclude = ['game/rules/']\nalways-hot = ['game/rules/']\n")
            _, out, _ = run(hl.main, "--root", str(d), "--config", str(cfg))
        self.assertEqual([r["key"] for r in rows_of(out)], HOT_KEYS[2:])

    def test_missing_explicit_config_is_an_error(self):
        code, _, err = run(hl.main, *ROOT, "--config", "/nonexistent/x.toml")
        self.assertEqual(code, 2)
        self.assertIn("config file not found", err)


class SelfCheckTest(unittest.TestCase):
    def test_self_check_passes(self):
        code, out, _ = run(hl.main, "--self-check")
        self.assertEqual(code, 0)
        self.assertIn("Self-check OK", out)

    def test_runs_as_a_plain_script(self):
        env = dict(os.environ, PYTHONPATH="")
        r = subprocess.run([sys.executable, "-B", str(SRC / "godot_code_gates" / "list_hot_loops.py"), "--self-check"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
