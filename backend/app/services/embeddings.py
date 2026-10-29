"""Local EmbeddingGemma adapter boundary.

The backend is intentionally safe when the model is not installed: lexical search still works.
Wire the exact local runtime and task prefixes after validating the downloaded model artifact.
No cloud endpoint is called by this module.
"""
from pathlib import Path
from ..config import settings


class EmbeddingService:
    def __init__(self) -> None:
        self.model_path: Path = settings.embedding_model_path
        self.enabled = settings.embedding_provider.lower() == "sentence_transformers" and self.model_path.exists()
        self.model = None

    def load(self) -> bool:
        if not self.enabled:
            return False
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(str(self.model_path), local_files_only=True, trust_remote_code=False)
            return True
        except Exception:
            self.model = None
            return False

    def encode(self, text: str) -> list[float] | None:
        if self.model is None and not self.load():
            return None
        vector = self.model.encode(text, normalize_embeddings=True)
        return vector.tolist()


embedding_service = EmbeddingService()
