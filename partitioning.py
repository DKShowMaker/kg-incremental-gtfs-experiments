"""Shared partition assignment for change generation and distributed scheduling."""
import hashlib


def partition_for_trip_id(trip_id, k):
    if k < 1:
        raise ValueError("k must be positive")
    digest = hashlib.md5(str(trip_id).encode("utf-8")).hexdigest()
    return int(digest, 16) % k
