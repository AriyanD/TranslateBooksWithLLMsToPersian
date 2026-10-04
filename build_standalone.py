"""Build a single-file program for THIS operating system (PyInstaller does not cross-compile).

    pip install -r requirements.txt pyinstaller
    python build_standalone.py

Result: dist/PersianBookTranslator(.exe). GitHub Actions runs this on Windows, macOS and
Linux automatically for every release (see .github/workflows/build.yml).
"""
import os
import subprocess
import sys

sep = ";" if os.name == "nt" else ":"
cmd = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
    "--name", "PersianBookTranslator",
    "--add-data", f"ptranslator{os.sep}web{sep}ptranslator{os.sep}web",
    "--collect-submodules", "ptranslator", "--collect-submodules", "pypdf",
    "--collect-data", "docx",
    "--hidden-import", "lxml.etree", "--hidden-import", "lxml._elementpath",
    "--hidden-import", "pypdf", "--hidden-import", "docx",
    "launcher.py",
]
sys.exit(subprocess.call(cmd))
