# Local Library Database

Pixiv downloads are now indexed in `pixiv_app_library.db` by default.

For the full user-facing workflow, see `USER_GUIDE.md`.

The SQLite schema includes:

- `artists`: Pixiv artist profile snapshots.
- `artworks`: illust/novel metadata, bookmark counts, page count, safety flags, raw metadata JSON.
- `tags` and `artwork_tags`: searchable tag index.
- `artwork_files`: local file path, source URL, page index, status, and file size.
- `download_history`: every download attempt with success/failure counts.
- `sync_state`: reserved for future followed-artist/bookmark incremental sync.

This database is the foundation for thumbnail cache, local gallery, tag search, automatic sync, and WebUI features.

## Thumbnail Cache

`pixiv_app.core.thumbnails.ThumbnailCache` builds JPEG thumbnails under `runtime/thumbnails` by default. It records source path, source mtime, source size, output path, and dimensions in `thumbnail_cache`, so stale thumbnails can be regenerated when files change.

## Local Query API

Run the local gallery API:

```bash
python -m pixiv_app.services.run_gallery_api --host 127.0.0.1 --port 8765
```

Available endpoints:

- `GET /api/health`
- `GET /api/artworks?q=&tag=&artist_id=&type=&limit=60&offset=0`
- `GET /api/artworks/{artwork_id}`
- `GET /api/tags?q=&limit=100`
- `GET /api/artists?q=&limit=100`
- `GET /api/thumbnails/build?limit=200`
- `GET /media/thumbnail/{artwork_id}/{page_index}`
- `GET /media/source/{artwork_id}/{page_index}`
