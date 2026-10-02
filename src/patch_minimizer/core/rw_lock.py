# -*- coding: utf-8 -*-
""" rwlock.py
    A class to implement read-write locks on top of the standard threading
    library.
    This is implemented with two mutexes (threading.Lock instances) as per this
    wikipedia pseudocode:
    https://en.wikipedia.org/wiki/Readers%E2%80%93writer_lock#Using_two_mutexes
    Code copied from Tyler Neylon at Unbox Research.
    This file is public domain.

    Copied from https://gist.github.com/tylerneylon/a7ff6017b7a1f9a506cf75aa23eacfd6.
"""


# _______________________________________________________________________
# Imports

from contextlib import contextmanager
from threading  import Lock
import threading
import os
import logging
from typing import Optional
from logging import Logger


# _______________________________________________________________________
# Class

class RWLock(object):
    """ RWLock class; this is meant to allow an object to be read from by
        multiple threads, but only written to by a single thread at a time. See:
        https://en.wikipedia.org/wiki/Readers%E2%80%93writer_lock
        Usage:
            from rwlock import RWLock
            my_obj_rwlock = RWLock()
            # When reading from my_obj:
            with my_obj_rwlock.r_locked():
                do_read_only_things_with(my_obj)
            # When writing to my_obj:
            with my_obj_rwlock.w_locked():
                mutate(my_obj)
    """

    def __init__(self):

        self.w_lock = Lock()
        self.num_r_lock = Lock()
        self.num_r = 0
        self._w_lock_owner = None  # Thread ID that holds write lock

    # ___________________________________________________________________
    # Reading methods.

    def r_acquire(self):
        self.num_r_lock.acquire()
        self.num_r += 1
        if self.num_r == 1:
            self.w_lock.acquire()
        self.num_r_lock.release()

    def r_release(self):
        assert self.num_r > 0
        self.num_r_lock.acquire()
        self.num_r -= 1
        if self.num_r == 0:
            self.w_lock.release()
        self.num_r_lock.release()

    @contextmanager
    def r_locked(self):
        """ This method is designed to be used via the `with` statement. """
        try:
            self.r_acquire()
            yield
        finally:
            self.r_release()

    # ___________________________________________________________________
    # Writing methods.

    def w_acquire(self):
        self.w_lock.acquire()
        self._w_lock_owner = threading.current_thread().ident

    def w_acquire_nowait(self):
        """Non-blocking acquire - raises AssertionError if lock unavailable.

        AIDEV-NOTE: Use this only in minimization module for fail-fast behavior.
        """
        current_thread = threading.current_thread().ident

        # Check for same-thread re-entrance (would deadlock)
        if self._w_lock_owner == current_thread:
            raise AssertionError(
                f"Deadlock detected: Thread {current_thread} attempting to re-acquire "
                f"write lock it already holds"
            )

        # Non-blocking acquire - fail immediately if lock held by another thread
        acquired = self.w_lock.acquire(blocking=False)
        if not acquired:
            raise AssertionError(
                f"Lock contention: Thread {current_thread} cannot acquire write lock "
                f"(held by thread {self._w_lock_owner})"
            )

        self._w_lock_owner = current_thread

    def w_release(self):
        self._w_lock_owner = None
        self.w_lock.release()

    def is_w_locked(self) -> bool:
        """Check if write lock is currently held.

        AIDEV-NOTE: Use this to assert lock is held in functions that require
        the caller to hold the lock (to avoid deadlock with non-reentrant locks).

        Returns:
            True if write lock is held, False otherwise.
        """
        return self.w_lock.locked()

    @contextmanager
    def w_locked(self):
        """ This method is designed to be used via the `with` statement. """
        try:
            self.w_acquire()
            yield
        finally:
            self.w_release()

    @contextmanager
    def w_locked_nowait(self):
        """Non-blocking write lock context manager - raises if lock unavailable.

        AIDEV-NOTE: Use this only in minimization module for fail-fast behavior.
        """
        try:
            self.w_acquire_nowait()
            yield
        finally:
            self.w_release()


# _______________________________________________________________________
# GitRWLock - Git-aware RWLock subclass

class GitRWLock(RWLock):
    """RWLock that cleans up .git/index.lock on lock release.

    Use this for code_dir_lock when guarding git repository operations.
    Automatically removes stale index.lock files in the finally block
    of both r_locked() and w_locked() to prevent race conditions in parallel execution.

    AIDEV-NOTE: This class addresses race conditions where stale .git/index.lock
    files can block git operations during parallel instrumentation runs. The
    cleanup happens in the finally block to ensure locks are removed even if
    the guarded operation raises an exception.
    """

    def __init__(self, git_dir: str, logger: Optional[Logger] = None):
        """
        Initialize GitRWLock with git directory path.

        Args:
            git_dir: Path to the .git directory (e.g., /path/to/repo/.git)
            logger: Optional logger instance for cleanup messages
        """
        super().__init__()
        self.git_dir = git_dir
        self.logger = logger or logging.getLogger(__name__)

    def _clean_index_lock(self):
        """Remove .git/index.lock if it exists."""
        lock_file = os.path.join(self.git_dir, "index.lock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                self.logger.warning(f"Cleaned stale git lock: {lock_file}")
            except OSError as e:
                self.logger.error(f"Failed to remove git lock {lock_file}: {e}")

    @contextmanager
    def r_locked(self):
        """Read lock with automatic git index.lock cleanup.

        This method is designed to be used via the `with` statement.
        Cleans up any stale .git/index.lock file before releasing the lock.
        """
        try:
            self.r_acquire()
            yield
        finally:
            self._clean_index_lock()  # Clean before releasing lock
            self.r_release()

    @contextmanager
    def w_locked(self):
        """Write lock with automatic git index.lock cleanup.

        This method is designed to be used via the `with` statement.
        Cleans up any stale .git/index.lock file before releasing the lock.
        """
        try:
            self.w_acquire()
            yield
        finally:
            self._clean_index_lock()  # Clean before releasing lock
            self.w_release()

    @contextmanager
    def w_locked_nowait(self):
        """Non-blocking write lock with automatic git index.lock cleanup.

        AIDEV-NOTE: Use this only in minimization module for fail-fast behavior.
        """
        try:
            self.w_acquire_nowait()
            yield
        finally:
            self._clean_index_lock()  # Clean before releasing lock
            self.w_release()