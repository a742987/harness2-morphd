"""API-key storage: OS keychain first, encrypted local vault as a documented fallback.

Keys are never written to ``config.json``, ``providers.json``, logs, exports or browser
storage. Only an opaque ``key_ref`` and the last four characters are persisted.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path

from .. import store as engine_store
from .errors import ConsumerError

SERVICE = "morphd"
VAULT_VERSION = 1


def _keyring_module():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def _keyring_available() -> bool:
    keyring = _keyring_module()
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
        name = f"{type(backend).__module__}.{type(backend).__name__}".lower()
        return "fail" not in name and "null" not in name
    except Exception:
        return False


def mask(secret: str) -> str:
    secret = str(secret or "")
    if len(secret) <= 4:
        return "…" if secret else ""
    return "…" + secret[-4:]


class EncryptedVault:
    """AES-GCM vault unlocked with a user passphrase (scrypt-derived key)."""

    def __init__(self, path: Path, passphrase: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._passphrase = passphrase or os.environ.get("MORPHD_VAULT_PASSPHRASE") or ""
        self._cache: dict[str, str] | None = None

    def unlocked(self) -> bool:
        return bool(self._passphrase) or self._cache is not None

    def unlock(self, passphrase: str) -> None:
        if not passphrase:
            raise ConsumerError("VAULT_LOCKED", "保险库口令不能为空。", "设置一个本地口令。")
        self._passphrase = passphrase
        self._cache = self._read()
        self.store(force=True)

    def lock(self) -> None:
        self._passphrase = ""
        self._cache = None

    def _derive(self, salt: bytes) -> bytes:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
        return kdf.derive(self._passphrase.encode("utf-8"))

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        if not self._passphrase:
            raise ConsumerError(
                "VAULT_LOCKED",
                "本地加密保险库尚未解锁。",
                "输入你设置的保险库口令后重试；口令只在本机内存中使用。",
            )
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            salt = base64.b64decode(payload["salt"])
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["ciphertext"])
            key = self._derive(salt)
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, b"morphd-vault-v1")
            data = json.loads(plaintext.decode("utf-8"))
            return {str(k): str(v) for k, v in data.items()}
        except InvalidTag as exc:
            raise ConsumerError(
                "VAULT_ERROR", "保险库口令不正确。", "重新输入口令；忘记口令时需要重新录入 Key。"
            ) from exc
        except (ValueError, KeyError, OSError) as exc:
            raise ConsumerError(
                "VAULT_ERROR", f"无法读取本地保险库：{exc}", "重新录入 Key 或删除保险库后重建。"
            ) from exc

    @property
    def data(self) -> dict[str, str]:
        if self._cache is None:
            self._cache = self._read()
        return self._cache

    def store(self, *, force: bool = False) -> None:
        if self._cache is None and not force:
            return
        if not self._passphrase:
            raise ConsumerError(
                "VAULT_LOCKED", "需要保险库口令才能保存 Key。", "设置并输入保险库口令。"
            )
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        key = self._derive(salt)
        plaintext = json.dumps(self._cache or {}, sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, b"morphd-vault-v1")
        payload = {
            "version": VAULT_VERSION,
            "kdf": "scrypt",
            "salt": base64.b64encode(salt).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }
        engine_store.atomic_write_text(self.path, json.dumps(payload, indent=2))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, name: str) -> str | None:
        return self.data.get(name)

    def set(self, name: str, secret: str) -> None:
        self.data[name] = secret
        self.store()

    def delete(self, name: str) -> None:
        self.data.pop(name, None)
        self.store()


class KeyVault:
    """Facade selecting keychain or encrypted vault and issuing stable key refs."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.vault_path = self.home / "vault.enc"
        self.vault = EncryptedVault(self.vault_path)

    @staticmethod
    def ref_for(provider_id: str, backend: str) -> str:
        return f"{backend}:{SERVICE}:provider:{provider_id}"

    @staticmethod
    def parse_ref(ref: str | None) -> tuple[str, str] | None:
        if not ref:
            return None
        parts = str(ref).split(":", 3)
        if len(parts) != 4 or parts[0] not in ("keychain", "vault"):
            return None
        if parts[1] != SERVICE or parts[2] != "provider":
            return None
        return parts[0], parts[3]

    def backend_status(self) -> dict:
        return {
            "keychain": _keyring_available(),
            "vault": self.vault_path.exists(),
            "vault_unlocked": self.vault.unlocked(),
        }

    def unlock_vault(self, passphrase: str) -> None:
        self.vault.unlock(passphrase)

    def lock_vault(self) -> None:
        self.vault.lock()

    def set(self, provider_id: str, secret: str) -> tuple[str, str]:
        if not secret or not str(secret).strip():
            raise ConsumerError("INVALID_REQUEST", "API Key 不能为空。", "粘贴服务商提供的 Key。")
        secret = str(secret).strip()
        if _keyring_available():
            try:
                keyring = _keyring_module()
                keyring.set_password(SERVICE, f"provider:{provider_id}", secret)
                return self.ref_for(provider_id, "keychain"), mask(secret)
            except Exception:
                pass
        try:
            self.vault.set(provider_id, secret)
        except ConsumerError as exc:
            if exc.code == "VAULT_LOCKED":
                raise ConsumerError(
                    "VAULT_LOCKED",
                    "系统钥匙串不可用，需要先设置本地保险库口令。",
                    "在模型页设置保险库口令；之后 Key 会以加密形式保存在本机。",
                ) from exc
            raise
        return self.ref_for(provider_id, "vault"), mask(secret)

    def get(self, ref: str | None) -> str | None:
        parsed = self.parse_ref(ref)
        if not parsed:
            return None
        backend, provider_id = parsed
        if backend == "keychain":
            keyring = _keyring_module()
            if keyring is not None:
                try:
                    return keyring.get_password(SERVICE, f"provider:{provider_id}")
                except Exception:
                    pass
            return None
        return self.vault.get(provider_id)

    def delete(self, ref: str | None) -> None:
        parsed = self.parse_ref(ref)
        if not parsed:
            return
        backend, provider_id = parsed
        if backend == "keychain":
            keyring = _keyring_module()
            if keyring is not None:
                try:
                    keyring.delete_password(SERVICE, f"provider:{provider_id}")
                except Exception:
                    pass
        else:
            self.vault.delete(provider_id)

    def delete_all(self, refs: list[str]) -> None:
        for ref in refs:
            self.delete(ref)
