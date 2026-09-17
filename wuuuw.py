"""
Generate a fresh VAPID keypair for Web Push.

Run once, offline, before enabling push:

    python scripts/generate_vapid_keys.py

Copy the two output lines into your .env. Never commit the private key.
"""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def main() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())

    pub_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    priv_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    print()
    print("=" * 64)
    print("  Add these three lines to your .env file")
    print("=" * 64)
    print()
    print(f"VAPID_PUBLIC_KEY={_b64url(pub_raw)}")
    print(f"VAPID_PRIVATE_KEY={_b64url(priv_der)}")
    print("VAPID_SUBJECT=mailto:admin@yourdomain.com")
    print()
    print("The private key is a credential. Keep it secret.")
    print("Never commit it to version control.")
    print()


if __name__ == '__main__':
    main()