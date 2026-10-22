#!/usr/bin/env python3
"""Generate OpenPLC_TP.xml, sign it into OpenPLC_TP.knxprod and write the board
package header openplc_knx_objects.h, all from knx_objects.json.

See README.md "Object map" and "Regenerating the product". Windows only (decision 143).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
TEMPLATE = TOOLS_DIR / "product_template.xml"
CONFIG_NAME = "knx_objects.json"
XML_NAME = "OpenPLC_TP.xml"
KNXPROD_NAME = "OpenPLC_TP.knxprod"
HEADER_REL = Path("libraries") / "OpenPLC_KNX" / "src" / "openplc_knx_objects.h"
ETS_DLL = "Knx.Ets.Xml.ObjectModel.dll"
ETS_PATHS = TOOLS_DIR / "ets_paths.json"
PRODUCER = TOOLS_DIR / "OpenKNXproducer" / "OpenKNXproducer-x64.exe"

# Flag letter -> ComObject attribute, in the attribute order of the product file.
FLAG_ATTRS = [
    ("R", "ReadFlag"),
    ("W", "WriteFlag"),
    ("C", "CommunicationFlag"),
    ("T", "TransmitFlag"),
    ("U", "UpdateFlag"),
    ("I", "ReadOnInitFlag"),
]
FLAG_ORDER = "CRWTUI"
LIST_PLACEHOLDERS = ("COM_OBJECTS", "COM_OBJECT_REFS", "COM_OBJECT_REF_REFS")


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- config

def load_config(path):
    """Read and validate knx_objects.json; objects come back sorted by number."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ConfigError("top level must be an object")

    version = raw.get("application_version")
    if version is None:
        raise ConfigError("application_version is missing")
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 255:
        raise ConfigError("application_version must be an integer 1-255")

    replaces = raw.get("replaces_versions", [])
    if not isinstance(replaces, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) and 1 <= v < version for v in replaces):
        raise ConfigError("replaces_versions must list integers below application_version")

    order = raw.get("order_number")
    if not isinstance(order, str) or not re.fullmatch(r"[\x21-\x7e]+", order) or '"' in order or "\\" in order:
        raise ConfigError("order_number must be printable ASCII without spaces, quotes or backslashes")

    objects = raw.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ConfigError("objects must be a non-empty list")

    seen_numbers, seen_keys, out = set(), set(), []
    for i, o in enumerate(objects):
        where = "objects[%d]" % i
        if not isinstance(o, dict):
            raise ConfigError(where + " must be an object")
        n = o.get("number")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ConfigError(where + ": number must be a positive integer")
        key = o.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ConfigError(where + ": key must be an upper-case C name")
        for field in ("name", "function", "size", "dpt"):
            if not isinstance(o.get(field), str) or not o[field]:
                raise ConfigError("%s: %s must be a non-empty string" % (where, field))
        flags = o.get("flags")
        if not isinstance(flags, str) or any(c not in FLAG_ORDER for c in flags) or len(set(flags)) != len(flags):
            raise ConfigError(where + ": flags must be letters from " + FLAG_ORDER)
        port = o.get("port")
        if port is not None and not isinstance(port, str):
            raise ConfigError(where + ": port must be a string or null")
        if n in seen_numbers:
            raise ConfigError("%s: number %d is used twice" % (where, n))
        if key in seen_keys:
            raise ConfigError("%s: key %s is used twice" % (where, key))
        seen_numbers.add(n)
        seen_keys.add(key)
        out.append({
            "number": n, "key": key, "name": o["name"], "function": o["function"],
            "port": port, "size": o["size"], "dpt": o["dpt"],
            "flags": "".join(c for c in FLAG_ORDER if c in flags),
            "note": o.get("note", ""),
        })

    out.sort(key=lambda o: o["number"])
    return {
        "application_version": version,
        "replaces_versions": list(replaces),
        "order_number": order,
        "objects": out,
    }


# ---------------------------------------------------------------- rendering

def id_escape(text):
    """Order number as written inside an ID: non-alphanumerics become '.' + hex."""
    return "".join(c if c.isascii() and c.isalnum() else ".%02X" % ord(c) for c in text)


def _attr(text):
    return escape(text, {'"': "&quot;"})


def render_xml(cfg, template_text):
    ver = "%02X" % cfg["application_version"]
    app_id = "M-00FA_A-0001-%s-0000" % ver
    objs = cfg["objects"]

    lists = {
        "COM_OBJECTS": [
            '<ComObject Id="{a}_O-{n}" Name="{name}" Text="{name}" Number="{n}" FunctionText="{fn}" '
            'ObjectSize="{size}" {flags} DatapointType="{dpt}" />'.format(
                a=app_id, n=o["number"], name=_attr(o["name"]), fn=_attr(o["function"]),
                size=_attr(o["size"]), dpt=_attr(o["dpt"]),
                flags=" ".join('%s="%s"' % (attr, "Enabled" if c in o["flags"] else "Disabled")
                               for c, attr in FLAG_ATTRS))
            for o in objs],
        "COM_OBJECT_REFS": [
            '<ComObjectRef Id="{a}_O-{n}_R-{n}" RefId="{a}_O-{n}" />'.format(a=app_id, n=o["number"])
            for o in objs],
        "COM_OBJECT_REF_REFS": [
            '<ComObjectRefRef RefId="{a}_O-{n}_R-{n}" />'.format(a=app_id, n=o["number"])
            for o in objs],
    }
    scalars = {
        "VER": ver,
        "APP_VERSION": str(cfg["application_version"]),
        "REPLACES_VERSIONS": " ".join(str(v) for v in cfg["replaces_versions"]),
        "ORDER_ID": id_escape(cfg["order_number"]),
        "ORDER_NUMBER": _attr(cfg["order_number"]),
    }

    pattern = re.compile(r"^([ \t]*)\$\{(%s)\}[ \t]*$|\$\{([A-Z_]+)\}" % "|".join(LIST_PLACEHOLDERS), re.M)

    def sub(m):
        if m.group(2):
            return "\n".join(m.group(1) + line for line in lists[m.group(2)])
        return scalars[m.group(3)]

    return pattern.sub(sub, template_text)


def _c_comment(text):
    return text.replace("*/", "* /")


def render_header(cfg):
    """Board package header; pure and deterministic (OpenPLC_Test P2 recomputes it)."""
    objs = cfg["objects"]
    macros = ["KNX_OBJ_" + o["key"] for o in objs]
    width = max(len(m) for m in macros)
    num_width = max(len(str(o["number"])) for o in objs)
    port_width = max(len(o["port"] or "-") for o in objs)
    lines = [
        "/* Generated from knx_objects.json by tools/gen_product.py (OpenPLC_ETS_Prod).",
        " * Do not edit: change knx_objects.json and run the generator again. */",
        "#ifndef OPENPLC_KNX_OBJECTS_H",
        "#define OPENPLC_KNX_OBJECTS_H",
        "",
        '#define OPENPLC_KNX_ORDER_NUMBER "%s"' % cfg["order_number"],
        "#define OPENPLC_KNX_APP_VERSION %d" % cfg["application_version"],
        "",
        "/* Group object numbers: port, name, DPT, flags */",
    ]
    for m, o in zip(macros, objs):
        lines.append("#define %s %s  /* %s %s, %s, %s */" % (
            m.ljust(width), str(o["number"]).rjust(num_width), (o["port"] or "-").ljust(port_width),
            _c_comment(o["name"]), _c_comment(o["dpt"]), o["flags"]))
    lines += ["", "#endif /* OPENPLC_KNX_OBJECTS_H */", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- change warnings

def read_product_objects(xml_path):
    """(application_version, {number: object}) from a product file, or None if absent."""
    path = Path(xml_path)
    if not path.is_file():
        return None
    root = ET.parse(path).getroot()
    version = None
    for el in root.iter():
        if el.tag.endswith("}ApplicationProgram"):
            version = int(el.get("ApplicationVersion"))
            break
    objects = {}
    for el in root.iter():
        if el.tag.endswith("}ComObject"):
            n = int(el.get("Number"))
            objects[n] = {
                "number": n, "name": el.get("Name"), "function": el.get("FunctionText"),
                "size": el.get("ObjectSize"), "dpt": el.get("DatapointType"),
                "flags": "".join(c for c in FLAG_ORDER
                                 if el.get(dict(FLAG_ATTRS)[c]) == "Enabled"),
            }
    return version, objects


def change_warnings(baseline, cfg):
    """Warnings for changes that shift users' group address links, and for an unchanged version."""
    if baseline is None:
        return []
    old_version, old_objs = baseline
    new_by_num = {o["number"]: o for o in cfg["objects"]}
    new_by_name = {o["name"]: o for o in cfg["objects"]}
    old_names = {o["name"] for o in old_objs.values()}
    warnings = []

    for n in sorted(old_objs):
        old = old_objs[n]
        new = new_by_name.get(old["name"])
        if new is None:
            cand = new_by_num.get(n)
            if cand is not None and cand["name"] not in old_names:
                new = cand  # renamed in place
        if new is None:
            warnings.append("object %d '%s' was removed" % (n, old["name"]))
            continue
        if new["number"] != n:
            warnings.append("object '%s' changed number %d -> %d" % (old["name"], n, new["number"]))
        if new["dpt"] != old["dpt"]:
            warnings.append("object %d '%s' changed DPT %s -> %s" % (n, old["name"], old["dpt"], new["dpt"]))
        if new["flags"] != old["flags"]:
            warnings.append("object %d '%s' changed flags %s -> %s" % (n, old["name"], old["flags"], new["flags"]))
    if warnings:
        warnings.append("group address links in existing ETS projects will shift")

    fields = ("number", "name", "function", "size", "dpt", "flags")
    old_set = {tuple(o[f] for f in fields) for o in old_objs.values()}
    new_set = {tuple(o[f] for f in fields) for o in cfg["objects"]}
    if old_set != new_set and old_version == cfg["application_version"]:
        warnings.append("objects changed but application_version is still %d; raise it by one"
                        % cfg["application_version"])
    return warnings


# ---------------------------------------------------------------- ETS / signing

def file_version(path):
    """File version (major, minor, build, revision) of a Windows binary, or None."""
    import ctypes
    from ctypes import wintypes
    ver = ctypes.WinDLL("version")
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)) or length.value < 16:
        return None
    info = ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD * 4)).contents  # signature, struct ver, MS, LS
    ms, ls = info[2], info[3]
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def ets_candidates(env, paths_file=ETS_PATHS):
    """Candidate ETS folders from ets_paths.json, %VAR% expanded; unresolvable ones are skipped."""
    with open(paths_file, encoding="utf-8") as f:
        spec = json.load(f)
    defaults = spec.get("defaults", {})
    dirs = []
    for pattern in spec["candidates"]:
        missing = []

        def var(m):
            value = env.get(m.group(1)) or defaults.get(m.group(1))
            if not value:
                missing.append(m.group(1))
            return value or ""

        path = re.sub(r"%([^%]+)%", var, pattern)
        if missing:
            continue
        if path.endswith(("\\*", "/*")):
            parent = Path(path[:-2])
            if parent.is_dir():
                dirs += sorted(p for p in parent.iterdir() if p.is_dir())
        else:
            dirs.append(Path(path))
    return dirs


def ets_dirs(env, paths_file=ETS_PATHS):
    """ETS folders in OpenKNXproducer's search order that hold the ETS object model DLL."""
    return [d for d in ets_candidates(env, paths_file) if (d / ETS_DLL).is_file()]


def check_ets(env):
    """None if an ETS 5.7 is installed, otherwise the error message."""
    found = []
    for d in ets_dirs(env):
        v = file_version(d / ETS_DLL)
        if v is not None and v[:2] == (5, 7):
            return None
        found.append("%s (%s)" % (d, ".".join(map(str, v)) if v else "version unknown"))
    if not found:
        return "ETS not found; signing needs ETS 5.7 installed"
    return "only ETS 5.7 is supported for now; found: " + ", ".join(found)


# ---------------------------------------------------------------- main

def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main(argv=None, env=None):
    env = os.environ if env is None else env
    if sys.platform != "win32":
        print("Windows only (decision 143)", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(description="Generate the OpenPLC ETS product and board header "
                                             "from knx_objects.json.")
    ap.add_argument("--root", default=str(REPO_ROOT), help="OpenPLC_ETS_Prod checkout (default: this repo)")
    ap.add_argument("--core", help="OpenPLC_Arduino_PKG checkout (default: ../OpenPLC_Arduino_PKG from the repo root)")
    ap.add_argument("--producer", help="OpenKNXproducer exe (default: tools/OpenKNXproducer/OpenKNXproducer-x64.exe)")
    ap.add_argument("--no-sign", action="store_true", help="write XML and header only")
    args = ap.parse_args(argv)

    root = Path(args.root)
    core = Path(args.core) if args.core else root.parent / "OpenPLC_Arduino_PKG"
    xml_path = root / XML_NAME
    header_path = core / HEADER_REL

    try:
        cfg = load_config(root / CONFIG_NAME)
    except (OSError, ValueError) as e:
        print("error: %s: %s" % (CONFIG_NAME, e), file=sys.stderr)
        return 1
    if not header_path.parent.is_dir():
        print("error: %s not found; pass --core" % header_path.parent, file=sys.stderr)
        return 1

    try:
        baseline = read_product_objects(xml_path)
    except (ET.ParseError, TypeError, ValueError) as e:
        print("warning: cannot read %s for change checks: %s" % (XML_NAME, e))
        baseline = None
    for w in change_warnings(baseline, cfg):
        print("warning: " + w)

    write_text(xml_path, render_xml(cfg, TEMPLATE.read_text(encoding="utf-8")))
    print("wrote " + str(xml_path))
    write_text(header_path, render_header(cfg))
    print("wrote " + str(header_path))

    if args.no_sign:
        return 0

    producer = Path(args.producer) if args.producer else PRODUCER
    if not producer.is_file():
        print("not signed: %s not found; pass --producer <OpenKNXproducer-x64.exe>" % producer,
              file=sys.stderr)
        return 1
    err = check_ets(env)
    if err:
        print("error: " + err, file=sys.stderr)
        return 1

    rc = subprocess.run([str(producer), "knxprod", "-o", KNXPROD_NAME, XML_NAME], cwd=root).returncode
    if rc != 0:
        print("error: OpenKNXproducer exited with %d" % rc, file=sys.stderr)
        return 1
    print("wrote " + str(root / KNXPROD_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())
