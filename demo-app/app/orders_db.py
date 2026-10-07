"""Stand-in for the orders database.

The lab has no real database. connect() behaves like a client connecting to
one that listens on orders-db:5432: any other address is refused, and
FAIL_MODE makes the database unreachable.
"""

import random
import time

LISTEN_ADDRESS = ("orders-db", 5432)


def connect(host: str, port: int, fail_mode: bool) -> None:
    """Raise ConnectionRefusedError if the database can't be reached."""
    if fail_mode or (host, port) != LISTEN_ADDRESS:
        time.sleep(random.uniform(0.2, 0.5))  # waiting for the connection attempt to fail
        raise ConnectionRefusedError(f"connection refused: {host}:{port}")
