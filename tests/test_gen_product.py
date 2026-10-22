"""T3-23: the object map generator (tools/gen_product.py). Host only; no signing."""

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import gen_product  # noqa: E402

ETS6_ONLY = "only ETS 5.7 is supported for now"


def group_objects(xml_text):
    """ComObject / ComObjectRef / ComObjectRefRef attributes, order-insensitive."""
    root = ET.fromstring(xml_text)
    out = {}
    for tag in ("ComObject", "ComObjectRef", "ComObjectRefRef"):
        out[tag] = sorted(sorted(el.attrib.items()) for el in root.iter("{*}" + tag))
    return out


class GenProductTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "ets"
        self.core = self.tmp / "core"
        self.root.mkdir()
        (self.core / gen_product.HEADER_REL).parent.mkdir(parents=True)
        self.cfg = json.loads((REPO / "knx_objects.json").read_text(encoding="utf-8"))
        shutil.copy(REPO / "OpenPLC_TP.xml", self.root / "OpenPLC_TP.xml")

    def write_cfg(self, cfg):
        (self.root / "knx_objects.json").write_text(json.dumps(cfg), encoding="utf-8")

    def run_main(self, *extra, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = gen_product.main(["--root", str(self.root), "--core", str(self.core)] + list(extra),
                                  env=env or {})
        return rc, out.getvalue() + err.getvalue()

    def generate(self, cfg):
        self.write_cfg(cfg)
        rc, text = self.run_main("--no-sign")
        self.assertEqual(rc, 0, text)
        self.assertTrue((self.core / gen_product.HEADER_REL).is_file())
        return text

    def obj(self, cfg, number):
        return next(o for o in cfg["objects"] if o["number"] == number)

    def bumped(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["replaces_versions"].append(cfg["application_version"])
        cfg["application_version"] += 1
        return cfg

    # -- the repo config reproduces the committed product

    def test_repo_config_matches_product(self):
        cfg = gen_product.load_config(REPO / "knx_objects.json")
        xml = gen_product.render_xml(cfg, gen_product.TEMPLATE.read_text(encoding="utf-8"))
        committed = (REPO / "OpenPLC_TP.xml").read_text(encoding="utf-8")
        self.assertEqual(group_objects(xml), group_objects(committed))

    # -- warnings for changes that shift existing links; still generates

    def assert_warns(self, cfg, *fragments):
        text = self.generate(cfg)
        for f in fragments:
            self.assertIn(f, text)
        self.assertIn("warning:", text)
        self.assertIn('ApplicationVersion="%d"' % cfg["application_version"],
                      (self.root / "OpenPLC_TP.xml").read_text(encoding="utf-8"))

    def test_number_change_warns(self):
        cfg = self.bumped()
        self.obj(cfg, 26)["number"] = 30
        self.assert_warns(cfg, "changed number 26 -> 30")

    def test_dpt_change_warns(self):
        cfg = self.bumped()
        self.obj(cfg, 23)["dpt"] = "DPST-9-21"
        self.assert_warns(cfg, "changed DPT DPST-9-20 -> DPST-9-21")

    def test_flags_change_warns(self):
        cfg = self.bumped()
        self.obj(cfg, 1)["flags"] = "CWT"
        self.assert_warns(cfg, "changed flags CW -> CWT")

    def test_removed_object_warns(self):
        cfg = self.bumped()
        cfg["objects"] = [o for o in cfg["objects"] if o["number"] != 22]
        self.assert_warns(cfg, "object 22 'Temperature switch' was removed")

    def test_added_object_is_silent(self):
        cfg = self.bumped()
        cfg["objects"].append({"number": 27, "key": "NEW_ONE", "name": "New one", "function": "Input",
                               "port": None, "size": "1 Bit", "dpt": "DPST-1-1", "flags": "CRT", "note": ""})
        text = self.generate(cfg)
        self.assertNotIn("warning", text)
        self.assertIn("#define KNX_OBJ_NEW_ONE",
                      (self.core / gen_product.HEADER_REL).read_text(encoding="utf-8"))

    def test_changed_objects_same_version_warns(self):
        cfg = copy.deepcopy(self.cfg)
        self.obj(cfg, 21)["name"] = "Room temperature"
        text = self.generate(cfg)
        self.assertIn("application_version is still %d" % cfg["application_version"], text)

    # -- missing application_version: error, nothing written

    def test_missing_version_writes_nothing(self):
        cfg = copy.deepcopy(self.cfg)
        del cfg["application_version"]
        self.write_cfg(cfg)
        (self.root / "OpenPLC_TP.xml").unlink()
        rc, text = self.run_main("--no-sign")
        self.assertNotEqual(rc, 0)
        self.assertIn("application_version is missing", text)
        self.assertFalse((self.root / "OpenPLC_TP.xml").exists())
        self.assertFalse((self.core / gen_product.HEADER_REL).exists())

    # -- ETS6 without ETS 5.7

    def test_ets6_only_refuses_to_sign(self):
        self.write_cfg(self.cfg)
        producer = self.tmp / "OpenKNXproducer-x64.exe"
        producer.write_bytes(b"")
        pf = self.tmp / "pf"
        (pf / "ETS6").mkdir(parents=True)
        (pf / "ETS6" / gen_product.ETS_DLL).write_bytes(b"")
        env = {"USERPROFILE": str(self.tmp / "home"), "ProgramFiles": str(pf),
               "ProgramFiles(x86)": str(self.tmp / "pf86")}
        with mock.patch.object(gen_product, "file_version", return_value=(6, 1, 0, 0)), \
                mock.patch.object(gen_product.subprocess, "run") as run:
            rc, text = self.run_main("--producer", str(producer), env=env)
        self.assertNotEqual(rc, 0)
        self.assertIn(ETS6_ONLY, text)
        run.assert_not_called()

    # -- bundled producer and ets_paths.json (decision 145)

    def test_default_producer_is_bundled(self):
        self.write_cfg(self.cfg)
        self.assertEqual(gen_product.PRODUCER, REPO / "tools" / "OpenKNXproducer" / "OpenKNXproducer-x64.exe")
        fake = self.tmp / "bundled.exe"
        fake.write_bytes(b"")
        with mock.patch.object(gen_product, "PRODUCER", fake), \
                mock.patch.object(gen_product, "check_ets", return_value=None), \
                mock.patch.object(gen_product.subprocess, "run") as run:
            run.return_value.returncode = 0
            rc, text = self.run_main()
        self.assertEqual(rc, 0, text)
        self.assertEqual(run.call_args[0][0][0], str(fake))

    def test_ets_candidates_come_from_ets_paths_json(self):
        home, pf, pf86 = self.tmp / "home", self.tmp / "pf", self.tmp / "pf86"
        found = [home / "bin" / "CV" / "5.7", pf86 / "ETS6", pf / "ETS5"]
        for d in found:
            d.mkdir(parents=True)
            (d / gen_product.ETS_DLL).write_bytes(b"")
        (pf / "ETS6").mkdir()  # no DLL: skipped
        env = {"USERPROFILE": str(home), "ProgramFiles": str(pf), "ProgramFiles(x86)": str(pf86)}
        self.assertEqual(gen_product.ets_dirs(env), found)

    def test_generator_has_no_absolute_paths(self):
        source = (REPO / "tools" / "gen_product.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"[A-Za-z]:[\\/]")


if __name__ == "__main__":
    unittest.main()
