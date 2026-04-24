"""Embedding engine — deterministic TF-IDF by default; sentence-transformers opt-in.

The EmbedderProtocol allows any backend:
  - TFIDFEmbedder  (default, no model download, fully deterministic)
  - SentenceTransformerEmbedder (opt-in, requires `pip install raven[semantic]`)
"""
from __future__ import annotations

import math
import re
from typing import Protocol


class EmbedderProtocol(Protocol):
    def encode(self, text: str) -> list[float]: ...
    def encode_batch(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...


class TFIDFEmbedder:
    """
    Corpus-free TF-IDF character-n-gram embedder.
    Fixed vocabulary of trigrams from the ASCII printable range bucketed into
    `dim` dimensions by hash. Deterministic, offline, no model required.
    """

    def __init__(self, dim: int = 512) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _tokenize(self, text: str) -> list[str]:
        # word unigrams + character trigrams
        words = re.sub(r"[^\w\s]", " ", text.lower()).split()
        trigrams = [text[i:i+3].lower() for i in range(len(text) - 2)]
        return words + trigrams

    def encode(self, text: str) -> list[float]:
        tokens = self._tokenize(text)
        if not tokens:
            return [0.0] * self._dim

        counts: dict[int, int] = {}
        for tok in tokens:
            idx = hash(tok) % self._dim
            counts[idx] = counts.get(idx, 0) + 1

        vec = [0.0] * self._dim
        total = len(tokens)
        for idx, cnt in counts.items():
            tf = cnt / total
            # IDF approximation: log(1 + 1/tf) — no corpus needed
            idf = math.log1p(1.0 / tf)
            vec[idx] = tf * idf

        # L2 normalise
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


class SentenceTransformerEmbedder:
    """Wrapper around sentence-transformers. Requires `pip install raven[semantic]`."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
        except ImportError as e:
            raise ImportError(
                "sentence-transformers not installed. "
                "Run: pip install 'raven[semantic]'"
            ) from e
        self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension()

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # Vectors assumed pre-normalised; clamp for float noise
    return max(-1.0, min(1.0, dot))


def default_embedder() -> TFIDFEmbedder:
    return TFIDFEmbedder()
