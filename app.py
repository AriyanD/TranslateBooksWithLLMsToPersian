"""Start the web UI:  python app.py [--port 7860] [--host 0.0.0.0]"""
import argparse
import os

from ptranslator.server import serve

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7860)))
    a = ap.parse_args()
    serve(a.host, a.port)
