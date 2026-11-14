import base64
import hashlib
import json
from fastapi import HTTPException


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def offset(cursor, identity):
    if cursor is None:
        return 0
    try:
        raw = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            set(raw) != {"fingerprint", "offset"}
            or type(raw["offset"]) is not int
            or raw["offset"] < 0
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "Invalid cursor")
    if raw["fingerprint"] != identity:
        raise HTTPException(409, "Source or filters changed; restart pagination")
    return raw["offset"]


def cursor(identity, position):
    return base64.urlsafe_b64encode(
        json.dumps({"fingerprint": identity, "offset": position}).encode()
    ).decode()
