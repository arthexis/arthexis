# Gallery ↔ Content Sample integration design

## Intent

Enable gallery uploads to conditionally participate in the Content Sample
pipeline without turning `GalleryImage` into a subclass of `ContentSample`.

## Why composition instead of subclassing

`apps/content` and `apps/gallery` represent different workflows:

- `ContentSample` focuses on ingestion, hash-based de-duplication, and
  classifier execution.
- `GalleryImage` focuses on publication metadata, ownership, and visibility.

Sub-classing `GalleryImage(ContentSample)` would force gallery concerns into the
capture/classification lifecycle and create an ambiguous source of truth for
binary storage (`ContentSample.path` vs `MediaFile.file`).

Composition keeps both systems explicit and independently maintainable.

## Data model

Add an optional link from gallery images to content samples:

- `GalleryImage.content_sample -> ContentSample (nullable, SET_NULL)`

This allows either:

1. gallery-only media (`content_sample` is null), or
2. gallery media linked to a reusable `ContentSample`.

## Upload behavior

Gallery upload now remains media-first, and conditionally creates a content
sample when requested:

1. Save uploaded binary as `MediaFile` in the gallery media bucket.
2. If caller requests sample linkage, call `save_content_sample(...)` with:
   - `kind=IMAGE`
   - `method="GAL_UPLOAD"`
   - `link_duplicates=True`
3. Persist `GalleryImage` with `content_sample` set to the returned sample.

Using `link_duplicates=True` allows multiple uploaded images to point at a
single deduplicated `ContentSample` when the payload hash matches.

## Integration touchpoints

- Gallery upload form supports a conditional "Also create a Content Sample
  record" option.
- `gallery media upload` command supports `--as-content-sample`.
- Gallery admin list displays linked content sample references.

## Non-goals

- Replacing `MediaFile` with `ContentSample` as the gallery storage backend.
- Running classifiers for every gallery upload by default.
- Changing existing gallery visibility/ownership rules.
