"""
PeculiarAI — Enterprise-grade AES-256-GCM Cryptographic Module
Provides authenticated symmetric encryption (AEAD) at rest and in transit preparation.
Complies with industry standards (AES-256-GCM with 96-bit random nonce and 128-bit authentication tag).
"""
import os
import base64
import hashlib
from typing import Any
from dotenv import load_dotenv
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.fernet import Fernet, InvalidToken

load_dotenv()

PREFIX = "aes256gcm$"
NONCE_LENGTH = 12  # 96 bits recommended for AES-GCM
TAG_LENGTH = 16    # 128 bits authentication tag


def generate_aes256_key() -> str:
    """Generate a new 256-bit key as a 64-character hexadecimal string."""
    return os.urandom(32).hex()


def get_aes256_key() -> bytes:
    """
    Retrieve and validate the 256-bit (32-byte) AES master encryption key.

    Strict Configuration & Safety Rules:
    1. If AES_SECRET_KEY is set:
       - Must be a 64-character hex string (32 bytes) or a valid 32-byte Base64 string.
       - Invalid formats raise ValueError immediately.
    2. If AES_SECRET_KEY is missing:
       - In production (ENVIRONMENT/APP_ENV in {'production', 'prod'}):
         Raises RuntimeError. Silent fallbacks or generated keys in production are strictly forbidden.
       - In development/local testing:
         Derives deterministically from GOOGLE_TOKEN_KEY using SHA-256 with domain separation.
         If GOOGLE_TOKEN_KEY is also missing, raises RuntimeError.
         Static hardcoded fallback seeds are strictly removed.
    """
    raw = os.getenv("AES_SECRET_KEY", "").strip()
    if raw:
        # Check 64-character hex format
        if len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw):
            try:
                key_bytes = bytes.fromhex(raw)
                if len(key_bytes) == 32:
                    return key_bytes
            except ValueError:
                pass

        # Check 32-byte Base64/urlsafe-base64 format
        try:
            b = base64.urlsafe_b64decode(raw)
            if len(b) == 32:
                return b
        except Exception:
            pass

        raise ValueError(
            f"Invalid AES_SECRET_KEY format: Expected a 64-character hex string (32 bytes) "
            f"or 32-byte base64 string, got length {len(raw)}. Generate one with crypto.generate_aes256_key()."
        )

    # If AES_SECRET_KEY is missing, check environment
    app_env = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")).lower()
    if app_env in {"production", "prod"}:
        raise RuntimeError(
            "Production security requirement: 'AES_SECRET_KEY' environment variable is missing. "
            "In production, a dedicated 64-character hex key (32 bytes) is required. "
            "Generate one with: python -c \"import os; print(os.urandom(32).hex())\""
        )

    # In development/test mode only: derive from GOOGLE_TOKEN_KEY if present
    dev_seed = os.getenv("GOOGLE_TOKEN_KEY", "").strip()
    if dev_seed:
        return hashlib.sha256(f"peculiarai-aes256-gcm-master:{dev_seed}".encode("utf-8")).digest()

    raise RuntimeError(
        "Configuration error: Neither 'AES_SECRET_KEY' nor 'GOOGLE_TOKEN_KEY' is configured. "
        "Set 'AES_SECRET_KEY' in your environment."
    )


class AES256GCMCipher:
    """
    Standard AES-256-GCM Authenticated Cipher.
    Each encryption uses a fresh, cryptographically secure 96-bit random nonce.
    """

    def __init__(self, key: bytes | None = None):
        self.key = key if key is not None else get_aes256_key()
        if len(self.key) != 32:
            raise ValueError(f"AES-256 requires exactly 32 bytes key, got {len(self.key)} bytes.")
        self._cipher = AESGCM(self.key)

    def encrypt(self, plaintext: str | bytes, associated_data: bytes | None = None) -> str:
        """
        Encrypt plaintext using AES-256-GCM.
        Returns a base64url string with prefix 'aes256gcm$'.
        """
        if not plaintext:
            raise ValueError("Cannot encrypt empty data.")

        if isinstance(plaintext, str):
            data_bytes = plaintext.encode("utf-8")
        else:
            data_bytes = plaintext

        nonce = os.urandom(NONCE_LENGTH)
        ciphertext = self._cipher.encrypt(nonce, data_bytes, associated_data)
        encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return f"{PREFIX}{encoded}"

    def decrypt(self, ciphertext_str: str, associated_data: bytes | None = None) -> str:
        """
        Decrypt and authenticate AES-256-GCM ciphertext.
        Raises ValueError if corrupted, tampered with, or invalid key.
        """
        if not ciphertext_str:
            raise ValueError("Cannot decrypt empty string.")

        raw_str = ciphertext_str
        if raw_str.startswith(PREFIX):
            raw_str = raw_str[len(PREFIX):]

        try:
            raw_bytes = base64.urlsafe_b64decode(raw_str.encode("ascii"))
        except Exception as exc:
            raise ValueError("Decryption failed: malformed base64 input.") from exc

        if len(raw_bytes) < (NONCE_LENGTH + TAG_LENGTH):
            raise ValueError("Decryption failed: payload is too short.")

        nonce = raw_bytes[:NONCE_LENGTH]
        ct = raw_bytes[NONCE_LENGTH:]

        try:
            decrypted_bytes = self._cipher.decrypt(nonce, ct, associated_data)
            return decrypted_bytes.decode("utf-8")
        except Exception as exc:
            raise ValueError("Decryption failed: invalid key, corrupted ciphertext, or tampered tag.") from exc

    def is_encrypted(self, val: Any) -> bool:
        """Check if value is an encrypted string."""
        if not isinstance(val, str):
            return False
        return val.startswith(PREFIX) or val.startswith("gAAAAAB")


_default_cipher: AES256GCMCipher | None = None


def get_default_cipher() -> AES256GCMCipher:
    """Retrieve or lazily initialize the default global cipher instance."""
    global _default_cipher
    if _default_cipher is None:
        _default_cipher = AES256GCMCipher()
    return _default_cipher


def reset_default_cipher() -> None:
    """Reset the default cipher instance (useful for test isolation when keys change)."""
    global _default_cipher
    _default_cipher = None


def encrypt_aes256_gcm(plaintext: str | bytes, associated_data: bytes | None = None) -> str:
    """Convenience helper for AES-256-GCM encryption."""
    return get_default_cipher().encrypt(plaintext, associated_data=associated_data)


def decrypt_aes256_gcm(ciphertext: str, associated_data: bytes | None = None) -> str:
    """Convenience helper for AES-256-GCM decryption."""
    return get_default_cipher().decrypt(ciphertext, associated_data=associated_data)


def is_encrypted(val: Any) -> bool:
    """Check if value is an encrypted string."""
    return get_default_cipher().is_encrypted(val)


def decrypt_any(data_str: str, fernet_key: str | None = None) -> str:
    """
    Multi-tier decryptor supporting:
    1. AES-256-GCM ('aes256gcm$...')
    2. Legacy Fernet tokens ('gAAAAAB...')
    3. Plaintext passthrough
    """
    if not isinstance(data_str, str) or not data_str:
        return ""

    if data_str.startswith(PREFIX):
        return get_default_cipher().decrypt(data_str)

    if data_str.startswith("gAAAAAB"):
        fkey = fernet_key or os.getenv("GOOGLE_TOKEN_KEY", "")
        if fkey:
            try:
                f = Fernet(fkey.encode("utf-8"))
                return f.decrypt(data_str.encode("utf-8")).decode("utf-8")
            except (InvalidToken, Exception):
                pass

    return data_str
