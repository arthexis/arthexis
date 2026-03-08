from pathlib import Path


def copy_to_media(bucket, media_model, old_file):
    if not old_file:
        return None
    filename = Path(old_file.name).name
    old_file.open("rb")
    try:
        media_file = media_model(
            bucket=bucket,
            original_name=filename,
            content_type=getattr(old_file, "content_type", "") or "",
            size=getattr(old_file, "size", 0) or 0,
        )
        media_file.file.save(filename, old_file, save=False)
        media_file.save()
    finally:
        old_file.close()
    try:
        old_file.delete(save=False)
    except Exception:
        pass
    return media_file
