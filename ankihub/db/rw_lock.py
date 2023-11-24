"""This module defines a readers-writer lock for the AnkiHub DB.
Multiple threads can enter the non_exclusive_db_access_context() context, but when
a thread enters the exclusive_db_access_context() context, no other thread can enter
either context until the thread that entered the exclusive_db_access_context() context
exits it.
"""
from contextlib import contextmanager

from readerwriterlock import rwlock

from .. import LOGGER
from .exceptions import LockAcquisitionTimeoutError

LOCK_TIMEOUT_SECONDS = 5

rw_lock = rwlock.RWLockFair()
write_lock = rw_lock.gen_wlock()


@contextmanager
def exclusive_db_access_context():
    if write_lock.acquire(blocking=True, timeout=LOCK_TIMEOUT_SECONDS):
        LOGGER.debug("Acquired exclusive access.")
        try:
            yield
        finally:
            write_lock.release()
            LOGGER.debug("Released exclusive access.")
    else:
        raise LockAcquisitionTimeoutError("Could not acquire exclusive access.")


@contextmanager
def non_exclusive_db_access_context():
    lock = rw_lock.gen_rlock()
    if lock.acquire(blocking=True, timeout=LOCK_TIMEOUT_SECONDS):
        LOGGER.debug("Acquired non-exclusive access.")
        try:
            yield
        finally:
            lock.release()
            LOGGER.debug("Released non-exclusive access.")
    else:
        raise LockAcquisitionTimeoutError("Could not acquire non-exclusive access.")
