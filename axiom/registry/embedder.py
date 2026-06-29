"""
AXIOM Embedder — generates and stores OpenAI text embeddings for skill descriptions.

Uses text-embedding-3-small (1536 dims) by default. Vectors are stored in Supabase
pgvector column for sub-millisecond cosine similarity search.
"""

from __future__ import annotations

import hashlib
from typing import Optional

import numpy as np
from loguru import logger
from openai import AsyncOpenAI

from axiom.config import settings


class SkillEmbedder:
    """
    Wraps the OpenAI Embeddings API.

    Thread-safe, async-first. Uses a simple in-process LRU-style cache keyed
    on the SHA-256 of the input text to avoid re-embedding identical descriptions.
    """

    def __init__(self) -> None:
        self._client: Optional[AsyncOpenAI] = None
        self._cache: dict[str, list[float]] = {}

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client

    @staticmethod
    def _cache_key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    async def embed(self, text: str) -> list[float]:
        """
        Embed a single text string. Returns a 1536-dim float list.
        Results are cached in-process to avoid duplicate API calls.
        """
        key = self._cache_key(text)
        if key in self._cache:
            logger.debug(f"Embedding cache hit for key {key[:12]}…")
            return self._cache[key]

        client = self._get_client()
        logger.debug(f"Embedding text ({len(text)} chars) with {settings.embedding_model}")
        response = await client.embeddings.create(
            input=text,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
        vector = response.data[0].embedding
        self._cache[key] = vector
        return vector

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a single API call (more efficient)."""
        if not texts:
            return []

        # Check cache first
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            key = self._cache_key(text)
            if key in self._cache:
                results[i] = self._cache[key]
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            client = self._get_client()
            logger.debug(f"Batch embedding {len(uncached_texts)} texts")
            response = await client.embeddings.create(
                input=uncached_texts,
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
            )
            for batch_idx, api_result in enumerate(response.data):
                vector = api_result.embedding
                original_idx = uncached_indices[batch_idx]
                results[original_idx] = vector
                key = self._cache_key(uncached_texts[batch_idx])
                self._cache[key] = vector

        return [r for r in results if r is not None]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two embedding vectors."""
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))

    @staticmethod
    def build_skill_text(name: str, description: str, tags: list[str]) -> str:
        """
        Construct the canonical text used when embedding a skill.
        Consistent across registration and search to ensure comparable vectors.
        """
        tag_str = ", ".join(tags) if tags else "general"
        return f"Skill: {name}\nDescription: {description}\nTags: {tag_str}"

    def clear_cache(self) -> None:
        self._cache.clear()
        logger.debug("Embedding cache cleared")


# Module-level singleton
embedder = SkillEmbedder()
