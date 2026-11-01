"""Embeddings SPECTER2 (allenai), conçus pour la similarité entre papiers.

- Adapter "proximity" pour les papiers (titre [SEP] abstract).
- Adapter "adhoc_query" pour les requêtes courtes en texte libre (chargé à la demande).
Entrée limitée à 512 tokens : adapté à titre + abstract, pas au texte intégral.
"""

import threading
from functools import lru_cache

import numpy as np

from .config import settings


class Specter2Embedder:
    def __init__(self, base: str, adapter: str, batch_size: int = 16, device: str | None = None):
        try:
            import torch
            from adapters import AutoAdapterModel
            from transformers import AutoTokenizer
        except ImportError as e:
            raise RuntimeError(
                "Dépendances d'embedding absentes : pip install -e '.[embeddings]'"
            ) from e

        self._torch = torch
        self.name = adapter  # clé stockée en base (colonne model)
        self.batch_size = batch_size
        self.tokenizer = AutoTokenizer.from_pretrained(base)
        self.model = AutoAdapterModel.from_pretrained(base)
        self.model.load_adapter(adapter, source="hf", load_as="proximity", set_active=True)
        self._has_query_adapter = False
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device).eval()
        # Changer d'adapter actif n'est pas sûr entre threads (FastAPI sert en parallèle).
        self._lock = threading.Lock()

    def embed_papers(self, items: list[tuple[str, str | None]]) -> np.ndarray:
        """items = [(titre, abstract), ...] -> matrice (n, dim) normalisée."""
        sep = self.tokenizer.sep_token
        texts = [f"{title}{sep}{abstract or ''}" for title, abstract in items]
        with self._lock:
            self.model.set_active_adapters("proximity")
            return self._encode(texts)

    def embed_query(self, text: str) -> np.ndarray:
        with self._lock:
            if not self._has_query_adapter:
                self.model.load_adapter(
                    "allenai/specter2_adhoc_query", source="hf", load_as="adhoc_query"
                )
                self.model.to(self.device)
                self._has_query_adapter = True
            self.model.set_active_adapters("adhoc_query")
            try:
                return self._encode([text])[0]
            finally:
                self.model.set_active_adapters("proximity")

    def _encode(self, texts: list[str]) -> np.ndarray:
        torch = self._torch
        out = []
        with torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                batch = self.tokenizer(
                    texts[i : i + self.batch_size],
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                    return_token_type_ids=False,
                ).to(self.device)
                cls = self.model(**batch).last_hidden_state[:, 0, :]
                out.append(torch.nn.functional.normalize(cls, dim=-1).cpu().numpy())
        return np.vstack(out).astype(np.float32)


@lru_cache(maxsize=1)
def get_embedder() -> Specter2Embedder:
    """Chargé une seule fois (le modèle pèse ~500 Mo)."""
    return Specter2Embedder(
        settings.embedding_base, settings.embedding_model, settings.embedding_batch_size
    )
