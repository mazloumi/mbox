import os
import sqlite3
import threading

ASSET_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  url_hash TEXT PRIMARY KEY,
  url TEXT,
  content_type TEXT,
  size INTEGER,
  width INTEGER,
  height INTEGER,
  status TEXT NOT NULL,
  error TEXT,
  fetched_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS archive_meta (key TEXT PRIMARY KEY, value TEXT);
"""


class AssetStore:
    """Owns archive.db (asset metadata + archive_meta) in the durable archive dir."""

    def __init__(self, archive_dir):
        os.makedirs(archive_dir, exist_ok=True)
        self._db_path = os.path.join(archive_dir, "archive.db")
        self._local = threading.local()

    @property
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=5000")
            self._local.conn = c
        return c

    def create_schema(self):
        self.conn.executescript(ASSET_SCHEMA)
        try:
            self.conn.execute("ALTER TABLE assets ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError as exc:
            # Expected when the column already exists (fresh DB, or a prior migration).
            # Re-raise anything else (e.g. a locked/IO error) instead of silently
            # leaving the column absent and breaking later upserts.
            if "duplicate column" not in str(exc).lower():
                raise
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    def upsert_asset(self, url_hash, url, content_type, size, width, height, status, error,
                     fetched_at, attempts=0):
        self.conn.execute(
            "INSERT INTO assets(url_hash,url,content_type,size,width,height,status,error,fetched_at,attempts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url_hash) DO UPDATE SET"
            " url=excluded.url, content_type=excluded.content_type, size=excluded.size,"
            " width=excluded.width, height=excluded.height, status=excluded.status,"
            " error=excluded.error, fetched_at=excluded.fetched_at,"
            " attempts=MAX(assets.attempts, excluded.attempts)",  # monotonic: never reset the retry counter
            (url_hash, url, content_type, size, width, height, status, error, fetched_at, attempts))

    def get_asset(self, url_hash):
        return self.conn.execute("SELECT * FROM assets WHERE url_hash=?", (url_hash,)).fetchone()

    def asset_status(self, url_hash):
        row = self.conn.execute("SELECT status FROM assets WHERE url_hash=?", (url_hash,)).fetchone()
        return row["status"] if row else None

    def get_attempts(self, url_hash):
        row = self.conn.execute("SELECT attempts FROM assets WHERE url_hash=?", (url_hash,)).fetchone()
        return row["attempts"] if row else 0

    def cached_asset_hashes(self, url_hashes):
        # Query in batches so a message with a pathological number of distinct image
        # URLs can't exceed SQLite's bound-parameter limit.
        hs = list(url_hashes)
        result = set()
        for i in range(0, len(hs), 500):
            batch = hs[i:i + 500]
            placeholders = ",".join("?" * len(batch))
            rows = self.conn.execute(
                f"SELECT url_hash FROM assets WHERE status='ok' AND url_hash IN ({placeholders})", batch).fetchall()
            result.update(r["url_hash"] for r in rows)
        return result

    def asset_counts(self):
        rows = self.conn.execute("SELECT status, COUNT(*) c FROM assets GROUP BY status").fetchall()
        by = {r["status"]: r["c"] for r in rows}
        return {"ok": by.get("ok", 0), "skipped": by.get("skipped", 0),
                "failed": by.get("failed", 0), "gave_up": by.get("gave_up", 0),
                "total": sum(by.values())}

    def get_archive_meta(self):
        rows = self.conn.execute("SELECT key, value FROM archive_meta").fetchall()
        d = {r["key"]: r["value"] for r in rows}
        if "source_size" in d and "source_mtime" in d:
            return {"source_size": int(d["source_size"]), "source_mtime": int(d["source_mtime"])}
        return None

    def set_archive_meta(self, size, mtime):
        for key, value in (("source_size", str(size)), ("source_mtime", str(mtime))):
            self.conn.execute(
                "INSERT INTO archive_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()
