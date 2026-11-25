"""A fake Interpol photo server for end-to-end runs of the face worker (ADR 0011, 0012).

No real photo is ever served or downloaded. The answer depends on the notice in the URL,
so that each path of the face worker can be seen in one run with
scripts/produce_fake_events.py:

- 1900-2: an HTML page instead of a photo (rejected as not_jpeg)
- 1900-6: 404 (rejected as not_found)
- a picture with a file <picture_id>.jpg in FAKE_INTERPOL_FACES_DIR: that file, a
  synthetic face made by scripts/prepare_e2e_faces.py
- any other picture: an 8x8 grey JPEG made here (a photo without a face)

Photos are sent with Interpol's non-standard content type.

It is started by docker-compose.e2e.yml; see that file for how to run it.
"""

import base64
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8080
# Synthetic face photos, if prepared; without them every photo is the grey JPEG.
FACES_DIR = Path(os.environ.get("FAKE_INTERPOL_FACES_DIR", "/faces"))
PHOTO_PATH = re.compile(r"^/notices/v1/red/(?P<url_id>[^/]+)/images/(?P<picture_id>[^/]+)$")
# An 8x8 grey JPEG; no person, no real photo.
GREY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxO"
    "QERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2Nj"
    "Y2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAAIAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKB"
    "kaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1"
    "dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcF"
    "BAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
    "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0"
    "tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDpKKKKAP/Z"
)


class FakeInterpolHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        match = PHOTO_PATH.match(self.path)
        if match is None or match["url_id"] == "1900-6":
            self._answer(404, "application/json", b'{"message": "Not found"}')
        elif match["url_id"] == "1900-2":
            self._answer(200, "text/html", b"<html>not a photo</html>")
        else:
            face = FACES_DIR / f"{match['picture_id']}.jpg"
            photo = face.read_bytes() if face.is_file() else GREY_JPEG
            # Interpol sends "image/JPG", not the standard "image/jpeg" (measured 2026-11-23).
            self._answer(200, "image/JPG", photo)

    def _answer(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"fake-interpol {self.address_string()} {format % args}", flush=True)


if __name__ == "__main__":
    faces = len(list(FACES_DIR.glob("*.jpg"))) if FACES_DIR.is_dir() else 0
    print(f"fake-interpol listening on port {PORT}, {faces} synthetic faces", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), FakeInterpolHandler).serve_forever()
