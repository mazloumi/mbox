import re
from html.parser import HTMLParser

import bleach
from bleach.css_sanitizer import CSSSanitizer

ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    "p", "br", "div", "span", "img", "table", "thead", "tbody", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr", "u", "font",
]
ALLOWED_ATTRS = {
    "*": ["style", "align", "width", "height", "color"],
    "a": ["href", "title", "target"],
    "img": ["src", "alt", "width", "height"],
    "font": ["color", "face", "size"],
}

# Preserve inline CSS (emails rely heavily on it) while validating it. Remote
# url() references are additionally stripped below when remote content is blocked.
_CSS_SANITIZER = CSSSanitizer()

# Elements whose entire subtree must be dropped. bleach with strip=True would keep
# their text content (e.g. a <script> body "alert(1)") as a bare text node, so we
# remove them up-front with a real HTML parser that tracks nesting depth (this
# handles nested and unclosed dangerous tags, which a regex cannot do reliably).
_DANGEROUS_TAGS = {
    "script", "style", "object", "embed", "applet", "form",
    "iframe", "frame", "frameset", "link", "meta", "base",
}
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

_REMOTE_SRC = re.compile(r'\ssrc\s*=\s*(["\'])\s*https?://[^"\']*\1', re.IGNORECASE)
_REMOTE_CSS_URL = re.compile(r'url\(\s*["\']?\s*https?://[^)]*\)', re.IGNORECASE)


class _DangerousStripper(HTMLParser):
    """Re-emit HTML with dangerous elements (and their content) removed.

    Special care is needed for RAWTEXT elements (script, style): Python's
    HTMLParser does not parse tags inside them, so <script><script>x</script>
    is tokenised as START:script / DATA:"<script>x" / END:script. The inner
    text before a *stray* dangerous close tag must also be suppressed — we do
    this by recording a "fence" index into self.out when we open a dangerous
    element and rolling back to that fence if we encounter a stray dangerous
    close (skip_depth == 0 at close time, meaning content leaked out).
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list = []
        self._skip_depth = 0
        # Stack of (tag, fence_index) pushed each time we open a dangerous tag.
        self._dangerous_stack: list = []

    def handle_starttag(self, tag, attrs):
        if tag in _DANGEROUS_TAGS:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
                self._dangerous_stack.append((tag, len(self.out)))
            return
        if self._skip_depth:
            return
        self.out.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if tag in _DANGEROUS_TAGS or self._skip_depth:
            return
        self.out.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag in _DANGEROUS_TAGS:
            if tag not in _VOID_TAGS:
                if self._skip_depth:
                    # Normal close: pop the matching fence.
                    self._skip_depth -= 1
                    if self._dangerous_stack and self._dangerous_stack[-1][0] == tag:
                        self._dangerous_stack.pop()
                else:
                    # Stray close: content between the previous dangerous close
                    # and here was emitted at depth 0 but belongs to the outer
                    # dangerous element (RAWTEXT parser quirk). Roll it back.
                    if self._dangerous_stack:
                        _, fence = self._dangerous_stack.pop()
                        del self.out[fence:]
                    else:
                        # No matching open seen — clear everything emitted so
                        # far to be conservative.
                        self.out.clear()
            return
        if self._skip_depth:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._skip_depth:
            self.out.append(data)


def _strip_dangerous(html: str) -> str:
    parser = _DangerousStripper()
    parser.feed(html or "")
    parser.close()
    return "".join(parser.out)


def sanitize_html(html: str, allow_remote: bool = False) -> str:
    pre_stripped = _strip_dangerous(html or "")
    cleaned = bleach.clean(
        pre_stripped, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS,
        css_sanitizer=_CSS_SANITIZER, strip=True)
    if not allow_remote:
        cleaned = _REMOTE_SRC.sub(' src=""', cleaned)
        cleaned = _REMOTE_CSS_URL.sub("url()", cleaned)
    return cleaned
