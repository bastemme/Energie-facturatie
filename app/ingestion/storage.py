"""Encrypted document storage. Files are never stored or served in plaintext from disk."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings, get_settings


class StorageError(Exception):
    pass


class EncryptedFileStore:
    def __init__(self, root: Path, key: bytes):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(key)

    def _path(self, storage_key: str) -> Path:
        if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
            raise StorageError("invalid storage key")
        return self.root / storage_key[:2] / storage_key

    def put(self, content: bytes) -> str:
        storage_key = secrets.token_hex(24)
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._fernet.encrypt(content))
        return storage_key

    def get(self, storage_key: str) -> bytes:
        path = self._path(storage_key)
        if not path.exists():
            raise StorageError("document not found in storage")
        try:
            return self._fernet.decrypt(path.read_bytes())
        except InvalidToken as exc:
            raise StorageError("document could not be decrypted (wrong key or tampered)") from exc

    def delete(self, storage_key: str) -> None:
        path = self._path(storage_key)
        if path.exists():
            path.unlink()


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _resolve_key(settings: Settings) -> bytes:
    if settings.storage_encryption_key:
        return settings.storage_encryption_key.encode()
    if settings.is_production:
        raise StorageError("ER_STORAGE_ENCRYPTION_KEY is required in production")
    # Development convenience: a persistent local key (never used in production).
    key_file = settings.data_dir / ".dev_storage_key"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not key_file.exists():
        key_file.write_bytes(Fernet.generate_key())
        key_file.chmod(0o600)
    return key_file.read_bytes().strip()


def get_store() -> EncryptedFileStore:
    settings = get_settings()
    return EncryptedFileStore(settings.data_dir / "documents", _resolve_key(settings))
