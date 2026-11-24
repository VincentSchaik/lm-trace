"""Tests for the skill-creator-lite starter: its validate_skill.py script (same results as the page's Validate),
the starter itself, and how new starters are added to an existing skills/ folder.
Run: python3 -m unittest discover tests"""
import importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import skills_lab as sl

STARTER = os.path.join(HERE, "skill-starters", "skill-creator-lite")
SCRIPT = os.path.join(STARTER, "scripts", "validate_skill.py")
spec = importlib.util.spec_from_file_location("validate_skill", SCRIPT)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)


def md(name="daily-standup", desc="Write a daily standup update from notes. Use this skill when the user asks for a standup.", body="1. Do it.", extra=""):
    return f"---\nname: {name}\ndescription: {desc}\n{extra}---\n\n{body}\n"


CASES = [
    md(),
    md(name="Daily-Standup"),
    md(name="-bad"),
    md(name="a--b"),
    md(name="a" * 65),
    md(name="bad_name"),
    md(desc=""),
    md(desc="x" * 1025),
    md(desc="Helps."),
    md(extra="version: 1\n"),
    md(extra="compatibility: " + "y" * 501 + "\n"),
    md(extra="metadata:\n  author: me\n"),
    md(extra="name: twice\n"),
    "---\ndescription: no name\n---\nbody\n",
    "no frontmatter\n",
    "---\nname: open\n",
    "---\nname: x\ndescription: >\n  Folded text that says\n  when to use it.\n---\nBody\n",
    "---\nname: x\nthis is not yaml\n---\nBody\n",
]


class Script(unittest.TestCase):
    def test_same_errors_as_the_page(self):
        # validate_skill.py is a copy of the checks of skills_lab (the Validate button), so the skill works
        # when you download it. It must give the same errors.
        for text in CASES:
            page = sl.validate_text(text)[1]
            self.assertEqual(vs.check(text)["errors"], page["errors"], text[:60])

    def test_valid(self):
        r = vs.check(md())
        self.assertEqual(r, {"valid": True, "name": "daily-standup", "errors": [], "hints": []})

    def test_code_block_around_the_draft(self):
        for fence in ("```markdown", "```", "````markdown", "~~~"):
            close = fence.rstrip("markdown") if fence.startswith("`") else "~~~"
            self.assertTrue(vs.check(f"{fence}\n{md()}\n{close}\n")["valid"], fence)

    def test_hints(self):
        self.assertIn("say when to use", " ".join(vs.check(md(desc="Writes standup updates for the team."))["hints"]))
        self.assertIn("body is empty", " ".join(vs.check(md(body=""))["hints"]))
        self.assertIn("under 500", " ".join(vs.check(md(body="x\n" * 600))["hints"]))

    def test_runs_as_a_script(self):
        # As run_skill_script runs it: the draft on stdin, JSON on stdout.
        r = subprocess.run([sys.executable, SCRIPT], input=md(name="Bad"), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertFalse(out["valid"])
        self.assertIn("must be lowercase", out["errors"][0])


class Starter(unittest.TestCase):
    def test_starter_is_valid_and_small(self):
        text = open(os.path.join(STARTER, "SKILL.md"), encoding="utf-8").read()
        fm, body, err = sl.parse_skill_md(text)
        files = ["references/best-practices.md", "scripts/validate_skill.py"]
        errors, hints = sl.lint("skill-creator-lite", fm, body, err, files)
        self.assertEqual(errors, [])
        self.assertEqual(hints, [])  # it names both of its files and says when to use the skill
        self.assertLess(len(body.split()), 700)  # Activation stays small (about 1,000 tokens)
        self.assertTrue(sl.starter_on("skill-creator-lite"))


class Seed(unittest.TestCase):
    """New starters join an existing skills/ folder once."""
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.starters = os.path.join(self.root, "starters")
        for n in ("meeting-notes", "release-notes", "release-notes-verbose", "skill-creator-lite"):
            os.makedirs(os.path.join(self.starters, n))
            open(os.path.join(self.starters, n, "SKILL.md"), "w").write(md(name=n))
        self.p = [mock.patch.object(sl, "SKILLS", os.path.join(self.root, "skills")),
                  mock.patch.object(sl, "META", os.path.join(self.root, "skills-meta.json")),
                  mock.patch.object(sl, "STARTERS", self.starters)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.root)

    def names(self):
        return sorted(os.listdir(sl.SKILLS))

    def test_first_time_all(self):
        sl.seed()
        self.assertEqual(self.names(), ["meeting-notes", "release-notes", "release-notes-verbose", "skill-creator-lite"])
        self.assertEqual(sl.read_meta()["_starters"], self.names())
        self.assertFalse(sl.read_meta()["release-notes-verbose"]["on"])

    def old_folder(self, folders, meta):
        os.makedirs(sl.SKILLS)
        for n in folders:
            shutil.copytree(os.path.join(self.starters, n), os.path.join(sl.SKILLS, n))
        sl.write_meta(meta)

    def test_existing_folder_gets_the_new_starter_only(self):
        # Before this change: three starters, and the user uninstalled release-notes-verbose. It does not come back.
        self.old_folder(["meeting-notes", "release-notes"], {"meeting-notes": {"on": False, "source": "Starter"}, "release-notes": {"on": True, "source": "Starter"}})
        sl.seed()
        self.assertEqual(self.names(), ["meeting-notes", "release-notes", "skill-creator-lite"])
        meta = sl.read_meta()
        self.assertEqual(meta["skill-creator-lite"], {"on": True, "source": "Starter"})
        self.assertFalse(meta["meeting-notes"]["on"])  # your settings stay

    def test_uninstalled_new_starter_stays_away(self):
        self.old_folder(["meeting-notes"], {})
        sl.seed()
        self.assertEqual(sl.delete_skill("skill-creator-lite")[0], 200)
        sl.seed()
        self.assertNotIn("skill-creator-lite", self.names())

    def test_your_skill_with_the_same_name_stays(self):
        self.old_folder([], {})
        os.makedirs(os.path.join(sl.SKILLS, "skill-creator-lite"))
        open(os.path.join(sl.SKILLS, "skill-creator-lite", "SKILL.md"), "w").write(md(name="skill-creator-lite", body="Mine."))
        sl.seed()
        self.assertIn("Mine.", open(os.path.join(sl.SKILLS, "skill-creator-lite", "SKILL.md")).read())

    def test_list_does_not_show_the_key(self):
        sl.seed()
        self.assertNotIn("_starters", [x["name"] for x in sl.list_skills()])

    def test_reset_brings_back_and_records(self):
        self.old_folder(["meeting-notes"], {})
        sl.seed()
        sl.delete_skill("skill-creator-lite")
        sl.reset()
        self.assertIn("skill-creator-lite", self.names())
        self.assertIn("skill-creator-lite", sl.read_meta()["_starters"])


if __name__ == "__main__":
    unittest.main()
