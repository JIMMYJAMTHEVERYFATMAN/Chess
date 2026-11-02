"""Create an RSA key pair for the encrypted telemetry event store."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    private_path = base / "OfflineChess" / "dashboard-private.pem"
    public_path = Path(__file__).with_name("dashboard-public.pem")
    if private_path.exists() or public_path.exists():
        raise SystemExit(
            "Dashboard key file already exists. Refusing to replace it; replacing only one key breaks decryption."
        )
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    private_created = False
    public_created = False
    try:
        with private_path.open("xb") as private_file:
            private_created = True
            private_file.write(private_pem)
        with public_path.open("xb") as public_file:
            public_created = True
            public_file.write(public_pem)
        try:
            private_path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        if private_created:
            private_path.unlink(missing_ok=True)
        if public_created:
            public_path.unlink(missing_ok=True)
        raise
    print(f"Private key saved outside the repository: {private_path}")
    print(f"Public key saved: {public_path}")
    print("Configure the public PEM as the collector's TELEMETRY_PUBLIC_KEY value.")


if __name__ == "__main__":
    main()
