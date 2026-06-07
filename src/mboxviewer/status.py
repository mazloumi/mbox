import threading


class IndexStatus:
    """Thread-safe holder for indexing progress, read by GET /api/status."""

    def __init__(self):
        self._lock = threading.Lock()
        self._indexing = False
        self._ready = False
        self._messages = 0
        self._bytes_done = 0
        self._bytes_total = 0
        self._error = None

    def start(self, bytes_total):
        with self._lock:
            self._indexing = True
            self._ready = False
            self._messages = 0
            self._bytes_done = 0
            self._bytes_total = bytes_total
            self._error = None

    def update(self, messages, bytes_done):
        with self._lock:
            self._messages = messages
            self._bytes_done = bytes_done

    def finish(self):
        with self._lock:
            self._indexing = False
            self._ready = True
            if self._bytes_total:
                self._bytes_done = self._bytes_total

    def fail(self, error):
        with self._lock:
            self._indexing = False
            self._ready = False
            self._error = str(error)

    def mark_ready(self, messages=0):
        with self._lock:
            self._indexing = False
            self._ready = True
            self._messages = messages

    def snapshot(self):
        with self._lock:
            total = self._bytes_total
            done = self._bytes_done
            if total:
                percent = round(done / total * 100, 1)
            else:
                percent = 100.0 if self._ready else 0.0
            return {
                "indexing": self._indexing,
                "ready": self._ready,
                "messages": self._messages,
                "bytes_done": done,
                "bytes_total": total,
                "percent": percent,
                "error": self._error,
            }
