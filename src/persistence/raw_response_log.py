"""
On-disk record of raw LLM responses, one JSONL file per translation job.

Each line holds one LLM call exactly as the provider returned it, before tag
extraction or any post-processing, so a suspicious word in the output can be
traced back to the model (it is in the raw response) or to the pipeline (it
is not). Enabled with SAVE_RAW_LLM_RESPONSES=true.
"""

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

RAW_RESPONSES_DIRNAME = "raw_responses"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def raw_response_log_path(data_dir: Union[str, Path], translation_id: str) -> Path:
    """Path of the JSONL file holding a job's raw responses."""
    safe_id = _UNSAFE_FILENAME_CHARS.sub("_", str(translation_id)) or "job"
    return Path(data_dir) / RAW_RESPONSES_DIRNAME / f"{safe_id}.jsonl"


class RawResponseLog:
    """Append-only JSONL writer for one translation job."""

    def __init__(self, data_dir: Union[str, Path], translation_id: str):
        self.path = raw_response_log_path(data_dir, translation_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        unit_index: int,
        attempt: int,
        response: Any,
        model: Optional[str] = None,
    ) -> None:
        """Append one LLM call.

        Args:
            unit_index: Zero-based index of the translated unit (chunk)
            attempt: One-based validation attempt for that unit
            response: The provider's LLMResponse
            model: Model identifier, for the record
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "unit_index": unit_index,
            "attempt": attempt,
            "model": model,
            "prompt_tokens": getattr(response, "prompt_tokens", None),
            "completion_tokens": getattr(response, "completion_tokens", None),
            "finish_reason": getattr(response, "finish_reason", None),
            "output_truncated": getattr(response, "output_truncated", False),
            "raw_response": getattr(response, "content", None),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
