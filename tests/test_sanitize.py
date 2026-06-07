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
