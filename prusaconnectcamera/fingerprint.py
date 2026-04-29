"""Camera fingerprint helpers.

Fingerprints are stored in the JSON config file so camera identity remains
stable across restarts and config edits unless the operator explicitly removes
or changes the value.
"""

import uuid


def generate_fingerprint() -> str:
    """Return a freshly generated UUID fingerprint string."""
    return str(uuid.uuid4())


def is_valid_fingerprint(value: str) -> bool:
    """Return True when *value* is a valid UUID string."""
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (AttributeError, TypeError, ValueError):
        return False
