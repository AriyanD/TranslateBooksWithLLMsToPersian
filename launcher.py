"""Standalone launcher: double-click and use.

Starts the web UI on 127.0.0.1 (this computer only), picks a free port if
7860 is busy, opens your browser, and keeps your data (checkpoints) in a
folder that always exists and is writable.

Data folder:
  * next to the program, in  PersianBookTranslator_Data/   (portable use), or
  * your home folder if the program folder is read-only.
Override with the PBT_DATA_DIR environment variable.
"""
import os
import socket
import sys
import threading
import webbrowser


def _data_dir() -> str:
    if os.environ.get("PBT_DATA_DIR"):
        return os.environ["PBT_DATA_DIR"]
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))
    for root in (base, os.path.expanduser("~")):
        path = os.path.join(root, "PersianBookTranslator_Data")
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".w")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return path
        except OSError:
            continue
    return os.getcwd()


def _free_port(preferred: int) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def selftest() -> int:
    """Used by the build to prove the packaged program has everything it needs
    (libraries, python-docx template, web page). Exits 0 when all is fine."""
    import io
    import urllib.request

    os.environ.setdefault("PBT_DATA_DIR", _data_dir())
    problems = []

    def check(name, fn):
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:  # noqa: BLE001
            problems.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")

    def libs():
        import lxml.etree  # noqa: F401
        import pypdf  # noqa: F401
        import docx
        docx.Document()  # needs docx's bundled default template

    def formats():
        from ptranslator.formats import load_pdf, load_docx, load_epub, load_txt, load_srt  # noqa: F401
        d = load_txt("Hello world.\n\nSecond paragraph.".encode())
        assert d is not None

    def web():
        from ptranslator.server import serve
        port = _free_port(7900)
        threading.Thread(target=serve, args=("127.0.0.1", port), daemon=True).start()
        import time
        time.sleep(1)
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read().decode("utf-8")
        assert "Persian Book Translator" in html and "{{" not in html and "const ROLES" in html

    check("libraries (lxml, pypdf, python-docx + template)", libs)
    check("format loaders", formats)
    check("web UI served", web)
    print("SELFTEST " + ("FAILED: " + ", ".join(problems) if problems else "PASSED"))
    return 1 if problems else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    # Must be set BEFORE importing ptranslator (job.py reads it at import time).
    os.environ["PBT_DATA_DIR"] = _data_dir()
    port = _free_port(int(os.environ.get("PORT", 7860)))
    if not port:
        print("No free port found (7860-7879). Close other programs and try again.")
        input("Press Enter to exit...")
        return 1

    from ptranslator.server import serve

    url = f"http://127.0.0.1:{port}"
    print(f"Data folder: {os.environ['PBT_DATA_DIR']}")
    print(f"Opening {url}  - keep this window open while translating (close it to quit).")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    serve("127.0.0.1", port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
