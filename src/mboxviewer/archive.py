import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .reader import read_message, get_display_body
from .assets import extract_image_refs, is_tracking_pixel, fetch_image, url_hash, write_asset_bytes

MAX_WORKERS = 12
MAX_FETCH_ATTEMPTS = 3


def _now():
    return datetime.now(timezone.utc).isoformat()


class ArchiveStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._messages_scanned = 0
        self._total_messages = 0
        self._urls_seen = 0
        self._downloaded = 0
        self._skipped = 0
        self._failed = 0
        self._error = None

    def start(self, total):
        with self._lock:
            self._running = True
            self._total_messages = total
            self._messages_scanned = self._urls_seen = 0
            self._downloaded = self._skipped = self._failed = 0
            self._error = None

    def mark_running(self):
        with self._lock:
            self._running = True

    def complete_from_counts(self, counts):
        with self._lock:
            self._running = False
            self._downloaded = counts.get("ok", 0)
            self._skipped = counts.get("skipped", 0)
            self._failed = counts.get("failed", 0)
            self._error = None

    def _inc(self, name):
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)

    def inc_scanned(self): self._inc("_messages_scanned")
    def inc_urls_seen(self): self._inc("_urls_seen")
    def inc_downloaded(self): self._inc("_downloaded")
    def inc_skipped(self): self._inc("_skipped")
    def inc_failed(self): self._inc("_failed")

    def finish(self):
        with self._lock:
            self._running = False

    def fail(self, error):
        with self._lock:
            self._running = False
            self._error = str(error)

    def running(self):
        with self._lock:
            return self._running

    def snapshot(self):
        with self._lock:
            return {
                "running": self._running,
                "messages_scanned": self._messages_scanned,
                "total_messages": self._total_messages,
                "urls_seen": self._urls_seen,
                "downloaded": self._downloaded,
                "skipped": self._skipped,
                "failed": self._failed,
                "error": self._error,
            }


def run_archive(settings, store, asset_store, status):
    """Archive remote images. Short-circuits when the mbox is unchanged and nothing
    failed. All asset_store writes happen on this thread; workers only fetch."""
    try:
        cur_size = os.path.getsize(settings.mbox_path)
        cur_mtime = int(os.path.getmtime(settings.mbox_path))
        meta = asset_store.get_archive_meta()
        counts = asset_store.asset_counts()
        if (meta and meta["source_size"] == cur_size and meta["source_mtime"] == cur_mtime
                and counts["failed"] == 0):
            status.complete_from_counts(counts)
            return

        spans = store.all_message_spans()
        status.start(len(spans))
        seen = set()
        to_download = []  # (hash, url, width, height)
        for row in spans:
            try:
                msg = read_message(settings.mbox_path, row["offset"], row["length"])
                mime, content = get_display_body(msg)
                if mime == "text/html":
                    for url, width, height in extract_image_refs(content):
                        h = url_hash(url)
                        if h in seen:
                            continue
                        seen.add(h)
                        status.inc_urls_seen()
                        if asset_store.asset_status(h) in ("ok", "skipped", "gave_up"):
                            continue
                        if is_tracking_pixel(url, width, height):
                            asset_store.upsert_asset(h, url, None, None, width, height, "skipped", None, _now())
                            status.inc_skipped()
                        else:
                            to_download.append((h, url, width, height))
            except Exception as exc:  # noqa: BLE001 - skip a bad message, keep going
                sys.stderr.write(f"archive scan skip: {exc}\n")
            status.inc_scanned()
        asset_store.commit()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(fetch_image, url): (h, url, width, height)
                       for (h, url, width, height) in to_download}
            for future in as_completed(futures):
                h, url, width, height = futures[future]
                res = future.result()
                if res.ok:
                    write_asset_bytes(settings.archive_dir, h, res.data)
                    asset_store.upsert_asset(h, url, res.content_type, len(res.data),
                                             width, height, "ok", None, _now())
                    status.inc_downloaded()
                elif res.skip:
                    # Deterministic policy skip (non-image / unsafe type): terminal, so a
                    # re-run won't re-fetch it and the unchanged-mbox short-circuit holds.
                    asset_store.upsert_asset(h, url, None, None, width, height, "skipped", res.error, _now())
                    status.inc_skipped()
                else:
                    attempts = asset_store.get_attempts(h) + 1
                    failed_status = "gave_up" if attempts >= MAX_FETCH_ATTEMPTS else "failed"
                    asset_store.upsert_asset(h, url, None, None, width, height, failed_status,
                                             res.error, _now(), attempts=attempts)
                    status.inc_failed()
        asset_store.commit()
        asset_store.set_archive_meta(cur_size, cur_mtime)
        status.finish()
    except Exception as exc:  # noqa: BLE001 - surface any fatal error to the UI
        sys.stderr.write(f"archive failed: {exc}\n")
        status.fail(exc)
