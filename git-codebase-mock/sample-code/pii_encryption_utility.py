"""
PII Encryption Utility
Hosted by git-codebase-mock for RAG ingestion.

Provides field-level encryption / decryption of personally identifiable
information (PII) using AES-256-GCM via the cryptography library.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── Key management ────────────────────────────────────────────────────────────
# In production these keys must come from a KMS / Vault, NOT environment vars.
_PII_KEY_B64: str = os.environ.get("PII_ENCRYPTION_KEY", "")


def _get_key() -> bytes:
    """Load the AES-256 key (32 bytes) from the environment."""
    if not _PII_KEY_B64:
        raise EnvironmentError(
            "PII_ENCRYPTION_KEY environment variable is not set. "
            "Generate a key with: python -c \"import os,base64; print(base64.b64encode(os.urandom(32)).decode())\""
        )
    key = base64.b64decode(_PII_KEY_B64)
    if len(key) != 32:
        raise ValueError("PII_ENCRYPTION_KEY must decode to exactly 32 bytes for AES-256.")
    return key


# ── Field-level operations ─────────────────────────────────────────────────────

def encrypt_field(plaintext: str) -> str:
    """
    Encrypt a single PII string field.

    Returns a base64-encoded payload: <12-byte nonce> + <ciphertext+tag>.
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), associated_data=None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt_field(encrypted_b64: str) -> str:
    """
    Decrypt a field previously encrypted with :func:`encrypt_field`.
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    raw = base64.b64decode(encrypted_b64)
    nonce, ciphertext = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ciphertext, associated_data=None).decode()


# ── Record-level operations ────────────────────────────────────────────────────

PII_FIELDS: List[str] = ["name", "email", "phone", "ssn", "date_of_birth", "address"]


def encrypt_pii_record(record: Dict[str, Any], fields: List[str] | None = None) -> Dict[str, Any]:
    """
    Encrypt specified PII fields in a dictionary record.

    Args:
        record: The original record dict.
        fields: Field names to encrypt. Defaults to :data:`PII_FIELDS`.

    Returns:
        A new dict with the specified fields replaced by their encrypted values.
    """
    target_fields = fields or PII_FIELDS
    encrypted = dict(record)
    for field in target_fields:
        if field in encrypted and isinstance(encrypted[field], str):
            encrypted[field] = encrypt_field(encrypted[field])
    return encrypted


def decrypt_pii_record(record: Dict[str, Any], fields: List[str] | None = None) -> Dict[str, Any]:
    """
    Decrypt specified PII fields in a dictionary record.

    Args:
        record: A record containing encrypted field values.
        fields: Field names to decrypt. Defaults to :data:`PII_FIELDS`.

    Returns:
        A new dict with the specified fields replaced by their plaintext values.
    """
    target_fields = fields or PII_FIELDS
    decrypted = dict(record)
    for field in target_fields:
        if field in decrypted and isinstance(decrypted[field], str):
            try:
                decrypted[field] = decrypt_field(decrypted[field])
            except Exception as exc:
                raise ValueError(f"Failed to decrypt field '{field}': {exc}") from exc
    return decrypted


# ── CLI helper ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    sample = {
        "user_id": "u-001",
        "name": "Jane Doe",
        "email": "jane.doe@example.com",
        "phone": "+1-555-867-5309",
        "ssn": "123-45-6789",
        "date_of_birth": "1990-04-15",
        "address": "123 Main St, Springfield, IL 62701",
        "account_balance": 4200.00,  # non-PII field, should not be encrypted
    }

    print("Original record:")
    print(json.dumps(sample, indent=2))

    enc = encrypt_pii_record(sample)
    print("\nEncrypted record:")
    print(json.dumps(enc, indent=2))

    dec = decrypt_pii_record(enc)
    print("\nDecrypted record:")
    print(json.dumps(dec, indent=2))

    assert dec["name"] == sample["name"], "Round-trip failed!"
    print("\n✓ Round-trip encryption/decryption successful.")
