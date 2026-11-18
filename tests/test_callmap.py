import os
import subprocess
import sys
import unittest

from _helpers import FIXTURE, SRC, fixture_copy, run

from godot_code_gates import callmap

ROOT = ["--root", str(FIXTURE)]


class CallmapTest(unittest.TestCase):
    def test_calls(self):
        code, out, _ = run(callmap.main, "calls", "_update_trail", *ROOT)
        self.assertEqual(code, 0)
        self.assertIn("defined: _update_trail() (1)\n  game/player.gd:15\n", out)
        self.assertIn("game/player.gd:11: _update_trail()", out)

    def test_calls_finds_scene_connections(self):
        _, out, _ = run(callmap.main, "calls", "on_click", *ROOT)
        self.assertIn("connected as a method in scenes (1)", out)
        self.assertIn('scenes/main.tscn:16: [connection signal="pressed"', out)

    def test_signal(self):
        _, out, _ = run(callmap.main, "signal", "moved", *ROOT)
        self.assertIn("declared: signal moved (1)", out)
        self.assertIn("game/player.gd:12: moved.emit(position)", out)
        self.assertIn("game/hud.gd:7: player.moved.connect(_on_moved)  -> _on_moved", out)
        self.assertNotIn("NOTE:", out)

    def test_signal_notes(self):
        with fixture_copy() as d:
            p = d / "game" / "player.gd"
            p.write_text(p.read_text() + "\nsignal unused_one\n")
            _, out, _ = run(callmap.main, "signal", "unused_one", "--root", str(d))
        self.assertIn("NOTE: declared but never connected or awaited", out)
        self.assertIn("NOTE: declared but never emitted", out)

    def test_file(self):
        _, out, _ = run(callmap.main, "file", "game/player.gd", *ROOT)
        self.assertTrue(out.startswith("game/player.gd  (class_name Player)\n"))
        self.assertIn("signals declared (1)\n  moved  line 4", out)
        self.assertIn("scenes using this script (1)\n  scenes/main.tscn", out)
        self.assertIn("on_click()  line 20: 1 outside use(s)", out)

    def test_file_absolute_path(self):
        _, out, _ = run(callmap.main, "file", str(FIXTURE / "game" / "player.gd"), *ROOT)
        self.assertTrue(out.startswith("game/player.gd  (class_name Player)"))

    def test_file_not_found(self):
        code, _, err = run(callmap.main, "file", "game/nope.gd", *ROOT)
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_impact(self):
        _, out, _ = run(callmap.main, "impact", "game/hud.gd", *ROOT)
        self.assertIn("public functions with no outside use", out)

    def test_addons_skipped_unless_all(self):
        _, out, _ = run(callmap.main, "calls", "_process", *ROOT)
        self.assertNotIn("addons/", out)
        _, out, _ = run(callmap.main, "calls", "_process", *ROOT, "--all")
        self.assertIn("addons/fancy_plugin/plugin.gd:5", out)

    def test_config_exclude(self):
        with fixture_copy() as d:
            (d / "godot-code-gates.toml").write_text("[callmap]\nexclude = ['game/']\n")
            _, out, _ = run(callmap.main, "calls", "_process", "--root", str(d))
        self.assertNotIn("game/", out)
        self.assertIn("addons/fancy_plugin/plugin.gd:5", out)

    def test_bad_config(self):
        with fixture_copy() as d:
            (d / "godot-code-gates.toml").write_text("[callmap]\nskip = ['x']\n")
            code, _, err = run(callmap.main, "calls", "x", "--root", str(d))
        self.assertEqual(code, 2)
        self.assertIn("unknown key 'skip'", err)

    def test_bad_command(self):
        code, _, _ = run(callmap.main, "who", "x", *ROOT)
        self.assertEqual(code, 2)

    def test_runs_as_a_plain_script(self):
        env = dict(os.environ, PYTHONPATH="")
        r = subprocess.run([sys.executable, "-B", str(SRC / "godot_code_gates" / "callmap.py"), "signal", "moved"],
                           cwd=FIXTURE, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("declared: signal moved (1)", r.stdout)


if __name__ == "__main__":
    unittest.main()
