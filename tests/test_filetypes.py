from mboxviewer.filetypes import category_for_mime, CATEGORY_ORDER


def test_categories():
    cases = {
        "application/pdf": "Documents",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Documents",
        "text/plain": "Documents",
        "text/csv": "Spreadsheets",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Spreadsheets",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "Presentations",
        "image/png": "Images",
        "image/jpeg": "Images",
        "application/zip": "Archives",
        "text/calendar": "Calendar",
        "audio/mpeg": "Media",
        "video/mp4": "Media",
        "application/octet-stream": "Other",
        "": "Other",
        None: "Other",
    }
    for mime, expected in cases.items():
        assert category_for_mime(mime) == expected, mime


def test_mime_params_stripped_and_case_insensitive():
    assert category_for_mime("IMAGE/PNG; name=x.png") == "Images"


def test_every_result_is_in_category_order():
    for mime in ["application/pdf", "image/png", "audio/x", "application/zip", "x/y"]:
        assert category_for_mime(mime) in CATEGORY_ORDER
