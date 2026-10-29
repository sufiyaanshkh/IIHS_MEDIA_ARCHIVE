# Worker boundary

The API persists ingestion job records, but this initial backend does not run a media-ingestion worker. Do not point it at the institutional archive and expect files to be processed yet. The next worker implementation must:
- claim jobs from a durable queue or PostgreSQL lease table;
- checkpoint each file outcome and be restart-safe;
- hash large files in bounded chunks;
- generate thumbnails and extract metadata using the helpers in `app.services.media`;
- use FFprobe/FFmpeg with timeouts and frame sampling caps for videos;
- create/update asset rows idempotently by content hash;
- keep source files read-only;
- mark progress from actual persisted outcomes, never a timer.
