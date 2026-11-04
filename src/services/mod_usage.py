"""Process-wide record of mods that a running operation is reading or patching."""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager


class ModInUseError(RuntimeError):
    """A mod cannot be deleted while an operation holds it."""


_changed = threading.Condition()
_holds: Counter[str] = Counter()
_deleting: set[str] = set()


@contextmanager
def hold_mods(mod_ids: Iterable[str | None]) -> Iterator[None]:
    """Mark the mods as busy until the block ends; nested and parallel holds add up.

    Waits first if one of the mods is being deleted at this very moment, so a deletion and a
    new operation never overlap (the operation then fails on its own with a missing file).
    """
    ids = {mod_id for mod_id in mod_ids if mod_id}
    with _changed:
        _changed.wait_for(lambda: not (_deleting & ids))
        _holds.update(ids)
    try:
        yield
    finally:
        with _changed:
            _holds.subtract(ids)
            for mod_id in ids:
                if _holds[mod_id] <= 0:
                    del _holds[mod_id]
            _changed.notify_all()


@contextmanager
def reserve_for_deletion(mod_ids: Iterable[str | None]) -> Iterator[None]:
    """Claim the mods for deletion; raises ModInUseError when an operation holds one of them."""
    ids = {mod_id for mod_id in mod_ids if mod_id}
    with _changed:
        if any(_holds[mod_id] > 0 for mod_id in ids) or _deleting & ids:
            raise ModInUseError(", ".join(sorted(ids)))
        _deleting.update(ids)
    try:
        yield
    finally:
        with _changed:
            _deleting.difference_update(ids)
            _changed.notify_all()


def is_mod_busy(mod_id: str | None) -> bool:
    with _changed:
        return bool(mod_id) and (_holds[mod_id] > 0 or mod_id in _deleting)
