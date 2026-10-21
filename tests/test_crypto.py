"""
Unit & Security tests for AES-256-GCM cryptographic module (crypto.py).
Verifies AEAD encryption, decryption, authentication tag verification,
tamper detection, key generation, and backward compatibility.
"""
import pytest
from cryptography.fernet import Fernet
import crypto


def test_aes256_gcm_encrypt_decrypt_roundtrip():
    """Verify standard text and unicode can be encrypted and decrypted accurately."""
    plaintext = "Sensitive personal note: payment of ₹12,500 due on 25 Oct!"
    encrypted = crypto.encrypt_aes256_gcm(plaintext)

    assert encrypted.startswith("aes256gcm$")
    assert encrypted != plaintext
    assert crypto.is_encrypted(encrypted) is True

    decrypted = crypto.decrypt_aes256_gcm(encrypted)
    assert decrypted == plaintext


def test_aes256_gcm_tamper_detection():
    """Verify that any modification to the ciphertext triggers authentication failure (GCM tag check)."""
    plaintext = "Top secret authorization token"
    encrypted = crypto.encrypt_aes256_gcm(plaintext)

    # Flip characters in the middle of ciphertext
    prefix, b64_body = encrypted.split("$", 1)
    tampered_body = b64_body[:-6] + ("A" if b64_body[-6] != "A" else "B") + b64_body[-5:]
    tampered = f"{prefix}${tampered_body}"

    with pytest.raises(ValueError, match="Decryption failed"):
        crypto.decrypt_aes256_gcm(tampered)


def test_aes256_gcm_empty_input_validation():
    """Empty data must raise a clean ValueError."""
    with pytest.raises(ValueError, match="Cannot encrypt empty data"):
        crypto.encrypt_aes256_gcm("")

    with pytest.raises(ValueError, match="Cannot decrypt empty string"):
        crypto.decrypt_aes256_gcm("")


def test_aes256_gcm_key_generation():
    """generate_aes256_key must output a valid 64-hex-character string (256 bits)."""
    key_hex = crypto.generate_aes256_key()
    assert isinstance(key_hex, str)
    assert len(key_hex) == 64
    bytes_key = bytes.fromhex(key_hex)
    assert len(bytes_key) == 32


def test_decrypt_any_fallback_modes():
    """
    decrypt_any must seamlessly decrypt:
    1. AES-256-GCM ciphertext
    2. Legacy Fernet tokens
    3. Return plaintext as-is
    """
    secret = "User-Data-Record-9988"

    # 1. AES-256-GCM
    aes_enc = crypto.encrypt_aes256_gcm(secret)
    assert crypto.decrypt_any(aes_enc) == secret

    # 2. Legacy Fernet
    fernet_key = Fernet.generate_key().decode()
    fernet = Fernet(fernet_key.encode())
    fernet_enc = fernet.encrypt(secret.encode()).decode()
    assert crypto.decrypt_any(fernet_enc, fernet_key=fernet_key) == secret

    # 3. Plaintext
    assert crypto.decrypt_any("ordinary_plaintext_unencrypted") == "ordinary_plaintext_unencrypted"
    assert crypto.is_encrypted("ordinary_plaintext_unencrypted") is False
