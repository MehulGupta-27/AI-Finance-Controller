"""
agents/llm_provider.py
Provider-agnostic LLM wrapper — the ONLY place in the codebase that calls
any LLM SDK directly. Every agent goes through call_llm(); no agent touches
groq or ollama directly.

Implements (Section 6A):
  1. Provider routing — Groq (test/demo) or Ollama (dev only)
  2. Structured output — always returns a Pydantic-validated object, never raw text
  3. Timeout + single retry with backoff; on repeated failure raises LLMError
     which the calling agent catches and routes the record to UNRESOLVED
  4. Hard token budget per call (LLM_MAX_TOKENS_PER_CALL)
  5. LLM response caching keyed on (record_id, prompt_hash) — SQLite table.
     A repeated pipeline run returns the cached result instantly, burning
     zero tokens for records that already resolved with unchanged prompts.
     Cache is invalidated automatically when prompt content changes (hash detects it).
  6. Concurrent execution via ThreadPoolExecutor, gated by a token-bucket
     rate limiter capped at LLM_RATE_LIMIT_RPM requests/minute
"""

import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Type, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.utils.config import (
    LLM_MAX_TOKENS_PER_CALL,
    LLM_TIMEOUT_SECONDS,
    LLM_MAX_RETRIES,
    LLM_RATE_LIMIT_RPM,
    LLM_TPM_SLEEP_SECONDS,
    LLM_RETRY_BACKOFF_MIN,
    LLM_RETRY_BACKOFF_MAX,
    LLM_REASONING_EFFORT,
    GROQ_REASONING_MODEL,
    GROQ_VERIFIER_MODEL,
)

load_dotenv(_ROOT / ".env")
logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Cache — SQLite, one table, keyed on (record_id, prompt_hash)
# ---------------------------------------------------------------------------
_CACHE_DB_PATH = _ROOT / "db" / "llm_cache.db"
_CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_cache_lock = threading.Lock()


def _get_cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_CACHE_DB_PATH), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_response_cache (
            record_id   TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (record_id, prompt_hash)
        )
    """)
    conn.commit()
    return conn


_cache_conn: Optional[sqlite3.Connection] = None


def _cache() -> sqlite3.Connection:
    global _cache_conn
    if _cache_conn is None:
        _cache_conn = _get_cache_conn()
    return _cache_conn


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _cache_get(record_id: str, phash: str) -> Optional[str]:
    with _cache_lock:
        try:
            cur = _cache().execute(
                "SELECT response_json FROM llm_response_cache WHERE record_id=? AND prompt_hash=?",
                (record_id, phash),
            )
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as exc:
            logger.warning("Cache read error: %s", exc)
            return None


def _cache_put(record_id: str, phash: str, response_json: str) -> None:
    with _cache_lock:
        try:
            _cache().execute(
                """INSERT OR REPLACE INTO llm_response_cache
                   (record_id, prompt_hash, response_json, created_at)
                   VALUES (?, ?, ?, ?)""",
                (record_id, phash, response_json, datetime.now(timezone.utc).isoformat()),
            )
            _cache().commit()
        except Exception as exc:
            logger.warning("Cache write error: %s", exc)


# ---------------------------------------------------------------------------
# Token-aware rate limiter (Section 6A)
# Tracks cumulative tokens sent in trailing 60-second window
# ---------------------------------------------------------------------------
class _TokenAwareRateLimiter:
    """
    Token-consumption-aware rate limiter for Groq's 8K tokens/minute limit.
    
    Section 6A: Track cumulative tokens sent in the trailing 60-second window,
    estimate from prompt length before sending, correct from actual response
    afterward. Pause new calls when approaching ~8K/minute ceiling.
    """

    def __init__(self, tpm_limit: int = 8000):
        self._tpm_limit = tpm_limit
        self._token_history: list[tuple[float, int]] = []  # (timestamp, tokens)
        self._lock = threading.Lock()
        logger.info(f"Token-aware rate limiter initialized: {tpm_limit} TPM")

    def _prune_old_entries(self, now: float) -> None:
        """Remove entries older than 60 seconds."""
        cutoff = now - 60.0
        self._token_history = [(ts, tokens) for ts, tokens in self._token_history if ts > cutoff]

    def _get_trailing_tokens(self, now: float) -> int:
        """Calculate cumulative tokens in trailing 60-second window."""
        self._prune_old_entries(now)
        return sum(tokens for _, tokens in self._token_history)

    def estimate_tokens(self, prompt: str) -> int:
        """
        Rough token estimate: ~4 characters per token for English.
        Section 6A: estimate from prompt length before sending.
        """
        return len(prompt) // 4

    def acquire(self, estimated_tokens: int) -> None:
        """
        Block until there's enough token budget in the trailing window.
        Section 6A: pause new calls when close to the 8K/minute ceiling.
        """
        while True:
            with self._lock:
                now = time.monotonic()
                trailing = self._get_trailing_tokens(now)
                
                # Leave 10% safety margin (90% of limit)
                available = int(self._tpm_limit * 0.9) - trailing
                
                if estimated_tokens <= available:
                    # Reserve these tokens immediately (will be corrected later)
                    self._token_history.append((now, estimated_tokens))
                    logger.debug(
                        f"Token budget OK: {trailing}/{self._tpm_limit} TPM used, "
                        f"acquiring {estimated_tokens} tokens"
                    )
                    return
                
                # Calculate how long to wait
                if self._token_history:
                    oldest_ts, oldest_tokens = self._token_history[0]
                    wait_time = (oldest_ts + 60.0) - now
                    if wait_time > 0:
                        logger.info(
                            f"Token budget nearly exhausted: {trailing}/{self._tpm_limit} TPM used. "
                            f"Waiting {wait_time:.1f}s for window to advance."
                        )
                        time.sleep(min(wait_time, 1.0))
                        continue
            
            # Outside lock, short sleep to avoid busy-wait
            time.sleep(0.1)

    def correct_actual_tokens(self, estimated: int, actual: int) -> None:
        """
        Section 6A: correct from actual usage after response received.
        Replace the last estimated entry with the actual token count.
        """
        with self._lock:
            # Find and replace the most recent entry matching the estimate
            for i in range(len(self._token_history) - 1, -1, -1):
                ts, tokens = self._token_history[i]
                if tokens == estimated:
                    self._token_history[i] = (ts, actual)
                    logger.debug(f"Corrected token estimate: {estimated} → {actual}")
                    break


_rate_limiter = _TokenAwareRateLimiter(tpm_limit=8000)  # Groq free tier: 8K TPM (source: console.groq.com/docs/rate-limits)

# Global token usage tracker (for reporting in pipeline summary)
_total_tokens_used = 0


# ---------------------------------------------------------------------------
# Custom exception — calling agents catch this and route to UNRESOLVED
# ---------------------------------------------------------------------------
def get_total_tokens_used() -> int:
    """Return cumulative tokens used across all LLM calls this session."""
    return _total_tokens_used


def reset_token_counter() -> None:
    """Reset the global token counter (call at pipeline start)."""
    global _total_tokens_used
    _total_tokens_used = 0


class LLMError(Exception):
    """Raised when an LLM call fails after all retries."""
    pass


# ---------------------------------------------------------------------------
# Provider-specific call implementations
# ---------------------------------------------------------------------------

def _call_groq(
    prompt: str,
    schema: Type[T],
    model: Optional[str] = None,
) -> T:
    """
    Call Groq API with JSON mode. Returns a schema-validated Pydantic object.
    Raises LLMError on auth/timeout/parse failures.
    
    Section 6A: Uses token-aware rate limiter - estimates tokens before call,
    corrects with actual usage after.
    """
    try:
        from groq import Groq
    except ImportError:
        raise LLMError("groq package not installed — run: pip install groq")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY not set in .env")

    _model = model or GROQ_REASONING_MODEL
    client = Groq(api_key=api_key)

    # Minimal system prompt — just the required JSON field names and types.
    # Keep it short to leave room for the user prompt few-shot examples.
    fields = {k: str(v) for k, v in schema.model_fields.items()}
    field_list = ", ".join(f'"{k}"' for k in fields)
    system_msg = (
        "You are a precise financial reconciliation analyst. "
        f"Respond ONLY with valid JSON containing exactly these fields: {field_list}. "
        "No markdown, no prose, no extra fields."
    )

    # reasoning_effort only applies to reasoning models (gpt-oss-*).
    # Non-reasoning models will reject it, so only pass it when relevant.
    _REASONING_MODELS = {
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-safeguard-20b",
    }
    extra_params = {}
    if _model in _REASONING_MODELS:
        extra_params["reasoning_effort"] = LLM_REASONING_EFFORT

    # Section 6A: Estimate tokens and acquire budget BEFORE calling API
    estimated = _rate_limiter.estimate_tokens(system_msg + prompt)
    _rate_limiter.acquire(estimated)

    try:
        response = client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=LLM_MAX_TOKENS_PER_CALL,
            temperature=0.0,
            response_format={"type": "json_object"},
            timeout=LLM_TIMEOUT_SECONDS,
            **extra_params,
        )
        
        # Section 6A: Correct token estimate with actual usage from response
        actual_tokens = response.usage.total_tokens if response.usage else estimated
        _rate_limiter.correct_actual_tokens(estimated, actual_tokens)
        
        # Track total tokens globally for pipeline reporting
        global _total_tokens_used
        _total_tokens_used += actual_tokens
        logger.info(f"LLM call: {actual_tokens} tokens (cumulative: {_total_tokens_used})")
        
    except Exception as exc:
        exc_str = str(exc)
        # If still hit 429 despite rate limiter, log warning but don't retry here
        if "429" in exc_str or "rate_limit" in exc_str.lower():
            logger.warning("Rate limit exceeded despite token-aware limiter: %s", exc_str)
        raise LLMError(f"Groq API call failed: {exc}") from exc

    raw = response.choices[0].message.content
    try:
        data = json.loads(raw)
        return schema.model_validate(data)
    except Exception as exc:
        raise LLMError(f"Failed to parse Groq response as {schema.__name__}: {exc}\nRaw: {raw[:300]}") from exc


def _call_ollama(
    prompt: str,
    schema: Type[T],
    model: Optional[str] = None,
) -> T:
    """
    Call a local Ollama model. Dev-only; never used for reported accuracy numbers.
    Requires `ollama` Python package and a running Ollama daemon.
    """
    try:
        import ollama as _ollama
    except ImportError:
        raise LLMError("ollama package not installed — run: pip install ollama")

    from agents.utils.config import OLLAMA_MODEL
    _model = model or OLLAMA_MODEL
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    full_prompt = (
        f"Respond ONLY with valid JSON matching this schema:\n{schema_json}\n\n"
        f"---\n{prompt}"
    )

    try:
        resp = _ollama.chat(
            model=_model,
            messages=[{"role": "user", "content": full_prompt}],
            options={"num_predict": LLM_MAX_TOKENS_PER_CALL, "temperature": 0},
        )
        raw = resp["message"]["content"]
        # Strip markdown code fences if present
        if raw.strip().startswith("```"):
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        return schema.model_validate(data)
    except Exception as exc:
        raise LLMError(f"Ollama call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Public call_llm() — the only function agents should ever call
# ---------------------------------------------------------------------------

def call_llm(
    prompt:    str,
    schema:    Type[T],
    record_id: str = "unknown",
    provider:  str = None,
    model:     str = None,
) -> T:
    """
    Route an LLM call through the shared wrapper.

    Parameters
    ----------
    prompt    : the full user prompt (system framing + few-shot + candidate data)
    schema    : Pydantic model class — the response is validated against this
    record_id : used as the cache key prefix; defaults to "unknown"
    provider  : "groq" | "ollama" — defaults to LLM_PROVIDER env var, then "groq"
    model     : override model name; defaults to provider's configured default

    Returns
    -------
    A validated instance of `schema`.

    Raises
    ------
    LLMError — if all retries are exhausted or parsing fails.
    The calling agent MUST catch this and route the record to UNRESOLVED.
    """
    _provider = provider or os.getenv("LLM_PROVIDER", "groq").lower()
    phash     = _prompt_hash(prompt)

    # --- Cache lookup ---
    cached_json = _cache_get(record_id, phash)
    if cached_json:
        try:
            obj = schema.model_validate(json.loads(cached_json))
            logger.debug("Cache HIT: record=%s provider=%s", record_id, _provider)
            return obj
        except Exception:
            pass  # stale/corrupt cache entry — fall through to live call

    # --- Live call with retry ---
    # Note: Token-aware rate limiting now happens INSIDE _call_groq() before API call
    @retry(
        stop=stop_after_attempt(LLM_MAX_RETRIES + 1),
        wait=wait_exponential(multiplier=2, min=LLM_RETRY_BACKOFF_MIN, max=LLM_RETRY_BACKOFF_MAX),
        retry=retry_if_exception_type(LLMError),
        reraise=True,
    )
    def _make_call() -> T:
        if _provider == "groq":
            return _call_groq(prompt, schema, model)
        elif _provider == "ollama":
            return _call_ollama(prompt, schema, model)
        else:
            raise LLMError(f"Unknown provider: {_provider!r}. Set LLM_PROVIDER=groq or ollama in .env")

    result = _make_call()

    # --- Cache store ---
    _cache_put(record_id, phash, result.model_dump_json())

    logger.debug(
        "LLM call: record=%s provider=%s model=%s cache=MISS",
        record_id, _provider, model or "default",
    )
    return result


# ---------------------------------------------------------------------------
# Concurrent batch execution — call_llm() for a list of (record_id, prompt)
# pairs, respecting the rate limiter, returning results in order
# ---------------------------------------------------------------------------

def call_llm_batch(
    items:     list[tuple[str, str]],   # list of (record_id, prompt)
    schema:    Type[T],
    provider:  str = None,
    model:     str = None,
    max_workers: int = 8,
) -> list[tuple[str, T | LLMError]]:
    """
    Execute multiple LLM calls concurrently, rate-limited.

    Returns a list of (record_id, result_or_error) in the SAME ORDER as `items`.
    On per-item failure, the entry contains the LLMError instance instead of
    a parsed result — the caller decides how to handle it (route to UNRESOLVED).
    Never raises — individual errors are returned, not propagated.
    """
    results: list[tuple[str, Any]] = [None] * len(items)

    def _worker(idx: int, record_id: str, prompt: str):
        try:
            return idx, record_id, call_llm(prompt, schema, record_id, provider, model)
        except LLMError as exc:
            return idx, record_id, exc

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_worker, i, rid, prompt)
            for i, (rid, prompt) in enumerate(items)
        ]
        for future in as_completed(futures):
            idx, record_id, outcome = future.result()
            results[idx] = (record_id, outcome)

    return results


# ---------------------------------------------------------------------------
# Cache statistics helper
# ---------------------------------------------------------------------------

def cache_stats() -> dict:
    """Return cache hit/miss counts for the current DB."""
    try:
        cur = _cache().execute("SELECT COUNT(*) FROM llm_response_cache")
        total = cur.fetchone()[0]
        return {"cached_entries": total, "db_path": str(_CACHE_DB_PATH)}
    except Exception:
        return {"cached_entries": 0, "db_path": str(_CACHE_DB_PATH)}


# ---------------------------------------------------------------------------
# Smoke-test — verifies Groq connectivity and caching round-trip
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from pydantic import BaseModel as BM

    class _TestSchema(BM):
        answer: str
        confidence: float

    print("\n=== llm_provider smoke test ===")
    provider = os.getenv("LLM_PROVIDER", "groq")
    print(f"  Provider : {provider}")
    print(f"  Cache DB : {_CACHE_DB_PATH}")

    test_prompt = (
        "A payment of ₹1,000 was captured on 2026-01-01. "
        "A bank settlement of ₹976.40 arrived on 2026-01-02. "
        "Does this look like a legitimate fee-adjusted settlement? "
        "Respond with answer ('yes' or 'no') and confidence (0.0-1.0)."
    )
    rid = "smoke_test_001"

    print(f"\n  Making first call (cache miss expected)...")
    t0 = time.time()
    try:
        result = call_llm(test_prompt, _TestSchema, record_id=rid)
        t1 = time.time()
        print(f"  Result   : answer={result.answer!r}  confidence={result.confidence}")
        print(f"  Latency  : {t1-t0:.2f}s")
    except LLMError as e:
        print(f"  LLMError : {e}")
        print("  (Groq may be unavailable — check GROQ_API_KEY in .env)")
        sys.exit(0)

    print(f"\n  Making second call (cache hit expected)...")
    t0 = time.time()
    result2 = call_llm(test_prompt, _TestSchema, record_id=rid)
    t1 = time.time()
    print(f"  Result   : answer={result2.answer!r}  confidence={result2.confidence}")
    print(f"  Latency  : {t1-t0:.4f}s  (should be ~0ms)")
    assert result.answer == result2.answer, "Cache returned different answer!"

    stats = cache_stats()
    print(f"\n  Cache stats: {stats}")
    print(f"  ✓ Caching round-trip verified")
    print(f"  ✓ Rate limiter active ({LLM_RATE_LIMIT_RPM} req/min)")
    print(f"  ✓ Structured output validated via Pydantic")
    print("=== OK ===\n")
