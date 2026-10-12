"""
Free LLMs for outreach (drafting, reading replies, identifying contacts):
Groq keys rotated across models, in Mark's order — free providers only.
Keys are read at run time from bazaraki-listings/_tools/groq_keys.txt.
"""

from __future__ import annotations

import itertools
import json
import time
from pathlib import Path

import requests

KEYS_FILE = Path.home() / "Projects/bazaraki-listings/_tools/groq_keys.txt"
MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"
_pairs = None


def _next():
    global _pairs
    if _pairs is None:
        keys = [k.strip() for k in KEYS_FILE.read_text().splitlines() if k.strip()]
        _pairs = itertools.cycle([(k, m) for m in MODELS for k in keys])
    return next(_pairs)


def chat_json(prompt: str, *, temperature: float = 0.0, max_tries: int = 18) -> dict | None:
    """One JSON object back, or None when every key/model refused."""
    for _ in range(max_tries):
        key, model = _next()
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers={"Authorization": f"Bearer {key}", "User-Agent": UA},
                              json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                    "temperature": temperature, "response_format": {"type": "json_object"}}, timeout=90)
        except requests.RequestException:
            time.sleep(3)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2)
            continue
        if r.status_code != 200:
            continue
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except (KeyError, ValueError):
            continue
    return None
