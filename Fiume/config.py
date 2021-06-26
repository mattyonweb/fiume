from pathlib import Path

CLIENT_VERSION = b"0010"
CLIENT_INFO = b"-FU" + CLIENT_VERSION + b"-"

DOT_DIRECTORY = Path.home() / ".fiume"
if not DOT_DIRECTORY.exists():
    Path.mkdir(DOT_DIRECTORY)

BITMAPS_DIR = DOT_DIRECTORY / "bitmaps"
if not BITMAPS_DIR.exists():
    Path.mkdir(BITMAPS_DIR)

IN_DOWNLOAD_FILE = DOT_DIRECTORY / "downloading.json"
if not IN_DOWNLOAD_FILE.exists():
    IN_DOWNLOAD_FILE.touch()
