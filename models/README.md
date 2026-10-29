# Local model assets

Do not commit model weights. Download them on the target machine after verifying the exact model card and license.

## EmbeddingGemma 2
The backend defaults to embeddings disabled. Set EMBEDDING_PROVIDER=sentence_transformers and EMBEDDING_MODEL_PATH to a local model directory only after validating the runtime, task prefixes, vector dimensions, memory, and licensing. Loading a model does not by itself implement vector search; vector indexing and query/document encoding still need to be integrated and evaluated.

## Video
Install FFmpeg/FFprobe locally or use the supplied Docker image. Video processing should sample frames at a configurable interval, impose duration/frame limits, and avoid extracting every frame by default.
