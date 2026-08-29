"""Pure domain contracts used by the application layer.

The modules in this package deliberately depend only on the Python standard
library.  Transport, Redis, QQ SDK, and the embedded legacy runtime belong at
the adapter boundary and must not leak into these contracts.
"""

from .contracts import (
    CommandError,
    CommandRequest,
    CommandResult,
    ComponentState,
    ComponentStatus,
    ErrorKind,
    MusicProviderPort,
    OopzRuntimePort,
    OperationResult,
    PlaybackPhase,
    PlaybackState,
    QueueItem,
    QueuePort,
    QueueSnapshot,
    SongCandidate,
)

__all__ = [
    "CommandError",
    "CommandRequest",
    "CommandResult",
    "ComponentState",
    "ComponentStatus",
    "ErrorKind",
    "MusicProviderPort",
    "OopzRuntimePort",
    "OperationResult",
    "PlaybackPhase",
    "PlaybackState",
    "QueueItem",
    "QueuePort",
    "QueueSnapshot",
    "SongCandidate",
]
