"""Real HTTP transport of the Interpol client, built on curl_cffi."""

from collections.abc import Mapping

import curl_cffi

from interpol_collector.interpol_client import TransportResponse

# Interpol answers browser-like clients reliably; curl_cffi copies Chrome's TLS handshake.
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

    def get(self, url: str, params: Mapping[str, str | int]) -> TransportResponse:
        """Send the request and parse a JSON body when there is one."""
        response = curl_cffi.get(
            url,
            params=dict(params),
            headers=HEADERS,
            impersonate=IMPERSONATE,
            timeout=self._timeout_seconds,
        )
        try:
            body = response.json()  # type: ignore[no-untyped-call]
        except ValueError:
            body = None
        return TransportResponse(status_code=response.status_code, body=body)
