from contextlib import contextmanager

from readerwriterlock import rwlock

from .. import LOGGER
from .exceptions import LockAcquisitionTimeoutError

# Multiple threads can concurrently make read/write queries to the AnkiHub DB (read_lock), but they can't
# do that while the AnkiHub DB is attached to the Anki DB connection (write_lock).
rw_lock = rwlock.RWLockFair()
write_lock = rw_lock.gen_wlock()


@contextmanager
def write_lock_context():
    if write_lock.acquire(blocking=True, timeout=5):
        LOGGER.info("Acquired write lock.")
        try:
            yield
        finally:
            write_lock.release()
            LOGGER.info("Released write lock.")
    else:
        raise LockAcquisitionTimeoutError("Could not acquire write lock")


@contextmanager
def read_lock_context():
    lock = rw_lock.gen_rlock()
    if lock.acquire(blocking=True, timeout=5):
        LOGGER.info("Acquired read lock.")
        try:
            yield
        finally:
            lock.release()
            LOGGER.info("Released read lock.")
    else:
        raise LockAcquisitionTimeoutError("Could not acquire read lock")
