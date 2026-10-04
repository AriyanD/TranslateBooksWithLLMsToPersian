"""Tiny web UI (standard library only). Run: python app.py  -> http://localhost:7860

Provider keys are kept in your browser (localStorage) and are sent only
with the request that starts a job; the server never writes them to disk."""
from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __repo__, __upstream__, __version__
from .dispatcher import DispatchSettings
from .job import TranslationJob
from .providers import ProviderConfig, ProviderError, chat_completion, list_models
from .prompts import build_system_prompt

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS: dict[str, TranslationJob] = {}
MAX_UPLOAD = 200 * 1024 * 1024


def _settings_from(d: dict) -> DispatchSettings:
    return DispatchSettings(
        max_chars=max(300, min(20000, int(d.get("chunk_chars") or 2500))),
        max_attempts=max(1, min(30, int(d.get("max_attempts") or 6))),
        temperature=float(d.get("temperature") if d.get("temperature") not in (None, "") else 0.3),
        timeout=max(20, float(d.get("timeout") or 240)),
        extra_instructions=str(d.get("instructions") or ""),
        persian_digits=bool(d.get("persian_digits")),
        wait_for_api=bool(d.get("wait_for_api", True)),
        mode="refine" if str(d.get("mode") or "") == "refine" else "translate",
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "PersianBookTranslator/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # ---- helpers ----
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("File too large")
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def _check_token(self) -> bool:
        tok = os.environ.get("PBT_PASSWORD")
        if not tok:
            return True
        return self.headers.get("X-Password") == tok

    # ---- routes ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "web", "index.html"), "rb") as f:
                body = f.read()
            body = (body.replace(b"{{REPO_URL}}", __repo__.encode())
                        .replace(b"{{UPSTREAM_URL}}", __upstream__.encode())
                        .replace(b"{{VERSION}}", __version__.encode()))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")   # always load the newest UI
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._check_token():
            return self._json({"error": "wrong password"}, 401)
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            job = JOBS.get(parts[2])
            if not job:
                return self._json({"error": "job not found"}, 404)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            since = int((q.get("since") or ["0"])[0])
            p = job.progress()
            p["logs"] = job.logs[since:]
            p["log_count"] = len(job.logs)
            return self._json(p)
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "text":
            job = JOBS.get(parts[2])
            if not job or job.output is None:
                return self._json({"error": "no output yet"}, 404)
            if not job.output_name.endswith((".txt", ".md", ".srt")):
                return self._json({"error": "this job's output is not plain text"}, 400)
            return self._json({"text": job.output.decode("utf-8", "replace")})
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "download":
            job = JOBS.get(parts[2])
            if not job or not job.output:
                return self._json({"error": "no output yet"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            fname = urllib.parse.quote(job.output_name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{fname}")
            self.send_header("Content-Length", str(len(job.output)))
            self.end_headers()
            self.wfile.write(job.output)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._check_token():
            return self._json({"error": "wrong password"}, 401)
        try:
            body = self._read_json()
        except Exception as e:
            return self._json({"error": f"bad request: {e}"}, 400)

        if path == "/api/test-provider":
            # Tests every key of the provider, so you see which one is dead / out of quota.
            try:
                cfg = ProviderConfig.from_dict(body.get("provider") or {})
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)[:400]})
            review_only = cfg.use_for == ["review"]
            t_sys = build_system_prompt(mode="refine") if review_only else build_system_prompt()
            t_txt = "پیرمرد به دریا نگاه کرد و لبخند زد." if review_only else \
                "The old man looked at the sea and smiled."
            results, sample, secs = [], "", None
            for i, key in enumerate(cfg.keys or [""], 1):
                t0 = time.time()
                try:
                    out = chat_completion(cfg, t_sys, t_txt, timeout=60, api_key=key)
                    sample, secs = sample or out[:300], secs or round(time.time() - t0, 1)
                    results.append({"key": i, "ok": True})
                except ProviderError as e:
                    results.append({"key": i, "ok": False, "kind": e.kind, "error": str(e)[:200]})
            good = sum(r["ok"] for r in results)
            if not good:
                r0 = results[0]
                msg = r0["error"] if len(results) == 1 else "; ".join(
                    f"key #{r['key']}: {r['kind']}" for r in results)
                return self._json({"ok": False, "error": msg[:400], "keys": results})
            return self._json({"ok": True, "sample": sample, "seconds": secs, "keys": results,
                               "keys_ok": good, "keys_total": len(results)})

        if path == "/api/models":
            try:
                d = dict(body.get("provider") or {})
                d.setdefault("model", "x")
                d["model"] = d["model"] or "x"
                return self._json({"ok": True, "models": list_models(ProviderConfig.from_dict(d))})
            except (ProviderError, ValueError) as e:
                return self._json({"ok": False, "error": str(e)[:400]})

        if path == "/api/jobs":
            try:
                providers = [ProviderConfig.from_dict(p) for p in body.get("providers", [])
                             if p.get("enabled", True)]
                if not providers:
                    raise ValueError("Add at least one enabled provider")
                settings = _settings_from(body.get("settings") or {})
                providers = [p for p in providers if p.used_for(settings.mode)]
                if not providers:
                    what = "review" if settings.mode == "refine" else "translation"
                    raise ValueError(f"No enabled provider is set to be used for {what}. "
                                     f"Tick '{what.capitalize()}' in the 'Use for' column of step 1.")
                names = [p.name for p in providers]
                if len(set(names)) != len(names):
                    raise ValueError("Provider names must be unique")
                if body.get("text") is not None:
                    # "Paste text" page: the text itself is the "file".
                    text = str(body.get("text") or "")
                    if not text.strip():
                        raise ValueError("The text box is empty")
                    data = text.encode("utf-8")
                    filename = "text.txt"
                else:
                    data = base64.b64decode(body.get("file_b64") or "")
                    if not data:
                        raise ValueError("No file uploaded")
                    filename = os.path.basename(str(body.get("filename") or "book.txt"))
                job = TranslationJob(filename, data, providers, settings,
                                     use_checkpoint=bool((body.get("settings") or {}).get("resume", True)))
            except Exception as e:
                return self._json({"error": str(e)}, 400)
            job_id = secrets.token_hex(8)
            JOBS[job_id] = job
            threading.Thread(target=job.run, daemon=True).start()
            return self._json({"job_id": job_id})

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "providers":
            job = JOBS.get(parts[2])
            if not job:
                return self._json({"error": "job not found"}, 404)
            try:
                cfgs = [ProviderConfig.from_dict(p) for p in body.get("providers", [])
                        if p.get("enabled", True)]
                notes = job.add_providers(cfgs)
            except Exception as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True, "notes": notes})

        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            job = JOBS.get(parts[2])
            if not job:
                return self._json({"error": "job not found"}, 404)
            job.cancel.set()
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)


def serve(host: str = "0.0.0.0", port: int = 7860):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"Persian Book Translator running on http://localhost:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
