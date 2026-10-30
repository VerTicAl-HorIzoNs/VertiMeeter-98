"""Find installed LV2 plugins by reading their manifest files (no lilv needed)."""
from __future__ import annotations

import os
import re

DEFAULT_DIRS = ["/usr/lib/lv2", "/usr/local/lib/lv2", "/usr/lib64/lv2", "~/.lv2", "~/.local/lib/lv2"]
# plugin classes that are not audio effects (the host loop needs audio in AND out)
SKIP = re.compile(r"InstrumentPlugin|GeneratorPlugin|AnalyserPlugin|MIDIPlugin|ConverterPlugin\b", re.I)


def search_dirs() -> list[str]:
    env = os.environ.get("LV2_PATH", "")
    dirs = [d for d in env.split(":") if d] + DEFAULT_DIRS
    out = []
    for d in dirs:
        d = os.path.expanduser(d)
        if os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def _read(path: str) -> str:
    try:
        with open(path, errors="ignore") as f:
            return f.read(400_000)
    except OSError:
        return ""


def scan(dirs: list[str] | None = None) -> list[tuple[str, str, str]]:
    """[(name, uri, bundle)] sorted by name. Instruments / generators / analysers are left out."""
    found: dict[str, tuple[str, str, str]] = {}
    for d in dirs or search_dirs():
        try:
            bundles = sorted(os.listdir(d))
        except OSError:
            continue
        for b in bundles:
            bp = os.path.join(d, b)
            man = os.path.join(bp, "manifest.ttl")
            if not b.endswith(".lv2") or not os.path.isfile(man):
                continue
            text = _read(man)
            # every file the manifest points at may carry the names / classes
            for see in re.findall(r"rdfs:seeAlso\s+<([^>]+\.ttl)>", text):
                text += "\n" + _read(os.path.join(bp, see))
            for m in re.finditer(r"(<[^>\s]+>)\s+a\s+([^.]*?)\.", text, re.S):
                uri, classes = m.group(1)[1:-1], m.group(2)
                if "lv2:" not in classes and "Plugin" not in classes:
                    continue
                if "Plugin" not in classes or SKIP.search(classes):
                    continue
                nm = re.search(r"doap:name\s+\"([^\"]+)\"", classes)
                if not nm:  # the name often sits in a separate statement about the same URI
                    blk = re.search(re.escape(m.group(1)) + r"[^.]*?doap:name\s+\"([^\"]+)\"", text, re.S)
                    nm = blk
                name = nm.group(1) if nm else uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
                found.setdefault(uri, (name, uri, bp))
    return sorted(found.values(), key=lambda t: t[0].lower())
