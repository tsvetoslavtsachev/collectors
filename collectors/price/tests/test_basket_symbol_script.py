# -*- coding: utf-8 -*-
"""Runs the script-style gate test_basket_symbol.py as CI does and asserts it ends GREEN."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_basket_symbol_script_is_green():
    env = dict(os.environ)
    root = HERE.parents[2]                                   # collectors repo root
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    r = subprocess.run([sys.executable, str(HERE / "test_basket_symbol.py")],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    assert "GREEN" in r.stdout
