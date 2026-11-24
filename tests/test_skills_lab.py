"""Regression tests for skills_lab.save_skill: create, edit, rename, collisions and rollback.
Run: python3 -m unittest discover tests"""
import json, os, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import skills_lab as sl


def md(name, body="Do the task."):
    return f"---\nname: {name}\ndescription: Test skill. Use it when testing.\n---\n\n{body}\n"


class SaveSkill(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.p = [mock.patch.object(sl, "SKILLS", os.path.join(self.root, "skills")),
                  mock.patch.object(sl, "META", os.path.join(self.root, "skills-meta.json")),
                  mock.patch.object(sl, "STARTERS", os.path.join(self.root, "no-starters"))]  # no seeding
        for p in self.p:
            p.start()
        os.makedirs(sl.SKILLS)

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.root)

    def read(self, name, f="SKILL.md"):
        with open(os.path.join(sl.SKILLS, name, f), encoding="utf-8") as fh:
            return fh.read()

    def meta(self):
        return json.load(open(sl.META)) if os.path.exists(sl.META) else {}

    def state(self):
        files = sorted(os.path.relpath(os.path.join(dp, f), sl.SKILLS) for dp, _, fs in os.walk(sl.SKILLS) for f in fs)
        return files, {n: open(os.path.join(sl.SKILLS, n)).read() for n in files}, self.meta()

    # ---- create
    def test_create(self):
        code, out = sl.save_skill(None, md("alpha"))
        self.assertEqual((code, out), (200, {"name": "alpha"}))
        self.assertEqual(self.read("alpha"), md("alpha"))
        self.assertEqual(self.meta()["alpha"], {"source": "Created", "on": True})
        self.assertFalse(os.path.exists(os.path.join(sl.SKILLS, "alpha", ".SKILL.md.tmp")))

    def test_create_collision(self):
        sl.save_skill(None, md("alpha", "first"))
        code, _ = sl.save_skill(None, md("alpha", "second"))
        self.assertEqual(code, 409)
        self.assertEqual(self.read("alpha"), md("alpha", "first"))

    def test_create_bad_name(self):
        for bad in ("Alpha", "-alpha", "al--pha", "a" * 65):
            code, _ = sl.save_skill(None, md(bad))
            self.assertEqual(code, 400, bad)
        self.assertEqual(os.listdir(sl.SKILLS), [])

    def test_create_not_text(self):
        self.assertEqual(sl.save_skill(None, None)[0], 400)

    # ---- edit
    def test_edit_same_name_keeps_meta_and_files(self):
        sl.save_skill(None, md("alpha"))
        sl.set_on("alpha", False)
        sl.set_example("alpha", "try me")
        os.makedirs(os.path.join(sl.SKILLS, "alpha", "references"))
        open(os.path.join(sl.SKILLS, "alpha", "references", "a.md"), "w").write("ref")
        code, _ = sl.save_skill(None, md("alpha", "changed"), old="alpha")
        self.assertEqual(code, 200)
        self.assertEqual(self.read("alpha"), md("alpha", "changed"))
        self.assertEqual(self.read("alpha", "references/a.md"), "ref")
        self.assertEqual(self.meta()["alpha"], {"source": "Created", "on": False, "example": "try me"})

    def test_edit_keeps_lowercase_file(self):
        os.makedirs(os.path.join(sl.SKILLS, "low"))
        open(os.path.join(sl.SKILLS, "low", "skill.md"), "w").write(md("low"))
        self.assertEqual(sl.save_skill(None, md("low", "new"), old="low")[0], 200)
        self.assertEqual(sorted(os.listdir(os.path.join(sl.SKILLS, "low"))), ["skill.md"])
        self.assertEqual(self.read("low", "skill.md"), md("low", "new"))

    def test_edit_missing(self):
        self.assertEqual(sl.save_skill(None, md("nope"), old="nope")[0], 404)
        self.assertEqual(os.listdir(sl.SKILLS), [])

    # ---- rename
    def test_rename_moves_folder_files_and_meta(self):
        sl.save_skill(None, md("alpha"))
        sl.set_on("alpha", False)
        os.makedirs(os.path.join(sl.SKILLS, "alpha", "scripts"))
        open(os.path.join(sl.SKILLS, "alpha", "scripts", "run.py"), "w").write("print(1)")
        code, out = sl.save_skill(None, md("beta"), old="alpha")
        self.assertEqual((code, out), (200, {"name": "beta"}))
        self.assertFalse(os.path.exists(os.path.join(sl.SKILLS, "alpha")))
        self.assertEqual(self.read("beta"), md("beta"))
        self.assertEqual(self.read("beta", "scripts/run.py"), "print(1)")
        self.assertNotIn("alpha", self.meta())
        self.assertEqual(self.meta()["beta"], {"source": "Created", "on": False})

    def test_rename_collision_changes_nothing(self):
        sl.save_skill(None, md("alpha"))
        sl.save_skill(None, md("beta"))
        before = self.state()
        self.assertEqual(sl.save_skill(None, md("beta", "x"), old="alpha")[0], 409)
        self.assertEqual(self.state(), before)

    # ---- rollback
    def test_rollback_when_replace_fails_on_rename(self):
        sl.save_skill(None, md("alpha"))
        before = self.state()
        with mock.patch.object(sl.os, "replace", side_effect=OSError(28, "No space left on device")):
            code, out = sl.save_skill(None, md("beta"), old="alpha")
        self.assertEqual(code, 500)
        self.assertIn("No space left", out["error"]["message"])
        self.assertEqual(self.state(), before)

    def test_rollback_when_meta_fails_on_rename(self):
        sl.save_skill(None, md("alpha"))
        before = self.state()
        with mock.patch.object(sl, "write_meta", side_effect=OSError(13, "Permission denied")):
            self.assertEqual(sl.save_skill(None, md("beta"), old="alpha")[0], 500)
        self.assertEqual(self.state(), before)

    def test_rollback_when_meta_fails_on_edit(self):
        sl.save_skill(None, md("alpha"))
        before = self.state()
        with mock.patch.object(sl, "write_meta", side_effect=OSError(13, "Permission denied")):
            self.assertEqual(sl.save_skill(None, md("alpha", "changed"), old="alpha")[0], 500)
        self.assertEqual(self.state(), before)

    def test_rollback_when_meta_fails_on_create(self):
        with mock.patch.object(sl, "write_meta", side_effect=OSError(13, "Permission denied")):
            self.assertEqual(sl.save_skill(None, md("alpha"))[0], 500)
        self.assertEqual(os.listdir(sl.SKILLS), [])

    def test_rollback_when_write_fails(self):
        sl.save_skill(None, md("alpha"))
        before = self.state()
        real = open
        def bad_open(path, mode="r", *a, **k):
            if str(path).endswith(".SKILL.md.tmp") and "w" in mode:
                raise OSError(28, "No space left on device")
            return real(path, mode, *a, **k)
        with mock.patch("builtins.open", bad_open):
            self.assertEqual(sl.save_skill(None, md("beta"), old="alpha")[0], 500)
        self.assertEqual(self.state(), before)

    # ---- the undo touches only what the save changed, and reports its own failures
    def test_original_untouched_when_temp_write_fails(self):
        # The save fails before SKILL.md is replaced: the undo must not write SKILL.md at all. Every write to the
        # skill folder fails here, so a needless restore would truncate the original or report an undo failure.
        sl.save_skill(None, md("alpha"))
        before = self.state()
        real = open
        def bad_open(path, mode="r", *a, **k):
            if "w" in mode and os.path.dirname(str(path)) == os.path.join(sl.SKILLS, "alpha"):
                real(path, mode, *a, **k).close()  # as a real failed write: the file is truncated, then the error
                raise OSError(28, "No space left on device")
            return real(path, mode, *a, **k)
        with mock.patch("builtins.open", bad_open):
            code, out = sl.save_skill(None, md("alpha", "changed"), old="alpha")
        self.assertEqual(code, 500)
        self.assertNotIn("undo", out["error"]["message"].lower())
        self.assertEqual(self.state(), before)

    def test_failed_restore_is_reported_and_file_not_truncated(self):
        # SKILL.md was replaced, then the metadata write and the restore both fail: the error says so, and
        # SKILL.md is whole (the new text), not half written.
        sl.save_skill(None, md("alpha"))
        real = open
        def bad_open(path, mode="r", *a, **k):
            if "w" in mode and str(path).endswith(".SKILL.md.undo"):
                raise OSError(28, "No space left on device")
            return real(path, mode, *a, **k)
        with mock.patch.object(sl, "write_meta", side_effect=OSError(13, "Permission denied")), mock.patch("builtins.open", bad_open):
            code, out = sl.save_skill(None, md("alpha", "changed"), old="alpha")
        self.assertEqual(code, 500)
        self.assertIn("undo also failed", out["error"]["message"])
        self.assertIn("restore SKILL.md", out["error"]["message"])
        self.assertEqual(self.read("alpha"), md("alpha", "changed"))
        self.assertFalse(os.path.exists(os.path.join(sl.SKILLS, "alpha", ".SKILL.md.undo")))

    def test_failed_rename_back_is_reported(self):
        sl.save_skill(None, md("alpha"))
        real_rename = os.rename
        calls = []
        def rename(a, b):
            calls.append((a, b))
            if len(calls) > 1:  # the first rename (alpha -> beta) works; the rename back fails
                raise OSError(16, "Resource busy")
            return real_rename(a, b)
        with mock.patch.object(sl.os, "replace", side_effect=OSError(28, "No space left on device")), mock.patch.object(sl.os, "rename", rename):
            code, out = sl.save_skill(None, md("beta"), old="alpha")
        self.assertEqual(code, 500)
        self.assertIn("rename the folder back to alpha", out["error"]["message"])


class ValidateText(unittest.TestCase):
    def test_validate_text(self):
        code, out = sl.validate_text(md("alpha"))
        self.assertEqual((code, out["name"], out["errors"]), (200, "alpha", []))
        self.assertEqual(sl.validate_text(None)[0], 400)


if __name__ == "__main__":
    unittest.main()
