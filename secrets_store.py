"""Encrypted, local-only secrets store for JARVIS connector tokens and API keys.

Secrets are encrypted with Fernet (AES-128-CBC + HMAC). The key lives in
data/secrets.key on the local machine — never shipped, never sent to the
language model. Values are only decrypted in memory at call time.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


class SecretsStore:
    def __init__(self, vault_path: Path | None = None, key_path: Path | None = None) -> None:
        self.vault_path = vault_path or Path(__file__).resolve().parent / "data" / "secrets.enc"
        self.key_path = key_path or Path(__file__).resolve().parent / "data" / "secrets.key"
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self.key_path.is_file():
            key = self.key_path.read_bytes().strip()
            if Fernet(key).decrypt(Fernet(key).encrypt(b"ping")):
                return key
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        try:
            self.key_path.chmod(0o600)
        except Exception:
            pass
        return key

    def _read_vault(self) -> dict[str, Any]:
        if not self.vault_path.is_file():
            return {}
        try:
            raw = self._fernet.decrypt(self.vault_path.read_bytes())
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_vault(self, data: dict[str, Any]) -> None:
        token = self._fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self.vault_path.write_bytes(token)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            data = self._read_vault()
            data[str(key).strip()] = str(value)
            self._write_vault(data)

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._read_vault().get(str(key).strip())

    def delete(self, key: str) -> bool:
        with self._lock:
            data = self._read_vault()
            if str(key).strip() in data:
                del data[str(key).strip()]
                self._write_vault(data)
                return True
            return False

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._read_vault().keys())


_secrets: Optional[SecretsStore] = None
_secrets_lock = threading.Lock()


def get_secrets() -> SecretsStore:
    global _secrets
    with _secrets_lock:
        if _secrets is None:
            _secrets = SecretsStore()
        return _secrets


if __name__ == "__main__":
    store = get_secrets()
    print("Secrets vault:", store.vault_path)
    print("Stored keys:", store.keys())