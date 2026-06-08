import json
import os
import sqlite3
import threading
from contextlib import contextmanager

# Bump whenever a change requires a full re-index even on an unchanged mbox —
# a new/changed column, extractor, or categorization rule. `index_is_current`
# compares this against the value stamped into `meta` at build time.
SCHEMA_VERSION = 4

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
  date_raw TEXT,
  preview TEXT
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
  size INTEGER,
  category TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  subject, from_addr, to_addr, body, attachments, content=''
);
"""


def _fts_query(q: str) -> str:
    terms = [t for t in q.split() if t]
    return " ".join('"' + t.replace('"', '""') + '"*' for t in terms)


# ORDER BY allowlist — the raw `sort` value is NEVER interpolated into SQL.
_SORT_MAP = {"date_desc": "m.date DESC", "date_asc": "m.date ASC"}


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _message_filters(date_from, date_to, from_q, has_attachment):
    """Build a list of (clause, param) for the shared message filters (bound params)."""
    where = []
    params = []
    if date_from:
        where.append("substr(m.date,1,10) >= ?")
        params.append(date_from)
    if date_to:
        where.append("substr(m.date,1,10) <= ?")
        params.append(date_to)
    if from_q:
        where.append("m.from_addr LIKE ? ESCAPE '\\'")
        params.append("%" + _like_escape(from_q) + "%")
    if has_attachment:
        where.append("EXISTS (SELECT 1 FROM attachments a WHERE a.message_id=m.id)")
    return where, params


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
        try:
            self.conn.execute("ALTER TABLE attachments ADD COLUMN category TEXT")
        except sqlite3.OperationalError:
            pass  # column already present (fresh CREATE or prior migration)
        try:
            self.conn.execute("ALTER TABLE messages ADD COLUMN preview TEXT")
        except sqlite3.OperationalError:
            pass  # column already present (fresh CREATE or prior migration)
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

    def add_message(self, offset, length, message_id, subject, from_addr, to_addr,
                    date, date_raw, preview=None):
        cur = self.conn.execute(
            "INSERT INTO messages(offset,length,message_id,subject,from_addr,to_addr,"
            "date,date_raw,preview)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (offset, length, message_id, subject, from_addr, to_addr, date, date_raw, preview))
        return cur.lastrowid

    def add_label(self, name):
        self.conn.execute("INSERT OR IGNORE INTO labels(name) VALUES(?)", (name,))
        return self.conn.execute("SELECT id FROM labels WHERE name=?", (name,)).fetchone()["id"]

    def link_label(self, message_id, label_id):
        self.conn.execute(
            "INSERT OR IGNORE INTO message_labels(message_id,label_id) VALUES(?,?)",
            (message_id, label_id))

    def add_attachment(self, message_id, idx, filename, mime, size, category):
        self.conn.execute(
            "INSERT INTO attachments(message_id,idx,filename,mime,size,category)"
            " VALUES(?,?,?,?,?,?)",
            (message_id, idx, filename, mime, size, category))

    def add_fts(self, rowid, subject, from_addr, to_addr, body, attachments):
        self.conn.execute(
            "INSERT INTO messages_fts(rowid,subject,from_addr,to_addr,body,attachments)"
            " VALUES(?,?,?,?,?,?)", (rowid, subject, from_addr, to_addr, body, attachments))

    def list_labels(self):
        rows = self.conn.execute(
            "SELECT l.name AS name, COUNT(*) AS c FROM labels l "
            "JOIN message_labels ml ON ml.label_id=l.id GROUP BY l.id ORDER BY l.name").fetchall()
        return [(r["name"], r["c"]) for r in rows]

    def list_messages(self, label, limit, offset, date_from=None, date_to=None,
                      from_q=None, has_attachment=False, sort="date_desc"):
        where, params = [], []
        if label:
            sql = ("SELECT m.* FROM messages m JOIN message_labels ml ON ml.message_id=m.id "
                   "JOIN labels l ON l.id=ml.label_id")
            where.append("l.name=?")
            params.append(label)
        else:
            sql = "SELECT m.* FROM messages m"
        fwhere, fparams = _message_filters(date_from, date_to, from_q, has_attachment)
        where.extend(fwhere)
        params.extend(fparams)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + _SORT_MAP.get(sort, _SORT_MAP["date_desc"])
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self.conn.execute(sql, params).fetchall()

    def search(self, query, label, limit, offset, date_from=None, date_to=None,
               from_q=None, has_attachment=False, sort="date_desc"):
        match = _fts_query(query)
        if not match:
            return []
        where = ["messages_fts MATCH ?"]
        params = [match]
        if label:
            sql = ("SELECT m.* FROM messages_fts f JOIN messages m ON m.id=f.rowid "
                   "JOIN message_labels ml ON ml.message_id=m.id "
                   "JOIN labels l ON l.id=ml.label_id")
            where.insert(0, "l.name=?")
            params.insert(0, label)
        else:
            sql = "SELECT m.* FROM messages_fts f JOIN messages m ON m.id=f.rowid"
        fwhere, fparams = _message_filters(date_from, date_to, from_q, has_attachment)
        where.extend(fwhere)
        params.extend(fparams)
        sql += " WHERE " + " AND ".join(where)
        # Default to relevance (rank); an explicit date sort uses the allowlist map.
        order = _SORT_MAP[sort] if sort in _SORT_MAP and sort != "date_desc" else "rank"
        sql += " ORDER BY " + order + " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
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

    def attachment_category_counts(self):
        return self.conn.execute(
            "SELECT category, COUNT(*) AS c FROM attachments GROUP BY category").fetchall()

    @staticmethod
    def _files_filter(category, query):
        """Build (where, params) for category + filename/FTS query filters."""
        where = []
        params = []
        if category:
            where.append("a.category = ?")
            params.append(category)
        q = (query or "").strip()
        if q:
            like = "%" + _like_escape(q) + "%"
            match = _fts_query(q)
            if match:
                where.append("(a.filename LIKE ? ESCAPE '\\' OR a.message_id IN"
                             " (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?))")
                params.extend([like, match])
            else:
                where.append("a.filename LIKE ? ESCAPE '\\'")
                params.append(like)
        return where, params

    def list_files_by_category(self, category, limit, offset, query=None):
        where, params = self._files_filter(category, query)
        if not where:
            return []
        sql = ("SELECT a.message_id AS message_id, a.idx AS idx, a.filename AS filename,"
               " a.size AS size, a.mime AS mime, m.subject AS subject, m.date AS date"
               " FROM attachments a JOIN messages m ON m.id = a.message_id"
               f" WHERE {' AND '.join(where)}"
               " ORDER BY a.filename LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "no such table" in message or "fts5" in message or "syntax error" in message:
                return []
            raise

    def list_files_for_export(self, category, query, limit):
        """Like list_files_by_category but returns the columns the zip builder needs,
        capped at `limit` rows (no offset)."""
        where, params = self._files_filter(category, query)
        if not where:
            return []
        sql = ("SELECT a.message_id AS message_id, a.idx AS idx, a.filename AS filename,"
               " a.mime AS mime, a.size AS size"
               " FROM attachments a JOIN messages m ON m.id = a.message_id"
               f" WHERE {' AND '.join(where)}"
               " ORDER BY a.filename LIMIT ?")
        params.append(limit)
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "no such table" in message or "fts5" in message or "syntax error" in message:
                return []
            raise

    def integrity(self):
        try:
            sample = json.loads(self.get_meta("skipped_sample") or "[]")
        except (ValueError, TypeError):
            sample = []  # never let a malformed meta value 500 the status endpoint
        return {
            "indexed": int(self.get_meta("indexed_count") or 0),
            "skipped": int(self.get_meta("skipped_count") or 0),
            "sample": sample,
        }

    def all_message_spans(self):
        return self.conn.execute("SELECT offset, length FROM messages ORDER BY offset").fetchall()
