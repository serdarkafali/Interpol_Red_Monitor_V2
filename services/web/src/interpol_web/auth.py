"""Username and password check in front of every page (ADR 0013)."""

import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from interpol_web.config import WebSettings

# Reads the user name and password the browser sends with every request.
security = HTTPBasic()


def require_login(settings: WebSettings) -> Callable[[HTTPBasicCredentials], None]:
    """Return a check that lets a request through only with the configured credentials."""

    def check(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> None:
        """Raise 401 unless both the user name and the password are right.

        compare_digest takes the same time whatever the input, so the answer time does
        not reveal how much of a guess was right; both are compared before deciding.
        """
        user_ok = secrets.compare_digest(credentials.username.encode(), settings.username.encode())
        password_ok = secrets.compare_digest(
            credentials.password.encode(), settings.password.get_secret_value().encode()
        )
        if not (user_ok and password_ok):
            # The header makes the browser show its user name and password window.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic"},
            )

    return check
