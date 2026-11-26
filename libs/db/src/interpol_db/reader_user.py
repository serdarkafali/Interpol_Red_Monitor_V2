"""Creates the login user of the web interface, with read-only rights (ADR 0013).

Run by the migrate step right after ``alembic upgrade head``:

    python -m interpol_db.reader_user

The read-only role ``notice_reader`` is created by a migration. Only the login user is
made here, because its password is a secret that must not live in a migration file. The
password comes from ``WEB_DATABASE_PASSWORD``; without it the step is skipped, so a setup
without the web interface still works. The password is never printed.
"""

import os
from typing import Any

from psycopg import sql
from sqlalchemy import Connection, create_engine, text

READER_ROLE = "notice_reader"
DEFAULT_USER = "web"


def ensure_reader_user(connection: Connection, user: str, password: str) -> bool:
    """Create the login user, or set its password if it exists; give it the reader role.

    Return True if the user was created. CREATE ROLE and ALTER ROLE take no query
    parameters, so the name and the password are quoted with psycopg's sql module:
    Identifier for names, Literal for values. Neither can break out of its quotes.
    """
    exists = (
        connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :user"), {"user": user}
        ).first()
        is not None
    )
    verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
    _execute(
        connection,
        sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
            verb, sql.Identifier(user), sql.Literal(password)
        ),
    )
    _execute(
        connection,
        sql.SQL("GRANT {} TO {}").format(sql.Identifier(READER_ROLE), sql.Identifier(user)),
    )
    return not exists


def _execute(connection: Connection, statement: sql.Composed) -> None:
    """Run a composed statement on the driver's own cursor, in the current transaction.

    Not through SQLAlchemy: it would hand the text to the driver with parameters, and a
    "%" in the password would then be read as a placeholder.
    """
    driver_connection: Any = connection.connection.driver_connection
    with driver_connection.cursor() as cursor:
        cursor.execute(statement)


def main() -> None:
    """Entry point of ``python -m interpol_db.reader_user``."""
    password = os.environ.get("WEB_DATABASE_PASSWORD", "")
    if not password:
        print("reader_user: WEB_DATABASE_PASSWORD is not set; web user skipped")
        return
    user = os.environ.get("WEB_DATABASE_USER") or DEFAULT_USER
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            created = ensure_reader_user(connection, user, password)
    finally:
        engine.dispose()
    print(f"reader_user: {user} {'created' if created else 'updated'} with read-only access")


if __name__ == "__main__":
    main()
