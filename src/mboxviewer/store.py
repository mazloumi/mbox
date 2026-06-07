import os
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  offset INTEGER NOT NULL,
  length INTEGER NOT NULL,
  message_id TEXT,
  subject TEXT,
  from_addr TEXT,
  to_addr TEXT,
  date TEXT,
  date_raw TEXT
);
CREATE TABLE IF NOT EXISTS labels (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS message_labels (
  message_id INTEGER NOT NULL REFERENCES messages(id),
  label_id INTEGER NOT NULL REFERENCES labels(id),
  PRIMARY KEY (message_id, label_id)
);
CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  idx INTEGER NOT NULL,
  filename TEXT,
  mime TEXT,
  size INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  subject, from_addr, to_addr, body, attachments, content=''
);
"""


def _fts_query(q: str) -> str:
    terms = [t for t in q.split() if t]
    return " ".join('"' + t.replace('"', '""') + '"*' for t in terms)


class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._local = threading.local()

    @property
    def conn(self):
        """A SQLite connection unique to the calling thread (lazily opened)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=5000")
            self._local.conn = c
        return c

    def message_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def create_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def clear(self):
        """Remove all indexed data so an index can be rebuilt from scratch."""
        self.conn.executescript(
            "DELETE FROM message_labels;"
            "DELETE FROM attachments;"
            "DELETE FROM labels;"
            "DELETE FROM messages;"
            "DELETE FROM meta;")
        # messages_fts is a contentless FTS5 table; the special 'delete-all'
        # command is the supported way to empty it.
        self.conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('delete-all')")
        self.conn.commit()

    @contextmanager
    def savepoint(self):
        """Per-unit transaction: on exception, roll back only this unit's writes."""
        self.conn.execute("SAVEPOINT unit")
        try:
            yield
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT unit")
            self.conn.execute("RELEASE SAVEPOINT unit")
            raise
        else:
            self.conn.execute("RELEASE SAVEPOINT unit")

    def commit(self):
        self.conn.commit()

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_meta(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def add_message(self, offset, length, message_id, subject, from_addr, to_addr, date, date_raw):
        cur = self.conn.execute(
            "INSERT INTO messages(offset,length,message_id,subject,from_addr,to_addr,date,date_raw)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (offset, length, message_id, subject, from_addr, to_addr, date, date_raw))
        return cur.lastrowid

    def add_label(self, name):
        self.conn.execute("INSERT OR IGNORE INTO labels(name) VALUES(?)", (name,))
        return self.conn.execute("SELECT id FROM labels WHERE name=?", (name,)).fetchone()["id"]

    def link_label(self, message_id, label_id):
        self.conn.execute(
            "INSERT OR IGNORE INTO message_labels(message_id,label_id) VALUES(?,?)",
            (message_id, label_id))

    def add_attachment(self, message_id, idx, filename, mime, size):
        self.conn.execute(
            "INSERT INTO attachments(message_id,idx,filename,mime,size) VALUES(?,?,?,?,?)",
            (message_id, idx, filename, mime, size))

    def add_fts(self, rowid, subject, from_addr, to_addr, body, attachments):
        self.conn.execute(
            "INSERT INTO messages_fts(rowid,subject,from_addr,to_addr,body,attachments)"
            " VALUES(?,?,?,?,?,?)", (rowid, subject, from_addr, to_addr, body, attachments))

    def list_labels(self):
        rows = self.conn.execute(
            "SELECT l.name AS name, COUNT(*) AS c FROM labels l "
            "JOIN message_labels ml ON ml.label_id=l.id GROUP BY l.id ORDER BY l.name").fetchall()
        return [(r["name"], r["c"]) for r in rows]

    def list_messages(self, label, limit, offset):
        if label:
            return self.conn.execute(
                "SELECT m.* FROM messages m JOIN message_labels ml ON ml.message_id=m.id "
                "JOIN labels l ON l.id=ml.label_id WHERE l.name=? "
                "ORDER BY m.date DESC LIMIT ? OFFSET ?", (label, limit, offset)).fetchall()
        return self.conn.execute(
            "SELECT * FROM messages ORDER BY date DESC LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()

    def search(self, query, label, limit, offset):
        match = _fts_query(query)
        if not match:
            return []
        if label:
            sql = ("SELECT m.* FROM messages_fts f JOIN messages m ON m.id=f.rowid "
                   "JOIN message_labels ml ON ml.message_id=m.id "
                   "JOIN labels l ON l.id=ml.label_id "
                   "WHERE l.name=? AND messages_fts MATCH ? ORDER BY rank LIMIT ? OFFSET ?")
            params = (label, match, limit, offset)
        else:
            sql = ("SELECT m.* FROM messages_fts f JOIN messages m ON m.id=f.rowid "
                   "WHERE messages_fts MATCH ? ORDER BY rank LIMIT ? OFFSET ?")
            params = (match, limit, offset)
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "no such table" in message or "fts5" in message or "syntax error" in message:
                return []
            raise

    def get_message_row(self, message_id):
        return self.conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()

    def get_attachments(self, message_id):
        return self.conn.execute(
            "SELECT * FROM attachments WHERE message_id=? ORDER BY idx", (message_id,)).fetchall()

    def attachment_mime_counts(self):
        return self.conn.execute(
            "SELECT mime, COUNT(*) AS c FROM attachments GROUP BY mime").fetchall()

    def list_files_by_mimes(self, mimes, limit, offset):
        # Drop NULLs: SQLite's `IN (NULL)` silently matches nothing, which would
        # produce a degenerate query rather than an honest empty result.
        mimes = [m for m in mimes if m is not None]
        if not mimes:
            return []
        placeholders = ",".join("?" * len(mimes))
        return self.conn.execute(
            "SELECT a.message_id AS message_id, a.idx AS idx, a.filename AS filename,"
            " a.size AS size, a.mime AS mime, m.subject AS subject, m.date AS date"
            " FROM attachments a JOIN messages m ON m.id = a.message_id"
            f" WHERE a.mime IN ({placeholders})"
            " ORDER BY a.filename LIMIT ? OFFSET ?",
            (*mimes, limit, offset)).fetchall()

    def all_message_spans(self):
        return self.conn.execute("SELECT offset, length FROM messages ORDER BY offset").fetchall()
