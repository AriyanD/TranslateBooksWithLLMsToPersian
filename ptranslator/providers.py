"""User-defined OpenAI-compatible providers. No built-in presets.

Every provider is just: name + base URL + API key + model (+ concurrency).
Anything that speaks the OpenAI ``/chat/completions`` format works
(Groq, OpenRouter, Gemini's OpenAI endpoint, Together, Cerebras, Mistral,
DeepSeek, NVIDIA NIM, GitHub Models, a self-hosted vLLM, etc.).
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Optional


class ProviderError(Exception):
    """Raised when a provider call fails.

    kind:
      - "rate_limit": 429, back off then retry (same provider later is fine)
      - "auth":       401/403, the provider is unusable, disable it
      - "transient":  5xx, timeouts, network errors, empty answers
      - "quota":      key is out of credits / daily quota (402, "insufficient_quota",
                      "exceeded your current quota", "insufficient balance"...).
                      That key is parked for a long time; other keys/providers take over.
      - "fatal":      4xx that will not fix itself (bad model name, etc.)
    """

    def __init__(self, message: str, kind: str = "transient",
                 status: Optional[int] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


ROLES = ("translate", "review")
_ROLE_ALIASES = {"translate": "translate", "translation": "translate", "t": "translate",
                 "review": "review", "refine": "review", "polish": "review", "r": "review",
                 "both": "both", "all": "both"}


def parse_roles(value) -> list[str]:
    """"translate", "review", "both", ["translate", "review"] -> normalized list.
    Missing / empty means both (old providers files keep working)."""
    if value is None or value == "" or value == []:
        return list(ROLES)
    items = re.split(r"[,;\s/+]+", value) if isinstance(value, str) else list(value)
    out = []
    for it in items:
        r = _ROLE_ALIASES.get(str(it).strip().lower())
        if r == "both":
            return list(ROLES)
        if r and r not in out:
            out.append(r)
    return [r for r in ROLES if r in out] or list(ROLES)


def mode_role(mode: str) -> str:
    """Job mode -> provider role. The 'refine' job is the review job."""
    return "review" if mode in ("refine", "review") else "translate"


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str = ""
    model: str = ""
    concurrency: int = 1          # parallel requests allowed on this provider
    enabled: bool = True
    extra_headers: dict = field(default_factory=dict)
    use_for: list = field(default_factory=lambda: list(ROLES))   # "translate", "review" or both

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderConfig":
        cfg = cls(
            name=str(d.get("name") or "").strip() or "provider",
            base_url=str(d.get("base_url") or d.get("url") or "").strip(),
            api_key=str(d.get("api_key") or d.get("key") or "").strip(),
            model=str(d.get("model") or "").strip(),
            concurrency=max(1, int(d.get("concurrency") or 1)),
            enabled=bool(d.get("enabled", True)),
            extra_headers=dict(d.get("extra_headers") or {}),
            use_for=parse_roles(d.get("use_for", d.get("roles"))),
        )
        if not cfg.base_url:
            raise ValueError(f"Provider '{cfg.name}' has no URL")
        if not cfg.model:
            raise ValueError(f"Provider '{cfg.name}' has no model")
        return cfg

    def used_for(self, mode: str) -> bool:
        """Should this provider work on a job of this mode ("translate" / "refine")?"""
        return mode_role(mode) in self.use_for

    @property
    def keys(self) -> list[str]:
        """The api_key field may hold several keys: "key1, key2" or one per line."""
        return split_keys(self.api_key)

    def to_dict(self, hide_key: bool = False) -> dict:
        d = asdict(self)
        if hide_key and d["api_key"]:
            d["api_key"] = d["api_key"][:4] + "..." + d["api_key"][-4:]
        return d


def split_keys(raw: str) -> list[str]:
    out = []
    for k in re.split(r"[,;\s]+", raw or ""):
        k = k.strip()
        if k and k not in out:
            out.append(k)
    return out


def chat_url(base_url: str) -> str:
    """Accept either a base URL (…/v1) or a full …/chat/completions URL."""
    u = base_url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    return u + "/chat/completions"


def models_url(base_url: str) -> str:
    u = base_url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")]
    return u + "/models"


_THINK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)


def clean_model_output(text: str) -> str:
    """Remove reasoning blocks and stray code fences some models add."""
    text = _THINK_RE.sub("", text or "")
    # Unclosed <think> at the start: drop everything up to </think>
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    t = text.strip()
    if t.startswith("```") and t.endswith("```"):
        t = t[3:-3]
        t = t.split("\n", 1)[1] if "\n" in t else t
    return t.strip("\n")


def _headers(cfg: ProviderConfig, api_key: Optional[str] = None) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "User-Agent": "PersianBookTranslator/1.0"}
    key = api_key if api_key is not None else (cfg.keys[0] if cfg.keys else "")
    if key:
        h["Authorization"] = f"Bearer {key}"
    h.update(cfg.extra_headers or {})
    return h


# "Out of money / out of daily quota" wording used by OpenAI, Gemini, OpenRouter,
# DeepSeek, Groq, Mistral, Together, etc.
_QUOTA_RE = re.compile(
    r"insufficient[_ ]?quota|exceeded your current quota|insufficient[_ ]?(balance|credits?|funds)|"
    r"out of credits|not enough credits|credit balance|billing|payment required|"
    r"per[_ ]?day|daily|tokens per day|requests per day|RPD|TPD|"
    r"quota exceeded|exceeded.*quota|quota.*exhausted|RESOURCE_EXHAUSTED", re.I)
# ...but a per-minute quota is just a normal rate limit.
_MINUTE_RE = re.compile(r"per[_ ]?minute|perminute|RPM|TPM|per second|try again in \d+(\.\d+)?s", re.I)


def _is_quota(text: str) -> bool:
    return bool(_QUOTA_RE.search(text or "")) and not _MINUTE_RE.search(text or "")


def _classify_http_error(e: urllib.error.HTTPError) -> ProviderError:
    try:
        body = e.read().decode("utf-8", "replace")[:500]
    except Exception:
        body = ""
    status = e.code
    retry_after = None
    ra = e.headers.get("Retry-After") if e.headers else None
    if ra:
        try:
            retry_after = float(ra)
        except ValueError:
            retry_after = None
    msg = f"HTTP {status}: {body.strip() or e.reason}"
    if status == 402:
        return ProviderError(msg, "quota", status, retry_after)
    if status == 429:
        if _is_quota(body):
            return ProviderError(msg, "quota", status, retry_after)
        return ProviderError(msg, "rate_limit", status, retry_after)
    if status in (401, 403):
        if _is_quota(body):           # some APIs answer 403 when credits are gone
            return ProviderError(msg, "quota", status, retry_after)
        return ProviderError(msg, "auth", status)
    if status in (408, 409, 425) or status >= 500:
        return ProviderError(msg, "transient", status, retry_after)
    if status == 413:
        return ProviderError(msg, "too_large", status)
    if status == 400 and re.search(r"context|too long|too many tokens|maximum", body, re.I):
        return ProviderError(msg, "too_large", status)
    if _is_quota(body):
        return ProviderError(msg, "quota", status, retry_after)
    return ProviderError(msg, "fatal", status)


def chat_completion(cfg: ProviderConfig, system: str, user: str,
                    temperature: float = 0.3, timeout: float = 180,
                    max_tokens: Optional[int] = None, api_key: Optional[str] = None) -> str:
    """One blocking chat completion call. Returns the cleaned text.
    ``api_key`` picks one specific key when the provider has several."""
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    req = urllib.request.Request(
        chat_url(cfg.base_url),
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(cfg, api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise _classify_http_error(e) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise ProviderError(f"Network error: {e}", "transient") from None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ProviderError(f"Non-JSON response: {raw[:200]}", "transient") from None
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        if _is_quota(str(msg)):
            kind = "quota"
        elif re.search(r"rate|quota|limit|too many", str(msg), re.I):
            kind = "rate_limit"
        elif re.search(r"api key|unauthori[sz]ed|invalid.*key|authenticat", str(msg), re.I):
            kind = "auth"
        else:
            kind = "transient"
        raise ProviderError(f"API error: {msg}", kind)
    try:
        choice = data["choices"][0]
        content = choice.get("message", {}).get("content")
        if content is None:
            content = choice.get("text", "")
        if isinstance(content, list):  # some APIs return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"Unexpected response shape: {raw[:200]}", "transient") from None
    finish = (choice.get("finish_reason") or "").lower()
    text = clean_model_output(content or "")
    if not text.strip():
        raise ProviderError("Empty answer from model", "transient")
    if finish == "length":
        raise ProviderError("Answer was cut off (output limit reached)", "too_large")
    return text


def list_models(cfg: ProviderConfig, timeout: float = 20) -> list[str]:
    req = urllib.request.Request(models_url(cfg.base_url), headers=_headers(cfg))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise _classify_http_error(e) from None
    except Exception as e:
        raise ProviderError(f"Could not list models: {e}") from None
    items = data.get("data", data) if isinstance(data, dict) else data
    out = []
    for m in items or []:
        if isinstance(m, dict) and m.get("id"):
            out.append(str(m["id"]))
        elif isinstance(m, str):
            out.append(m)
    return sorted(set(out))


def load_providers_file(path: str) -> list[ProviderConfig]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("providers", [])
    return [ProviderConfig.from_dict(d) for d in data if d.get("enabled", True)]
