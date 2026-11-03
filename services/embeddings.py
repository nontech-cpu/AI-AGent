"""
PeculiarAI FastEmbed 384-dim Vector Embedding Service
Optimized for 512MB RAM constraints on cloud containers.
Uses ONNX Runtime under the hood (zero PyTorch overhead, ~20MB RAM).
"""
import os
import logging
from typing import List, Optional

logger = logging.getLogger("peculiar_embeddings")

_EMBED_MODEL = None
_EMBED_CACHE: dict = {}
CACHE_MAX_SIZE = 1000

def get_embedding_model():
    """Lazy-loads the lightweight ONNX-based fastembed model."""
    global _EMBED_MODEL
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL if _EMBED_MODEL is not False else None

    # Check if local embeddings are forcefully disabled
    if os.getenv("DISABLE_LOCAL_EMBEDDINGS", "0").lower() in ("1", "true", "yes"):
        logger.info("Local embeddings disabled by env var DISABLE_LOCAL_EMBEDDINGS")
        _EMBED_MODEL = False
        return None

    try:
        from fastembed import TextEmbedding
        logger.info("Initializing FastEmbed TextEmbedding (BAAI/bge-small-en-v1.5)...")
        _EMBED_MODEL = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        logger.info("FastEmbed TextEmbedding initialized successfully.")
    except Exception as exc:
        logger.warning(f"FastEmbed initialization failed: {exc}")
        _EMBED_MODEL = False

    return _EMBED_MODEL if _EMBED_MODEL is not False else None


def embed_text(text: str, is_query: bool = False) -> Optional[List[float]]:
    """
    Generates a 384-dimensional vector embedding for the input text.
    Uses bge-small-en-v1.5 prefixing for asymmetric semantic retrieval:
      - is_query=True  -> 'Represent this sentence for searching relevant passages: '
      - is_query=False -> standard passage
    """
    if not text or not text.strip():
        return None

    clean = text.strip()
    cache_key = (clean[:300].lower(), is_query)
    if cache_key in _EMBED_CACHE:
        return _EMBED_CACHE[cache_key]

    model = get_embedding_model()
    if not model:
        return None

    try:
        # BGE prompt prefix for query asymmetric search
        prefix = "Represent this sentence for searching relevant passages: " if is_query else ""
        text_to_embed = f"{prefix}{clean}"
        
        embeddings = list(model.embed([text_to_embed]))
        if embeddings:
            vec = embeddings[0].tolist()
            if len(_EMBED_CACHE) >= CACHE_MAX_SIZE:
                _EMBED_CACHE.clear()
            _EMBED_CACHE[cache_key] = vec
            return vec
    except Exception as exc:
        logger.error(f"Error generating embedding: {exc}")
        return None

    return None


def embed_query(query: str) -> Optional[List[float]]:
    """Generates embedding specifically optimized for query search."""
    return embed_text(query, is_query=True)


def embed_batch(texts: List[str]) -> List[Optional[List[float]]]:
    """Generates embeddings for a batch of texts in a single forward pass."""
    if not texts:
        return []
    model = get_embedding_model()
    if not model:
        return [None] * len(texts)

    results = []
    to_embed_indices = []
    to_embed_texts = []

    for idx, t in enumerate(texts):
        if not t or not t.strip():
            results.append(None)
            continue
        c = t.strip()
        ck = (c[:300].lower(), False)
        if ck in _EMBED_CACHE:
            results.append(_EMBED_CACHE[ck])
        else:
            results.append(None)
            to_embed_indices.append(idx)
            to_embed_texts.append(c)

    if to_embed_texts:
        try:
            embs = list(model.embed(to_embed_texts))
            for i, vec_arr in zip(to_embed_indices, embs):
                vec = vec_arr.tolist()
                results[i] = vec
                ck = (to_embed_texts[to_embed_indices.index(i)][:300].lower(), False)
                _EMBED_CACHE[ck] = vec
        except Exception as exc:
            logger.error(f"Error in embed_batch: {exc}")

    return results

