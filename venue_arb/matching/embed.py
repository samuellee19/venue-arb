from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

MODEL = "BAAI/bge-small-en-v1.5"
CACHE_DIR = Path.home() / ".cache" / "fastembed"  # persistent: the default /tmp is wiped by a WSL restart
_model: TextEmbedding | None = None


def text_for(row: dict) -> str:
    return f"{row['title']} | {row['yes_label']} | closes {row['close_time'].date().isoformat()}"


def embed(texts: list[str]) -> np.ndarray:
    global _model
    if _model is None:
        _model = TextEmbedding(MODEL, cache_dir=str(CACHE_DIR))
    v = np.array(list(_model.embed(texts)), dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)
