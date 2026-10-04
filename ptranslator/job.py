"""One translation job: load file -> resume from checkpoint -> split into
chunks -> translate chunks in parallel across providers -> merge -> save."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Callable, Optional

from .dispatcher import Dispatcher, DispatchSettings, make_chunks
from .formats import load_document
from .providers import ProviderConfig, mode_role

DEFAULT_DATA_DIR = os.environ.get("PBT_DATA_DIR", os.path.join(os.getcwd(), "data"))


def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


class Checkpoint:
    """Saves every finished segment so a stopped/crashed job resumes for free."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.data: dict[str, str] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        self._dirty = 0

    def get(self, text: str) -> Optional[str]:
        return self.data.get(_h(text))

    def put_many(self, pairs: list[tuple[str, str]]):
        with self.lock:
            for src, tr in pairs:
                self.data[_h(src)] = tr
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)


class TranslationJob:
    def __init__(self, filename: str, data: bytes, providers: list[ProviderConfig],
                 settings: DispatchSettings, data_dir: str = DEFAULT_DATA_DIR,
                 log: Optional[Callable[[str], None]] = None, use_checkpoint: bool = True):
        self.filename = filename
        self.data = data
        self.settings = settings
        # Only providers set to "Use for" this job type (translate or review) take part.
        self.providers = [p for p in providers if p.used_for(settings.mode)]
        if providers and not self.providers:
            raise ValueError(f"No provider is set to be used for {self.role_label}. "
                             f"Tick '{self.role_label.capitalize()}' for at least one provider.")
        self.data_dir = data_dir
        self.cancel = threading.Event()
        self.logs: list[str] = []
        self._ext_log = log
        self.status = "pending"
        self.error = ""
        self.output: Optional[bytes] = None
        self.output_name = ""
        self.dispatcher: Optional[Dispatcher] = None
        self.total_segments = 0
        self.resumed_segments = 0
        self.started = time.time()
        self.finished: Optional[float] = None
        self.use_checkpoint = use_checkpoint
        self.failed_segments = 0
        self.total_chars = 0
        self.resumed_chars = 0
        self._pending_providers: list[ProviderConfig] = []
        self._plock = threading.Lock()

    @property
    def role_label(self) -> str:
        return "review" if mode_role(self.settings.mode) == "review" else "translation"

    def add_providers(self, cfgs: list[ProviderConfig]) -> list[str]:
        """Add providers / extra API keys to this job, even while it is running.
        Providers not set to be used for this job type are skipped."""
        skipped = [c.name for c in cfgs if not c.used_for(self.settings.mode)]
        cfgs = [c for c in cfgs if c.used_for(self.settings.mode)]
        notes = self._add_providers(cfgs)
        if skipped:
            notes.append(f"skipped {', '.join(skipped)} (not set for {self.role_label})")
        return notes

    def _add_providers(self, cfgs: list[ProviderConfig]) -> list[str]:
        with self._plock:
            if self.dispatcher is not None:
                return self.dispatcher.add_providers(cfgs)
            notes = []
            for c in cfgs:
                old = next((p for p in self.providers if p.name == c.name), None)
                if old:
                    merged = old.keys + [k for k in c.keys if k not in old.keys]
                    old.api_key = ", ".join(merged)
                    notes.append(f"{c.name}: keys merged")
                else:
                    self.providers.append(c)
                    notes.append(f"{c.name}: added")
            return notes

    def log(self, msg: str):
        line = time.strftime("%H:%M:%S ") + msg
        self.logs.append(line)
        if len(self.logs) > 2000:
            self.logs = self.logs[-1500:]
        if self._ext_log:
            self._ext_log(line)

    def run(self) -> Optional[bytes]:
        self.status = "running"
        try:
            doc = load_document(self.filename, self.data)
            segs = doc.segments
            self.total_segments = len(segs)
            self.total_chars = sum(len(x) for x in segs)
            self.log(f"Loaded '{self.filename}': {len(segs)} text segments, "
                     f"{sum(len(s) for s in segs):,} characters.")
            file_hash = hashlib.sha256(self.data).hexdigest()[:20]
            suffix = "-refine" if self.settings.mode == "refine" else ""
            ck = Checkpoint(os.path.join(self.data_dir, "checkpoints", f"{file_hash}{suffix}.json")) \
                if self.use_checkpoint else None
            translations: dict[int, str] = {}
            if ck:
                for i, s in enumerate(segs):
                    t = ck.get(s)
                    if t is not None:
                        translations[i] = t
                self.resumed_segments = len(translations)
                self.resumed_chars = sum(len(segs[i]) for i in translations)
                if translations:
                    self.log(f"Resuming: {len(translations)} segment(s) already translated earlier.")

            chunks = make_chunks(segs, self.settings.max_chars, skip=set(translations))

            def on_result(res: dict):
                if ck:
                    ck.put_many([(segs[i], t) for i, t in res.items()])

            with self._plock:
                self.dispatcher = Dispatcher(self.providers, self.settings, log=self.log,
                                             on_result=on_result, cancel_event=self.cancel)
            results = self.dispatcher.run(chunks)
            translations.update(results)
            self.failed_segments = len(segs) - len(translations)

            if self.cancel.is_set() and self.failed_segments:
                self.status = "cancelled"
                self.log(f"Stopped. {len(translations)}/{len(segs)} segments are saved; "
                         f"start the same file again to continue where it left off.")
            self.output = doc.build(translations)
            base = os.path.splitext(os.path.basename(self.filename))[0]
            if self.settings.mode == "refine":
                base = base[:-3] if base.endswith(".fa") else base
                self.output_name = f"{base}.refined.fa{doc.ext}"
            else:
                self.output_name = f"{base}.fa{doc.ext}"
            if self.status != "cancelled":
                self.status = "done"
                if self.failed_segments:
                    self.log(f"Finished with {self.failed_segments} segment(s) left untranslated "
                             f"(all providers failed on them). Run the same file again to retry only those.")
                else:
                    self.log("Finished. All segments " + ("refined" if self.settings.mode == "refine"
                                                          else "translated") + " and merged.")
            return self.output
        except Exception as e:
            self.status = "error"
            self.error = f"{type(e).__name__}: {e}"
            self.log(f"Error: {self.error}")
            return None
        finally:
            self.finished = time.time()

    def progress(self) -> dict:
        st = self.dispatcher.stats() if self.dispatcher else {
            "total_chunks": 0, "done_chunks": 0, "failed_segments": 0, "providers": [],
            "done_chars": 0, "total_chars": 0, "eta_seconds": None, "waiting": False,
            "wait_reason": "", "last": None, "recent": [], "chars_per_min": None}
        # Percent is by characters (chunks differ a lot in size) and counts resumed text.
        if self.status == "done":
            pct = 100.0
        elif self.total_chars:
            pct = round(100.0 * (self.resumed_chars + st["done_chars"]) / self.total_chars, 1)
        else:
            pct = 0.0
        status = self.status
        if status == "running" and st.get("waiting"):
            status = "waiting"
        if self.status != "running":
            st["eta_seconds"] = None
        end = self.finished or time.time()
        return {
            "status": status, "error": self.error, "percent": min(pct, 100.0),
            "mode": self.settings.mode,
            "elapsed": int(end - self.started), "total_segments": self.total_segments,
            "resumed_segments": self.resumed_segments, "failed_segments": self.failed_segments,
            "output_name": self.output_name, **st,
        }
