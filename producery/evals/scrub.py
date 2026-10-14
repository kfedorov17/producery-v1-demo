"""Scrubbing for minted golden rows.

`scripts/mint_golden.py` copies a production trace's email into the repository. The sender's identity has no place
in git: the address keeps its domain (the heuristics route on it) and gets a deterministic local part, the display
name becomes a fixture name, and both are replaced wherever they appear in the subject and body. Phone numbers and
SSN shapes in the text are redacted the same way the trace masker does it.
"""

from __future__ import annotations

import hashlib
from typing import Any

from producery.observability.masking import mask_text


def scrub_email(email: dict[str, Any], *, salt: str = "") -> dict[str, Any]:
    """Return a copy of the email with the sender pseudonymised and PII masked in subject and body.

    The address keeps its domain and gets a deterministic `client-<digest>` local part; the display name
    becomes `Client <XXXX>`; both (and the name's first word) are replaced wherever they appear in the subject
    and body before `mask_text` runs.
    Empty `from_email` or `from_name` values are left empty. The input mapping is not modified.

    Args:
        email: The email mapping with `from_email`, `from_name`, `subject` and `body` keys.
        salt: Extra input to the pseudonym digest, so the same sender maps differently across datasets.
    """
    out = dict(email)
    addr = str(out.get("from_email") or "")
    name = str(out.get("from_name") or "")
    digest = hashlib.sha1(f"{salt}|{addr}|{name}".encode()).hexdigest()[:8]  # noqa: S324 — pseudonym, not security
    domain = addr.split("@", 1)[1] if "@" in addr else "example.net"
    new_addr = f"client-{digest}@{domain}"
    new_name = f"Client {digest[:4].upper()}"
    for field in ("subject", "body"):
        text = str(out.get(field) or "")
        if addr:
            text = text.replace(addr, new_addr)
        if name and len(name) >= 3:
            text = text.replace(name, new_name)
            first = name.split()[0]
            if len(first) >= 3:
                text = text.replace(first, new_name.split()[0])
        out[field] = mask_text(text).replace(f"***@{domain}", new_addr)
    out["from_email"] = new_addr if addr else addr
    out["from_name"] = new_name if name else name
    return out
