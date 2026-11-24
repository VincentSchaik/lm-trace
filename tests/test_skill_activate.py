"""Tests for /name in the message box (user-explicit activation): skills_lab.activate and the POST /skill-activate route.
Run: python3 -m unittest discover tests"""
import http.client, json, os, shutil, sys, tempfile, threading, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import skills_lab as sl
import server


def md(name, body="Do the task.", description="Test skill. Use it when testing."):
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


class Temp(unittest.TestCase):
    """Each test gets its own empty skills/ folder and skills-meta.json."""
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

    def add(self, name, body="Do the task.", files=(), **kw):
        self.assertEqual(sl.save_skill(None, md(name, body, **kw))[0], 200)
        for rel, text in files:
            self.assertEqual(sl.save_file(name, rel, text)[0], 200)


class Activate(Temp):
    def test_content_block(self):
        self.add("alpha", "# Alpha\n\nStep 1.", files=[("references/a.md", "A"), ("scripts/run.py", "print(1)")])
        code, out = sl.activate("alpha")
        self.assertEqual(code, 200)
        self.assertEqual(out["name"], "alpha")
        self.assertEqual(sorted(out["files"]), ["references/a.md", "scripts/run.py"])
        r = out["result"]
        self.assertTrue(r.startswith('<skill_content name="alpha">\n# Alpha\n\nStep 1.\n'), r)
        self.assertTrue(r.endswith("</skill_content>"), r)
        self.assertIn("Skill folder: skills/alpha", r)
        self.assertIn("Files: ", r)
        self.assertIn("references/a.md", r)

    def test_body_only_no_frontmatter(self):
        self.add("alpha", "Only the body.")
        r = sl.activate("alpha")[1]["result"]
        self.assertNotIn("description:", r)
        self.assertNotIn("---", r)
        self.assertIn("Only the body.", r)

    def test_no_files(self):
        self.add("alpha")
        self.assertIn("Files: none", sl.activate("alpha")[1]["result"])

    def test_same_instructions_as_load_skill(self):
        # The user path and the model path give the model the same instructions and file list.
        self.add("alpha", "Steps.", files=[("references/a.md", "A")])
        inner = sl.activate("alpha")[1]["result"].removeprefix('<skill_content name="alpha">\n').removesuffix("\n</skill_content>")
        self.assertEqual(inner, sl.call("load_skill", {"name": "alpha"})["result"])

    def test_unknown(self):
        self.add("alpha")
        code, out = sl.activate("nope")
        self.assertEqual(code, 404)
        self.assertIn("/nope", out["error"]["message"])
        self.assertIn("/alpha", out["error"]["message"])  # it lists the skills you can use

    def test_unknown_does_not_list_off_skills(self):
        self.add("alpha")
        self.add("beta")
        sl.set_on("beta", False)
        msg = sl.activate("nope")[1]["error"]["message"]
        self.assertIn("/alpha", msg)
        self.assertNotIn("/beta", msg)

    def test_off(self):
        self.add("alpha")
        sl.set_on("alpha", False)
        code, out = sl.activate("alpha")
        self.assertEqual(code, 409)
        self.assertIn("off", out["error"]["message"])

    def test_not_valid(self):
        os.makedirs(os.path.join(sl.SKILLS, "broken"))  # a folder without SKILL.md
        code, out = sl.activate("broken")
        self.assertEqual(code, 409)
        self.assertIn("not valid", out["error"]["message"])
        self.assertIn("SKILL.md", out["error"]["message"])

    def test_bad_input(self):
        self.add("alpha")
        for bad in (None, 5, ["alpha"], "", "../alpha", "alpha/../alpha"):
            self.assertEqual(sl.activate(bad)[0], 404, bad)


class Route(Temp):
    """The real HTTP server: POST /skill-activate and GET /skill-slash.js."""
    def setUp(self):
        super().setUp()
        self.httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        host = f"127.0.0.1:{self.port}"
        self.p2 = [mock.patch.object(server, "HOSTS", server.HOSTS | {host}),
                   mock.patch.object(server, "ORIGINS", server.ORIGINS | {f"http://{host}"})]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in self.p2:
            p.stop()
        super().tearDown()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_activate(self):
        self.add("alpha", "Steps.")
        status, data = self.req("POST", "/skill-activate", {"name": "alpha"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["result"], sl.activate("alpha")[1]["result"])

    def test_activate_unknown(self):
        status, data = self.req("POST", "/skill-activate", {"name": "nope"})
        self.assertEqual(status, 404)
        self.assertIn("/nope", json.loads(data)["error"]["message"])

    def test_activate_not_json_object(self):
        self.assertEqual(self.req("POST", "/skill-activate", ["alpha"])[0], 400)

    def test_scripts_are_served(self):
        for path, word in (("/skill-slash.js", b"parseSlash"), ("/skill-draft.js", b"findSkillDrafts")):
            status, data = self.req("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(word, data, path)


if __name__ == "__main__":
    unittest.main()
