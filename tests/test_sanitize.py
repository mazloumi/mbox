from mboxviewer.sanitize import sanitize_html


def test_strips_script():
    out = sanitize_html("<p>hi</p><script>alert(1)</script>", allow_remote=False)
    assert "<script>" not in out and "alert" not in out
    assert "hi" in out


def test_blocks_remote_image_by_default():
    out = sanitize_html('<img src="http://tracker.example/x.gif">', allow_remote=False)
    assert "tracker.example" not in out


def test_allows_remote_image_when_opted_in():
    out = sanitize_html('<img src="http://imgs.example/x.png">', allow_remote=True)
    assert "imgs.example" in out


def test_keeps_basic_formatting():
    out = sanitize_html("<p>Hello <b>Bob</b></p>", allow_remote=False)
    assert "<b>" in out and "Bob" in out


def test_strips_unclosed_script():
    out = sanitize_html("<p>ok</p><script>alert(1)", allow_remote=False)
    assert "alert" not in out and "ok" in out


def test_strips_nested_script_text():
    out = sanitize_html("<script><script>x</script>alert(1)</script><p>ok</p>", allow_remote=False)
    assert "alert" not in out and "ok" in out


def test_preserves_inline_style():
    out = sanitize_html('<p style="color: red">hi</p>', allow_remote=False)
    assert "color" in out and "hi" in out


def test_blocks_remote_css_background():
    out = sanitize_html('<div style="background: url(http://tracker.example/x.png)">hi</div>', allow_remote=False)
    assert "tracker.example" not in out


def test_stray_close_script_preserves_content():
    out = sanitize_html("<p>ok</p></script><p>more</p>", allow_remote=False)
    assert "ok" in out and "more" in out


def test_blocks_protocol_relative_image():
    out = sanitize_html('<img src="//tracker.example/x.gif">', allow_remote=False)
    assert "tracker.example" not in out


def test_allows_protocol_relative_when_opted_in():
    out = sanitize_html('<img src="//imgs.example/x.png">', allow_remote=True)
    assert "imgs.example" in out
