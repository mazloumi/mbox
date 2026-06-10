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


class EmbedStatus:
    """Thread-safe progress for the semantic-tier background passes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = "idle"   # idle | chunking | embedding | ready | error
        self._messages_done = 0
        self._messages_total = 0
        self._vectors_done = 0
        self._vectors_total = 0
        self._error = None

    def start_chunking(self, message_total):
        with self._lock:
            self._state = "chunking"
            self._messages_total = message_total
            self._messages_done = 0
            self._error = None

    def update_chunks(self, messages_done):
        with self._lock:
            self._messages_done = messages_done

    def start_embedding(self, vectors_total):
        with self._lock:
            self._state = "embedding"
            self._vectors_total = vectors_total
            self._vectors_done = 0

    def update_vectors(self, vectors_done):
        with self._lock:
            self._vectors_done = vectors_done

    def finish(self):
        with self._lock:
            self._state = "ready"
            if self._vectors_total:
                self._vectors_done = self._vectors_total

    def fail(self, error):
        with self._lock:
            self._state = "error"
            self._error = str(error)

    def snapshot(self):
        with self._lock:
            return {
                "state": self._state,
                "ready": self._state == "ready",
                "messages_done": self._messages_done,
                "messages_total": self._messages_total,
                "vectors_done": self._vectors_done,
                "vectors_total": self._vectors_total,
                "error": self._error,
            }
