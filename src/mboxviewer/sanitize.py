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

_REMOTE_SRC = re.compile(r'\ssrc\s*=\s*(["\'])\s*(?:https?:)?//[^"\']*\1', re.IGNORECASE)
_REMOTE_CSS_URL = re.compile(r'url\(\s*["\']?\s*(?:https?:)?//[^)]*\)', re.IGNORECASE)


class _DangerousStripper(HTMLParser):
    """Re-emit HTML with dangerous elements (and their content) removed.

    Special care is needed for RAWTEXT elements (script, style): Python's
    HTMLParser does not parse tags inside them, so <script><script>x</script>
    is tokenised as START:script / DATA:"<script>x" / END:script. That yields
    one extra dangerous close tag, after which inner text can leak out at depth
    0. We record the output length at each normal dangerous close (_last_close_
    fence) and, on a *stray* close, roll back to it — removing only the leaked
    content, not unrelated markup that came before any dangerous element.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list = []
        self._skip_depth = 0
        self._dangerous_stack: list = []  # tag names of open dangerous elements
        self._last_close_fence = None     # out length at the most recent dangerous close

    def handle_starttag(self, tag, attrs):
        if tag in _DANGEROUS_TAGS:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
                self._dangerous_stack.append(tag)
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
                    # Normal close of an open dangerous element.
                    self._skip_depth -= 1
                    if self._dangerous_stack and self._dangerous_stack[-1] == tag:
                        self._dangerous_stack.pop()
                    self._last_close_fence = len(self.out)
                elif self._last_close_fence is not None:
                    # Stray close caused by the RAWTEXT quirk: drop only the text
                    # that leaked since the most recent dangerous close.
                    del self.out[self._last_close_fence:]
                # else: a stray close with no prior dangerous element — ignore it
                # rather than destroying unrelated content that came before.
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
