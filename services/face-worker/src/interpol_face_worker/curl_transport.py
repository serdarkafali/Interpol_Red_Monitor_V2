"""Real HTTP transport of the photo client, built on curl_cffi."""

import curl_cffi

from interpol_face_worker.interpol_photos import TransportResponse

# Interpol answers browser-like clients reliably; curl_cffi copies Chrome's TLS handshake.
# The same headers worked for photo downloads in the 2026-11-23 measurement (ADR 0011).
IMPERSONATE = "chrome120"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.interpol.int/",
    "Origin": "https://www.interpol.int",
}


class CurlTransport:
    """Sends GET requests that look like they come from Chrome."""

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self._timeout_seconds = timeout_seconds

    def get(self, url: str) -> TransportResponse:
        """Send the request and return the status code with the raw body."""
        response = curl_cffi.get(
            url,
            headers=HEADERS,
            impersonate=IMPERSONATE,
            timeout=self._timeout_seconds,
        )
        return TransportResponse(status_code=response.status_code, content=response.content)
