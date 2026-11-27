"""Web-Push-Benachrichtigungen (RFC 8291 aes128gcm + VAPID RFC 8292), nur mit `cryptography`.
Funktioniert auf Android (Chrome) und iPhone (ab iOS 16.4, App muss auf dem Home-Bildschirm liegen)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("push")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _raw_public(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


class Vapid:
    def __init__(self, data_dir: Path):
        path = data_dir / "vapid_private.pem"
        if path.exists():
            self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
            os.chmod(path, 0o600)
        self.public_b64 = b64u(_raw_public(self.key))

    def auth_header(self, endpoint: str, subject: str) -> str:
        u = urlparse(endpoint)
        header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                                  "sub": subject}).encode())
        signing_input = f"{header}.{claims}".encode()
        r, s = decode_dss_signature(self.key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
        token = f"{header}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        return f"vapid t={token}, k={self.public_b64}"


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """Verschlüsselt die Nachricht für ein Abo (Body im aes128gcm-Format)."""
    ua_public = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(server_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + ciphertext


async def send(vapid: Vapid, sub: dict[str, Any], data: dict[str, Any], subject: str) -> int:
    body = encrypt(json.dumps(data, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
    headers = {"Authorization": vapid.auth_header(sub["endpoint"], subject), "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream", "TTL": "86400", "Urgency": "high"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(sub["endpoint"], content=body, headers=headers)
    return r.status_code
