"""Parallel multi-provider dispatcher.

Each provider gets ``concurrency`` worker threads. All workers pull chunks
from one shared queue, so every chunk goes to whichever provider is free:
faster providers simply take more chunks. When a provider fails, the chunk
goes back to the queue and is preferably handed to a *different* provider.
At the end, results are merged back in the original order.

Keys: a provider may have several API keys ("key1, key2, key3"). Requests
rotate over them; a key that is rate limited, out of quota/credits or
rejected is parked and the next key is used immediately.

When *nothing* is usable any more (every key rejected or out of quota) the
job does not throw the rest of the book away: it pauses in a "waiting for
API" state, keeps all progress, and continues as soon as you add a new
provider or key (web UI: "Add to running job"; CLI: edit providers.json).
"""
from __future__ import annotations

import collections
import queue
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .prompts import build_system_prompt, build_user_prompt, parse_response, normalize_persian
from .providers import ProviderConfig, ProviderError, chat_completion, split_keys

QUOTA_COOLDOWN = 30 * 60        # first park time for a key that ran out of quota
QUOTA_COOLDOWN_MAX = 6 * 3600   # doubles on every repeat, up to this
ETA_WINDOW = 300                # seconds of recent history used for the speed estimate


@dataclass
class Chunk:
    id: int
    seg_ids: list[int]            # indexes into the global segment list
    texts: list[str]
    attempts: int = 0
    format_failures: int = 0
    tried: set = field(default_factory=set)
    last_error: str = ""

    @property
    def chars(self) -> int:
        return sum(len(t) for t in self.texts)


@dataclass
class KeyState:
    key: str
    cooldown_until: float = 0.0
    dead: bool = False             # rejected (401/403): never used again
    quota_hits: int = 0            # times it reported "out of quota/credits"
    reason: str = ""               # last problem, for the UI
    kind: str = ""                 # "rate_limit" | "quota" | "auth" | ""
    uses: int = 0

    def usable(self, now: float) -> bool:
        return not self.dead and self.cooldown_until <= now

    def label(self) -> str:
        k = self.key
        return (k[:4] + "…" + k[-4:]) if len(k) > 10 else ("(no key)" if not k else "…")


@dataclass
class ProviderState:
    cfg: ProviderConfig
    keys: list = field(default_factory=list)
    key_idx: int = 0
    done: int = 0
    failed: int = 0
    chars: int = 0
    busy: int = 0
    disabled: bool = False
    disabled_reason: str = ""
    cooldown_until: float = 0.0     # provider-wide pause (server errors etc.)
    consecutive_errors: int = 0
    total_seconds: float = 0.0
    threads: int = 0

    def __post_init__(self):
        if not self.keys:
            self.keys = [KeyState(k) for k in (self.cfg.keys or [""])]

    # -- keys --
    def add_keys(self, keys: list[str]) -> int:
        have = {k.key for k in self.keys}
        added = 0
        for k in keys:
            if k and k not in have:
                self.keys.append(KeyState(k))
                have.add(k)
                added += 1
        # A key-less placeholder is useless once real keys exist.
        if added:
            self.keys = [k for k in self.keys if k.key] or self.keys
            if self.disabled and self.disabled_reason.startswith("all keys"):
                self.disabled = False
                self.disabled_reason = ""
        return added

    def next_key(self, now: float) -> Optional[KeyState]:
        n = len(self.keys)
        for i in range(n):
            k = self.keys[(self.key_idx + i) % n]
            if k.usable(now):
                self.key_idx = (self.key_idx + i + 1) % n
                return k
        return None

    def live_keys(self) -> list:
        return [k for k in self.keys if not k.dead]

    def usable_now(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        return (not self.disabled and self.cooldown_until <= now
                and any(k.usable(now) for k in self.keys))

    def only_quota_left(self, now: float) -> bool:
        """True if every non-dead key is parked because it is out of quota."""
        live = self.live_keys()
        return bool(live) and all(k.kind == "quota" and k.cooldown_until > now for k in live)

    def snapshot(self) -> dict:
        now = time.time()
        live = self.live_keys()
        usable = [k for k in self.keys if k.usable(now)]
        if self.disabled:
            status = "disabled"
        elif not usable:
            wait = min((k.cooldown_until for k in live), default=now) - now
            what = "out of quota" if self.only_quota_left(now) else "rate limited"
            status = f"all keys {what} (retry in {_fmt(wait)})"
        elif self.cooldown_until > now:
            status = f"cooling down ({int(self.cooldown_until - now)}s)"
        elif self.busy:
            status = "working"
        else:
            status = "idle"
        return {
            "name": self.cfg.name, "model": self.cfg.model,
            "concurrency": self.cfg.concurrency, "done": self.done,
            "failed": self.failed, "chars": self.chars, "status": status,
            "reason": self.disabled_reason,
            "avg_seconds": round(self.total_seconds / self.done, 1) if self.done else None,
            "keys_total": len(self.keys), "keys_usable": len(usable), "keys_dead": len(self.keys) - len(live),
            "keys": [{"key": k.label(), "uses": k.uses, "dead": k.dead, "kind": k.kind,
                      "usable": k.usable(now), "reason": k.reason[:160],
                      "retry_in": max(0, int(k.cooldown_until - now))} for k in self.keys],
        }


def _fmt(sec: float) -> str:
    sec = max(0, int(sec))
    if sec >= 3600:
        return f"{sec // 3600}h {sec % 3600 // 60}m"
    if sec >= 60:
        return f"{sec // 60}m {sec % 60}s"
    return f"{sec}s"


@dataclass
class DispatchSettings:
    max_chars: int = 2500                 # target size of one chunk sent to an API
    max_attempts: int = 6                 # per chunk, across all providers (real errors only)
    temperature: float = 0.3
    timeout: float = 240
    extra_instructions: str = ""
    persian_digits: bool = False
    max_consecutive_errors: int = 5       # then a provider is paused for a while
    wait_for_api: bool = True             # pause (instead of stopping) when no API is usable
    mode: str = "translate"               # "translate" or "refine" (polish an existing Persian text)


def make_chunks(segments: list[str], max_chars: int,
                skip: Optional[set] = None) -> list[Chunk]:
    """Group consecutive segments into chunks of roughly ``max_chars``."""
    skip = skip or set()
    chunks: list[Chunk] = []
    cur_ids: list[int] = []
    cur_txt: list[str] = []
    size = 0
    for i, s in enumerate(segments):
        if i in skip:
            continue
        L = len(s) + 12
        if cur_ids and size + L > max_chars:
            chunks.append(Chunk(len(chunks), cur_ids, cur_txt))
            cur_ids, cur_txt, size = [], [], 0
        cur_ids.append(i)
        cur_txt.append(s)
        size += L
    if cur_ids:
        chunks.append(Chunk(len(chunks), cur_ids, cur_txt))
    return chunks


class Dispatcher:
    def __init__(self, providers: list[ProviderConfig], settings: DispatchSettings,
                 log: Callable[[str], None] = print,
                 on_result: Optional[Callable[[dict], None]] = None,
                 cancel_event: Optional[threading.Event] = None):
        if not providers:
            raise ValueError("Add at least one provider")
        self.states = [ProviderState(p) for p in providers if p.enabled]
        if not self.states:
            raise ValueError("All providers are disabled")
        self.settings = settings
        self.system_prompt = build_system_prompt(settings.extra_instructions, settings.mode)
        self.log = log
        self.on_result = on_result          # called with {seg_id: translation} per finished chunk
        self.cancel = cancel_event or threading.Event()
        self.q: "queue.Queue[Chunk]" = queue.Queue()
        self.lock = threading.Lock()
        self.pending = 0
        self.next_chunk_id = 0
        self.results: dict[int, str] = {}
        self.failed_segments: dict[int, str] = {}
        self.total_chunks = 0
        self.done_chunks = 0
        self.all_done = threading.Event()
        self.threads: list[threading.Thread] = []
        self.running = False
        # progress / ETA
        self.total_chars = 0
        self.done_chars = 0
        self.started_at = 0.0
        self.history: collections.deque = collections.deque(maxlen=400)   # (time, done_chars)
        # last translated parts (newest last)
        self.recent: collections.deque = collections.deque(maxlen=5)
        # waiting-for-API state
        self.waiting = False
        self.wait_reason = ""
        self.waiting_since = 0.0
        self.paused_seconds = 0.0

    # ---------- public ----------
    def run(self, chunks: list[Chunk]) -> dict[int, str]:
        self.total_chunks = len(chunks)
        self.next_chunk_id = len(chunks)
        self.pending = len(chunks)
        self.total_chars = sum(c.chars for c in chunks)
        if not chunks:
            return {}
        for c in chunks:
            self.q.put(c)
        self.started_at = time.time()
        self.history.append((self.started_at, 0))
        self.running = True
        for st in self.states:
            self._start_threads(st)
        workers = sum(s.cfg.concurrency for s in self.states)
        if self.settings.mode == "refine":
            self.log("Refine mode: polishing an already translated Persian text.")
        nkeys = sum(len(s.keys) for s in self.states)
        self.log(f"Started {workers} parallel worker(s) on {len(self.states)} provider(s) "
                 f"({nkeys} key(s)) for {len(chunks)} chunk(s).")
        while not self.all_done.wait(0.5):
            if self.cancel.is_set():
                self.log("Cancelled.")
                break
            self._check_stuck()
        self.all_done.set()
        self.running = False
        for t in self.threads:
            t.join(timeout=2)
        return self.results

    def add_providers(self, cfgs: list[ProviderConfig]) -> list[str]:
        """Add providers or extra keys while a job is running (or before it starts).
        A provider with an existing name gets its new keys appended; a new name
        becomes a new provider with its own workers. Returns human-readable notes."""
        notes = []
        for cfg in cfgs:
            if not cfg.enabled:
                continue
            with self.lock:
                st = next((s for s in self.states if s.cfg.name == cfg.name), None)
            if st:
                changed = []
                added = st.add_keys(cfg.keys)
                if added:
                    changed.append(f"+{added} key(s)")
                # Model / URL fixed by the user -> revive a provider disabled for that.
                if st.disabled and (cfg.model != st.cfg.model or cfg.base_url != st.cfg.base_url):
                    st.cfg.model, st.cfg.base_url = cfg.model, cfg.base_url
                    st.disabled, st.disabled_reason = False, ""
                    for k in st.keys:
                        k.dead, k.cooldown_until, k.kind, k.reason = False, 0.0, "", ""
                    changed.append("new model/URL")
                if cfg.concurrency > st.cfg.concurrency:
                    st.cfg.concurrency = cfg.concurrency
                    changed.append(f"parallel {cfg.concurrency}")
                if changed:
                    st.consecutive_errors, st.cooldown_until = 0, 0.0
                    if self.running:
                        self._start_threads(st)
                    notes.append(f"{cfg.name}: {', '.join(changed)}")
                    self.log(f"{cfg.name}: updated while running ({', '.join(changed)}).")
            else:
                st = ProviderState(cfg)
                with self.lock:
                    self.states.append(st)
                if self.running:
                    self._start_threads(st)
                notes.append(f"{cfg.name}: added ({len(st.keys)} key(s), parallel {cfg.concurrency})")
                self.log(f"New provider '{cfg.name}' joined the job ({cfg.model}).")
        return notes

    def stats(self) -> dict:
        with self.lock:
            last = self.recent[-1] if self.recent else None
            return {
                "total_chunks": self.total_chunks,
                "done_chunks": self.done_chunks,
                "failed_segments": len(self.failed_segments),
                "total_chars": self.total_chars,
                "done_chars": self.done_chars,
                "eta_seconds": self.eta_seconds(),
                "chars_per_min": self.speed() and int(self.speed() * 60),
                "waiting": self.waiting,
                "wait_reason": self.wait_reason,
                "last": last,
                "recent": list(self.recent),
                "providers": [s.snapshot() for s in self.states],
            }

    def speed(self) -> Optional[float]:
        """Characters per second, from the recent window (falls back to the whole run)."""
        if len(self.history) < 2 or self.done_chars <= 0:
            return None
        now = time.time()
        t_last, c_last = self.history[-1]
        old = [h for h in self.history if h[0] >= now - ETA_WINDOW]
        t0, c0 = (old[0] if len(old) >= 3 else self.history[0])
        span = max(now, t_last) - t0
        if self.waiting:
            return None
        if span <= 0 or c_last - c0 <= 0:
            return None
        return (c_last - c0) / span

    def eta_seconds(self) -> Optional[int]:
        sp = self.speed()
        if not sp:
            return None
        return int(max(0, self.total_chars - self.done_chars) / sp)

    # ---------- internals ----------
    def _start_threads(self, st: ProviderState):
        while st.threads < st.cfg.concurrency:
            k = st.threads
            st.threads += 1
            t = threading.Thread(target=self._worker, args=(st,), daemon=True,
                                 name=f"{st.cfg.name}-{k}")
            t.start()
            self.threads.append(t)

    def _active_states(self):
        now = time.time()
        return [s for s in self.states if s.usable_now(now)]

    def _check_stuck(self):
        now = time.time()
        alive = [s for s in self.states if not s.disabled]
        if not alive:
            reason = "Every provider is disabled (keys rejected or wrong URL/model)."
        elif all(not s.usable_now(now) and s.only_quota_left(now) for s in alive):
            nxt = min(min(k.cooldown_until for k in s.live_keys()) for s in alive)
            reason = (f"Every API key is out of quota/credits. Next automatic retry in "
                      f"{_fmt(nxt - now)}.")
        else:
            reason = ""
        if reason and not self.waiting:
            if not self.settings.wait_for_api:
                self.log(reason + " Stopping (progress is saved; run again to continue).")
                self.cancel.set()
                return
            self.waiting, self.wait_reason, self.waiting_since = True, reason, now
            self.log(reason + " Translation is PAUSED, nothing is lost. Add a new provider or "
                     "API key to continue right away (web: 'Add to running job', CLI: edit and save "
                     "the providers file), or press Stop to finish later.")
        elif reason:
            self.wait_reason = reason
        elif self.waiting:
            self.paused_seconds += now - self.waiting_since
            self.waiting, self.wait_reason = False, ""
            self.history.append((now, self.done_chars))   # restart speed measurement
            self.log("An API is available again, continuing.")

    def _finish_chunk(self, chunk: Chunk, translations: Optional[list[str]], provider: str):
        with self.lock:
            if translations is not None:
                for sid, tr in zip(chunk.seg_ids, translations):
                    self.results[sid] = tr
                self.recent.append({
                    "chunk": chunk.id, "provider": provider, "time": time.strftime("%H:%M:%S"),
                    "source": "\n\n".join(chunk.texts)[:6000],
                    "translation": "\n\n".join(translations)[:6000],
                })
            else:
                for sid, src in zip(chunk.seg_ids, chunk.texts):
                    self.failed_segments[sid] = chunk.last_error
            self.done_chunks += 1
            self.done_chars += chunk.chars
            self.history.append((time.time(), self.done_chars))
            self.pending -= 1
            if self.pending <= 0:
                self.all_done.set()
        if translations is not None and self.on_result:
            self.on_result(dict(zip(chunk.seg_ids, translations)))

    def _split_chunk(self, chunk: Chunk) -> bool:
        """Split a chunk in two (models sometimes mangle long marker lists)."""
        if len(chunk.seg_ids) < 2:
            return False
        mid = len(chunk.seg_ids) // 2
        with self.lock:
            a = Chunk(self.next_chunk_id, chunk.seg_ids[:mid], chunk.texts[:mid])
            b = Chunk(self.next_chunk_id + 1, chunk.seg_ids[mid:], chunk.texts[mid:])
            self.next_chunk_id += 2
            self.pending += 1          # one chunk became two
            self.total_chunks += 1
        self.q.put(a)
        self.q.put(b)
        return True

    def _requeue_or_fail(self, chunk: Chunk, provider: str):
        if chunk.attempts >= self.settings.max_attempts:
            self.log(f"Chunk {chunk.id}: giving up after {chunk.attempts} attempts "
                     f"({chunk.last_error}). Original text kept for it.")
            self._finish_chunk(chunk, None, provider)
        else:
            self.q.put(chunk)

    def _eta_text(self) -> str:
        eta = self.eta_seconds()
        return f", ~{_fmt(eta)} left" if eta is not None else ""

    def _worker(self, st: ProviderState):
        s = self.settings
        while not self.all_done.is_set() and not self.cancel.is_set():
            if st.disabled:
                st.threads -= 1
                return
            now = time.time()
            wait = st.cooldown_until - now
            if wait > 0:
                time.sleep(min(wait, 1.0))
                continue
            if not any(k.usable(now) for k in st.keys):
                if not st.live_keys():
                    st.disabled = True
                    st.disabled_reason = "all keys rejected"
                    continue
                time.sleep(1.0)
                continue
            try:
                chunk = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            # Prefer giving a retried chunk to a provider that has not tried it yet.
            others = [o for o in self._active_states()
                      if o is not st and o.cfg.name not in chunk.tried]
            if st.cfg.name in chunk.tried and others and chunk.attempts < s.max_attempts - 1:
                self.q.put(chunk)
                time.sleep(0.3 + random.random() * 0.3)
                continue

            with self.lock:
                key = st.next_key(time.time())
            if key is None:            # another worker of this provider just used the last key
                self.q.put(chunk)
                continue
            key.uses += 1
            kno = st.keys.index(key) + 1
            ktxt = f" key #{kno}/{len(st.keys)}" if len(st.keys) > 1 else ""

            chunk.attempts += 1
            chunk.tried.add(st.cfg.name)
            with self.lock:
                st.busy += 1
            t0 = time.time()
            try:
                answer = chat_completion(
                    st.cfg, self.system_prompt, build_user_prompt(chunk.texts),
                    temperature=s.temperature, timeout=s.timeout, api_key=key.key)
                parts = parse_response(answer, len(chunk.texts))
                if parts is None:
                    chunk.format_failures += 1
                    chunk.attempts -= 1   # not the provider's fault; don't burn attempts
                    chunk.last_error = "segment markers were not preserved"
                    if chunk.format_failures >= 2 and self._split_chunk(chunk):
                        self.log(f"Chunk {chunk.id}: {st.cfg.name} mixed up the segment markers, "
                                 f"splitting it into smaller pieces.")
                        continue
                    if chunk.format_failures >= 4:
                        chunk.attempts = s.max_attempts
                    self._requeue_or_fail(chunk, st.cfg.name)
                    continue
                parts = [normalize_persian(p, s.persian_digits) for p in parts]
                dt = time.time() - t0
                with self.lock:
                    st.done += 1
                    st.chars += chunk.chars
                    st.total_seconds += dt
                    st.consecutive_errors = 0
                    key.kind, key.reason = "", ""
                self._finish_chunk(chunk, parts, st.cfg.name)
                self.log(f"Chunk {chunk.id} done by {st.cfg.name} in {dt:.1f}s "
                         f"({self.done_chunks}/{self.total_chunks}{self._eta_text()}).")
            except ProviderError as e:
                chunk.last_error = str(e)[:300]
                with self.lock:
                    st.failed += 1
                if e.kind == "auth":
                    key.dead, key.kind, key.reason = True, "auth", str(e)[:200]
                    chunk.attempts -= 1
                    self.q.put(chunk)
                    if st.live_keys():
                        self.log(f"{st.cfg.name}:{ktxt} rejected ({str(e)[:120]}), "
                                 f"switching to the next key.")
                    else:
                        st.disabled = True
                        st.disabled_reason = f"all keys rejected: {str(e)[:150]}"
                        self.log(f"{st.cfg.name}: disabled, API key rejected ({str(e)[:150]}).")
                    continue
                if e.kind == "quota":
                    key.quota_hits += 1
                    park = e.retry_after or min(QUOTA_COOLDOWN_MAX,
                                                QUOTA_COOLDOWN * 2 ** (key.quota_hits - 1))
                    key.cooldown_until = time.time() + park
                    key.kind, key.reason = "quota", str(e)[:200]
                    chunk.attempts -= 1   # the text is fine; the key is just empty
                    self.q.put(chunk)
                    self.log(f"{st.cfg.name}:{ktxt or ' key'} is out of quota/credits, parked for "
                             f"{_fmt(park)}. Chunk {chunk.id} goes to another key/provider.")
                    continue
                if e.kind == "fatal":
                    st.disabled = True
                    st.disabled_reason = str(e)[:200]
                    self.log(f"{st.cfg.name}: disabled ({e}). Check the URL and model name.")
                    chunk.attempts -= 1
                    self.q.put(chunk)
                    continue
                if e.kind == "rate_limit":
                    key.quota_hits = 0
                    with self.lock:
                        st.consecutive_errors += 1
                    delay = e.retry_after or min(120, 10 * (2 ** min(st.consecutive_errors - 1, 4)))
                    key.cooldown_until = time.time() + delay
                    key.kind, key.reason = "rate_limit", str(e)[:200]
                    chunk.attempts -= 1   # waiting is not a failure of the chunk
                    self.q.put(chunk)
                    more = "next key" if any(k.usable(time.time()) for k in st.keys) else "another provider"
                    self.log(f"{st.cfg.name}:{ktxt} rate limited, pausing that key {int(delay)}s. "
                             f"Chunk {chunk.id} goes to {more}.")
                    continue
                with self.lock:
                    st.consecutive_errors += 1
                if e.kind == "too_large":
                    chunk.attempts -= 1
                    if self._split_chunk(chunk):
                        self.log(f"Chunk {chunk.id} too big for {st.cfg.name}, splitting it.")
                        continue
                    chunk.attempts += 1
                delay = min(60, 3 * (2 ** min(st.consecutive_errors - 1, 4)))
                if st.consecutive_errors >= s.max_consecutive_errors:
                    delay = 120
                st.cooldown_until = time.time() + delay
                self.log(f"{st.cfg.name}: error on chunk {chunk.id} ({str(e)[:160]}), "
                         f"pausing {int(delay)}s.")
                self._requeue_or_fail(chunk, st.cfg.name)
            except Exception as e:  # never let a worker die silently
                chunk.last_error = f"{type(e).__name__}: {e}"
                self.log(f"{st.cfg.name}: unexpected error on chunk {chunk.id}: {chunk.last_error}")
                self._requeue_or_fail(chunk, st.cfg.name)
            finally:
                with self.lock:
                    st.busy -= 1
        st.threads -= 1
