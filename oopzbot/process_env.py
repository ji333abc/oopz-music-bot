"""Build least-privilege environments for managed child processes."""

from __future__ import annotations

import os
from collections.abc import Iterable


_RUNTIME_ENVIRONMENT = {
    "COMSPEC",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "USER",
    "USERPROFILE",
    "WINDIR",
}


def minimal_child_environment(passthrough: Iterable[str] = ()) -> dict[str, str]:
    allowed = _RUNTIME_ENVIRONMENT | {name.upper() for name in passthrough}
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed
    }
