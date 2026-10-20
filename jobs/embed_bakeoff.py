"""Colab T4 job for the embedding bake-off (runs on the VM; never touches the platform).

    colab upload -s vab-embed data/bakeoff/listings.parquet /content/listings.parquet
    colab exec -s vab-embed -f jobs/embed_bakeoff.py

Reads /content/listings.parquet, embeds `text` with each model (fp16 on the GPU), L2-normalizes, and writes
/content/out/<slug>.npy (row order = Parquet order) plus /content/out/manifest.json.
"""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
from sentence_transformers import SentenceTransformer

IN = Path("/content/listings.parquet")
OUT = Path("/content/out")
# Matching is symmetric (both sides are market questions), so every text gets the same handling: bge-small as
# fastembed runs it in production (no prefix); Qwen3 with the model card's query instruction applied to both sides.
QWEN_INSTRUCT = ("Instruct: Given a prediction market question, retrieve prediction markets that resolve on the same "
                 "event and outcome\nQuery:")
MODELS = [
    {"slug": "bge-small", "id": "BAAI/bge-small-en-v1.5", "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
     "prompt": "", "batch_size": 256, "kwargs": {}},
    {"slug": "qwen3-4b", "id": "Qwen/Qwen3-Embedding-4B", "revision": "5cf2132abc99cad020ac570b19d031efec650f2b",
     "prompt": QWEN_INSTRUCT, "batch_size": 64,
     "kwargs": {"model_kwargs": {"dtype": torch.float16, "attn_implementation": "sdpa"},
                "tokenizer_kwargs": {"padding_side": "left"}}},
]


def main() -> dict:
    assert torch.cuda.is_available(), "no GPU"
    sha = hashlib.sha256(IN.read_bytes()).hexdigest()
    texts = pq.read_table(IN, columns=["text"])["text"].to_pylist()
    OUT.mkdir(exist_ok=True)
    manifest = {"input_sha256": sha, "rows": len(texts), "gpu": torch.cuda.get_device_name(0), "models": []}
    for m in MODELS:
        t0 = time.time()
        model = SentenceTransformer(m["id"], revision=m["revision"], device="cuda", **m["kwargs"])
        if m["slug"] == "bge-small":
            model.half()
        load_s = time.time() - t0
        t0 = time.time()
        v = model.encode(texts, prompt=m["prompt"] or None, batch_size=m["batch_size"], convert_to_numpy=True,
                         normalize_embeddings=True, show_progress_bar=False).astype(np.float32)
        embed_s = time.time() - t0
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        np.save(OUT / f"{m['slug']}.npy", v)
        manifest["models"].append({"slug": m["slug"], "id": m["id"], "revision": m["revision"], "dim": v.shape[1],
                                   "rows": v.shape[0], "prompt": m["prompt"], "batch_size": m["batch_size"],
                                   "dtype": "float16", "load_seconds": round(load_s, 1),
                                   "embed_seconds": round(embed_s, 1),
                                   "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2)})
        del model
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        print(manifest["models"][-1], flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


main()
