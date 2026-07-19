"""Contract for sandbox_denylist.py -- R9 layer-1 static scan.

Pins: every denylist class (network/exec/env/escape-fs/dynamic-import) fires
on its trigger and only on its trigger (AST-based, so a variable named
`requests_count` or a string literal `"import socket"` must NOT match); a
source that fails to parse is a distinct refuse signal, not a silent pass;
the non-python regex lane is a coarser fallback for wave 2 languages; and the
CLI's three exit codes (clean/finding/parse-error) match the contract the
sandbox runner (a later phase) will drive off of.
"""

import importlib.util
import subprocess
import sys

import pytest

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = (
    _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "sandbox_denylist.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("sandbox_denylist", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: the module uses `from __future__ import annotations`,
    # so its frozen dataclass resolves string annotations via sys.modules[__module__]
    # at class-definition time -- omitting this raises AttributeError there.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(args):
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + list(args),
        capture_output=True, text=True,
    )


def test_clean_fill_passes():
    mod = _load()
    source = (
        "import re\n"
        "import unicodedata\n"
        "import json\n"
        "\n"
        "def normalize(s):\n"
        "    return unicodedata.normalize('NFC', re.sub(r'\\s+', ' ', s)).strip()\n"
        "\n"
        "json.dumps({'a': 1})\n"
    )
    assert mod.scan_source(source) == []


def test_network_class():
    mod = _load()
    cases = [
        "import requests\n",
        "from urllib import request\n",
        "import urllib.request as x\n",
        "import socket\n",
        "import _socket\n",  # mandatory: the C-ext under stdlib socket, easy to miss
    ]
    for source in cases:
        findings = mod.scan_source(source)
        assert findings, f"expected a network finding for: {source!r}"
        assert all(f.code == "network" for f in findings), source


def test_exec_class():
    mod = _load()
    cases = {
        'eval("x")\n': 1,
        'os.system("id")\n': 1,
        "os.fork()\n": 1,  # mandatory: a grandchild fork survives timeout+cleanup
        'os.posix_spawn("/bin/true", ["true"], {})\n': 1,
        "import subprocess\n": 1,
    }
    for source, expected_line in cases.items():
        findings = mod.scan_source(source)
        assert findings, source
        assert findings[0].code == "exec"
        assert findings[0].line == expected_line


def test_env_class():
    mod = _load()
    for source in ('os.environ["K"]\n', 'os.getenv("A")\n'):
        findings = mod.scan_source(source)
        assert findings and findings[0].code == "env"


def test_escape_fs_class():
    mod = _load()
    findings = mod.scan_source('open("/etc/passwd", "w")\n')
    assert findings and findings[0].code == "escape-fs"
    findings = mod.scan_source("import shutil\nshutil.rmtree('/tmp/x')\n")
    assert any(f.code == "escape-fs" for f in findings)


def test_dynamic_import_class():
    mod = _load()
    findings = mod.scan_source("import importlib\n")
    assert findings and findings[0].code == "dynamic-import"


def test_no_false_positive_on_names():
    mod = _load()
    source = (
        "requests_count = 1\n"
        's = "import socket"\n'
        "os = requests_count\n"
    )
    assert mod.scan_source(source) == []


def test_os_from_import_and_alias_bypass():
    # A literal `os.system(...)` is caught, but the idiomatic indirections
    # `from os import system`, `import os as o; o.system()`, and the env
    # readers reached the same way must not slip through CLEAN -- the nonce
    # anti-forgery guarantee in the runner rests on os env-reads being denied.
    mod = _load()
    exec_cases = [
        "from os import system\nsystem('id')\n",
        "from os import system as s\ns('id')\n",
        "import os as o\no.system('id')\n",
        "from os import fork\nfork()\n",
    ]
    for source in exec_cases:
        findings = mod.scan_source(source)
        assert findings, f"expected an exec finding for: {source!r}"
        assert any(f.code == "exec" for f in findings), source

    env_cases = [
        "from os import environ\nenviron.get('HARNESS_R9_NONCE')\n",
        "from os import getenv\ngetenv('HARNESS_R9_NONCE')\n",
        "import os as o\no.getenv('X')\n",
        "import os as o\no.environ['K']\n",
    ]
    for source in env_cases:
        findings = mod.scan_source(source)
        assert findings, f"expected an env finding for: {source!r}"
        assert any(f.code == "env" for f in findings), source


def test_os_indirection_no_false_positive():
    # An innocent os submodule import and a bare os-alias with no dangerous use
    # must stay CLEAN -- the hardening must not over-refuse ordinary code-fill.
    mod = _load()
    assert mod.scan_source("from os import path\nx = path.join('a', 'b')\n") == []
    assert mod.scan_source("import os as o\ncwd = o.getcwd()\n") == []


def test_path_variable_absolute_write_escape():
    # `Path('/abs').write_text()` inline is caught; the same via a variable
    # binding must be caught too -- both are the identical escape-fs class.
    mod = _load()
    findings = mod.scan_source("from pathlib import Path\np = Path('/etc/passwd')\np.write_text('x')\n")
    assert findings and any(f.code == "escape-fs" for f in findings)
    # a relative-path variable write stays clean (best-effort: only absolute)
    assert mod.scan_source("from pathlib import Path\np = Path('out.txt')\np.write_text('x')\n") == []


def test_syntax_error_refuses():
    mod = _load()
    with pytest.raises(SyntaxError):
        mod.scan_source("def (:\n")


def test_cli_exit_codes(tmp_path):
    clean = tmp_path / "clean.py"
    clean.write_text("import json\njson.dumps({})\n", encoding="utf-8")
    result = _run([str(clean)])
    assert result.returncode == 0, result.stdout + result.stderr

    dirty = tmp_path / "dirty.py"
    dirty.write_text("import socket\n", encoding="utf-8")
    result = _run([str(dirty)])
    assert result.returncode == 1
    assert "network" in result.stdout

    broken = tmp_path / "broken.py"
    broken.write_text("def (:\n", encoding="utf-8")
    result = _run([str(broken)])
    assert result.returncode == 2


def test_cli_unreadable_file_is_refuse_not_finding(tmp_path):
    # A missing / unreadable / non-UTF-8 file cannot be scanned -- it must fold
    # into the exit-2 refuse bucket (like a parse error), never collide with the
    # exit-1 "finding" code or crash out with an uncaught traceback.
    missing = _run([str(tmp_path / "does_not_exist.py")])
    assert missing.returncode == 2, missing.stdout + missing.stderr
    assert "Traceback" not in missing.stderr

    d = tmp_path / "adir.py"
    d.mkdir()
    isdir = _run([str(d)])
    assert isdir.returncode == 2 and "Traceback" not in isdir.stderr

    binf = tmp_path / "bin.py"
    binf.write_bytes(b"\xff\xfe not utf-8\n")
    nonutf = _run([str(binf)])
    assert nonutf.returncode == 2 and "Traceback" not in nonutf.stderr


def test_regex_lane_js():
    mod = _load()
    source = 'fetch("http://x");\nconsole.log(process.env.SECRET);\n'
    findings = mod.scan_source(source, lang="js")
    assert findings


def test_multiline_and_unicode_source():
    mod = _load()
    source = (
        '"""Mo-dun xu ly du lieu tieng Viet: chuan hoa chuoi.\n'
        "Khong mang, khong exec, khong doc env.\n"
        '"""\n'
        "import re\n"
        "\n"
        "def clean(s):\n"
        "    return re.sub(r'\\s+', ' ', s).strip()\n"
    )
    assert mod.scan_source(source) == []
