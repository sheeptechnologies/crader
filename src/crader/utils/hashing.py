import hashlib


def compute_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
