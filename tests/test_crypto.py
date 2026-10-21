"""
Unit & Security tests for AES-256-GCM cryptographic module (crypto.py).
Verifies AEAD encryption, decryption, authentication tag verification,
tamper detection, key generation, strict key validation, and real SQLite persistence.
"""
import os
import pytest
from unittest.mock import patch
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
import crypto
import google_auth


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


def test_strict_key_validation_invalid_hex():
    """Invalid hex or incorrect key length must raise ValueError."""
    with patch.dict(os.environ, {"AES_SECRET_KEY": "invalid_short_key"}, clear=False):
        crypto.reset_default_cipher()
        with pytest.raises(ValueError, match="Invalid AES_SECRET_KEY format"):
            crypto.get_aes256_key()
    crypto.reset_default_cipher()


def test_missing_key_in_production_raises_runtime_error():
    """In production mode, missing AES_SECRET_KEY must raise RuntimeError (no silent fallbacks)."""
    env_overrides = {
        "ENVIRONMENT": "production",
        "APP_ENV": "production",
        "AES_SECRET_KEY": "",
    }
    with patch.dict(os.environ, env_overrides, clear=False):
        crypto.reset_default_cipher()
        with pytest.raises(RuntimeError, match="Production security requirement: 'AES_SECRET_KEY' environment variable is missing"):
            crypto.get_aes256_key()
    crypto.reset_default_cipher()


def test_missing_all_keys_in_dev_raises_runtime_error():
    """If neither AES_SECRET_KEY nor GOOGLE_TOKEN_KEY is set, must raise RuntimeError."""
    env_overrides = {
        "ENVIRONMENT": "development",
        "APP_ENV": "development",
        "AES_SECRET_KEY": "",
        "GOOGLE_TOKEN_KEY": "",
    }
    with patch.dict(os.environ, env_overrides, clear=False):
        crypto.reset_default_cipher()
        with pytest.raises(RuntimeError, match="Neither 'AES_SECRET_KEY' nor 'GOOGLE_TOKEN_KEY' is configured"):
            crypto.get_aes256_key()
    crypto.reset_default_cipher()


def test_wrong_key_fails_decryption():
    """Decryption with a different key must fail authentication, preventing accidental data corruption."""
    key1 = crypto.generate_aes256_key()
    key2 = crypto.generate_aes256_key()

    cipher1 = crypto.AES256GCMCipher(bytes.fromhex(key1))
    cipher2 = crypto.AES256GCMCipher(bytes.fromhex(key2))

    encrypted = cipher1.encrypt("Classified OAuth Secret")

    with pytest.raises(ValueError, match="Decryption failed"):
        cipher2.decrypt(encrypted)


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


def test_real_sqlite_persistence_roundtrip():
    """Real SQLite database read/write roundtrip verifying token persistence and recovery."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE google_tokens (
                user_id INTEGER PRIMARY KEY,
                email TEXT,
                refresh_token_enc TEXT,
                scopes TEXT
            )
        """))

        raw_token = "1//04AbCdEfGhIjKlMnOpQrStUvWxYz_RealIntegrationToken999"
        enc = google_auth.encrypt_token(raw_token)
        assert enc.startswith("aes256gcm$")

        conn.execute(
            text("INSERT INTO google_tokens (user_id, email, refresh_token_enc, scopes) VALUES (1, 'u@gmail.com', :enc, 'calendar')"),
            {"enc": enc}
        )

    with engine.connect() as conn:
        row = conn.execute(text("SELECT refresh_token_enc FROM google_tokens WHERE user_id = 1")).first()
        assert row is not None
        stored_enc = row[0]
        decrypted = google_auth.decrypt_token(stored_enc)
        assert decrypted == raw_token
