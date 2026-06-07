CATEGORY_ORDER = [
    "Documents", "Spreadsheets", "Presentations", "Images",
    "Archives", "Calendar", "Media", "Other",
]

_DOCUMENTS = {
    "application/pdf", "application/msword", "application/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
}
_SPREADSHEETS = {
    "application/vnd.ms-excel", "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
}
_PRESENTATIONS = {
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.presentation",
}
_ARCHIVES = {
    "application/zip", "application/x-zip-compressed", "application/gzip",
    "application/x-gzip", "application/x-tar", "application/x-rar-compressed",
    "application/vnd.rar", "application/x-7z-compressed",
}


def category_for_mime(mime):
    m = (mime or "").lower().split(";")[0].strip()
    if m.startswith("image/"):
        return "Images"
    if m.startswith("audio/") or m.startswith("video/"):
        return "Media"
    if m == "text/calendar":
        return "Calendar"
    if m == "text/csv":
        return "Spreadsheets"
    if m in _DOCUMENTS:
        return "Documents"
    if m in _SPREADSHEETS:
        return "Spreadsheets"
    if m in _PRESENTATIONS:
        return "Presentations"
    if m in _ARCHIVES:
        return "Archives"
    if m.startswith("text/"):
        return "Documents"
    return "Other"
