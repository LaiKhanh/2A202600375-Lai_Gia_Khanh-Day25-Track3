from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Shared utilities — use these in both ResponseCache and SharedRedisCache
# ---------------------------------------------------------------------------

PRIVACY_PATTERNS = re.compile(
    r"\b(balance|password|credit.card|ssn|social.security|user.\d+|account.\d+)\b",
    re.IGNORECASE,
)


def _is_uncacheable(query: str) -> bool:
    """Return True if query contains privacy-sensitive keywords."""
    return bool(PRIVACY_PATTERNS.search(query))


def _looks_like_false_hit(query: str, cached_key: str) -> bool:
    """Return True if query and cached key contain different 4-digit numbers (years, IDs)."""
    nums_q = set(re.findall(r"\b\d{4}\b", query))
    nums_c = set(re.findall(r"\b\d{4}\b", cached_key))
    return bool(nums_q and nums_c and nums_q != nums_c)


# ---------------------------------------------------------------------------
# In-memory cache (existing)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheEntry:
    key: str
    value: str
    created_at: float
    metadata: dict[str, str]


class ResponseCache:
    """Simple in-memory cache skeleton.

    TODO(student): Add a better semantic similarity function and false-hit guardrails.
    Use the module-level _is_uncacheable() and _looks_like_false_hit() helpers in your
    get() and set() methods.  For production, replace with SharedRedisCache.
    """

    def __init__(self, ttl_seconds: int, similarity_threshold: float):
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self._entries: list[CacheEntry] = []

    def get(self, query: str) -> tuple[str | None, float]:
        if _is_uncacheable(query):
            return None, 0.0
        
        best_value: str | None = None
        best_score = 0.0
        best_cached_query = ""
        now = time.time()
        self._entries = [e for e in self._entries if now - e.created_at <= self.ttl_seconds]
        for entry in self._entries:
            score = self.similarity(query, entry.key)
            if score > best_score:
                best_score = score
                best_value = entry.value
                best_cached_query = entry.key
        if best_score >= self.similarity_threshold:
            if _looks_like_false_hit(query, best_cached_query):
                return None, best_score
            return best_value, best_score
        return None, best_score

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        if _is_uncacheable(query):
            return
        self._entries.append(CacheEntry(query, value, time.time(), metadata or {}))

    @staticmethod
    def similarity(a: str, b: str) -> float:
        """Improved semantic similarity using token overlap with character n-grams.

        Combines exact token overlap (Jaccard) with 3-character n-gram overlap
        for better detection of paraphrases and subtle differences.
        """
        # Token-level Jaccard similarity
        left_tokens = set(a.lower().split())
        right_tokens = set(b.lower().split())
        if not left_tokens or not right_tokens:
            return 0.0
        jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        
        # 3-character n-gram similarity for nuanced distinction
        left_normalized = a.lower().replace(" ", "")
        right_normalized = b.lower().replace(" ", "")
        
        left_ngrams = set(left_normalized[i:i+3] for i in range(len(left_normalized) - 2))
        right_ngrams = set(right_normalized[i:i+3] for i in range(len(right_normalized) - 2))
        
        if not left_ngrams or not right_ngrams:
            return jaccard
        
        ngram_similarity = len(left_ngrams & right_ngrams) / len(left_ngrams | right_ngrams)
        
        # Weighted average: token overlap is primary, n-gram is secondary
        return 0.7 * jaccard + 0.3 * ngram_similarity


# ---------------------------------------------------------------------------
# Redis shared cache (new)
# ---------------------------------------------------------------------------


class SharedRedisCache:
    """Redis-backed shared cache for multi-instance deployments.

    TODO(student): Implement the get() and set() methods using Redis commands
    so that cache state is shared across multiple gateway instances.

    Data model (suggested):
        Key    = "{prefix}{query_hash}"   (Redis String namespace)
        Value  = Redis Hash with fields:  "query", "response"
        TTL    = Redis EXPIRE (automatic cleanup — no manual eviction)

    For similarity lookup: SCAN all keys with self.prefix, HGET each entry's
    "query" field, compute similarity locally via ResponseCache.similarity().

    Provided helpers:
        _is_uncacheable(query)          — True if privacy-sensitive
        _looks_like_false_hit(q, key)   — True if 4-digit numbers differ
        self._query_hash(query)         — deterministic short hash for Redis key
        ResponseCache.similarity(a, b)  — reuse your improved similarity function
    """

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        similarity_threshold: float,
        prefix: str = "rl:cache:",
    ):
        import redis as redis_lib

        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self.prefix = prefix
        self.false_hit_log: list[dict[str, object]] = []
        self._redis: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response from Redis.
        
        Implements two-step lookup: exact match first, then similarity scan.
        Applies privacy guardrails and false-hit detection.
        """
        if _is_uncacheable(query):
            return None, 0.0
        
        # Step 1: Try exact-match lookup
        exact_key = f"{self.prefix}{self._query_hash(query)}"
        try:
            exact_response = self._redis.hget(exact_key, "response")
            if exact_response is not None:
                return exact_response, 1.0
        except Exception:
            return None, 0.0
        
        # Step 2: Similarity scan across all cached keys
        best_value: str | None = None
        best_score = 0.0
        best_cached_query = ""
        
        try:
            for key in self._redis.scan_iter(f"{self.prefix}*"):
                try:
                    cached_query = self._redis.hget(key, "query")
                    if cached_query is None:
                        continue
                    
                    score = ResponseCache.similarity(query, cached_query)
                    if score > best_score:
                        best_score = score
                        best_value = self._redis.hget(key, "response")
                        best_cached_query = cached_query
                except Exception:
                    continue
        except Exception:
            return None, 0.0
        
        if best_score >= self.similarity_threshold:
            # Check for false hits (e.g., different years/IDs)
            if _looks_like_false_hit(query, best_cached_query):
                self.false_hit_log.append({
                    "query": query,
                    "cached_query": best_cached_query,
                    "score": best_score,
                    "timestamp": time.time()
                })
                return None, best_score
            return best_value, best_score
        
        return None, best_score

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in Redis with TTL.
        
        Skips uncacheable queries and sets automatic expiration.
        """
        if _is_uncacheable(query):
            return
        
        key = f"{self.prefix}{self._query_hash(query)}"
        try:
            self._redis.hset(key, mapping={"query": query, "response": value})
            self._redis.expire(key, self.ttl_seconds)
        except Exception:
            pass

    def flush(self) -> None:
        """Remove all entries with this cache prefix (for testing)."""
        for key in self._redis.scan_iter(f"{self.prefix}*"):
            self._redis.delete(key)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()

    @staticmethod
    def _query_hash(query: str) -> str:
        """Deterministic short hash for a query string."""
        return hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
