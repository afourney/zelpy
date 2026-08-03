"""Authentication credentials for the Zello Channel API."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass(frozen=True)
class ZelloCredentials:
    """Credentials required to authenticate with the Zello Channel API.

    Instances can be created directly or populated from any string-keyed
    mapping. Loading a particular storage format belongs to the application
    layer; the bundled CLI currently supports YAML.
    """

    developer_token: str
    username: str
    password: str
    issuer: str
    public_key: str
    private_key: str

    def __post_init__(self) -> None:
        missing = [
            field.name
            for field in fields(self)
            if not isinstance(getattr(self, field.name), str)
            or not getattr(self, field.name).strip()
        ]
        if missing:
            raise ValueError(f"Missing required credential field(s): {', '.join(missing)}")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> ZelloCredentials:
        """Create credentials from a mapping supplied by any configuration source."""
        names = [field.name for field in fields(cls)]
        missing = [
            name
            for name in names
            if not isinstance(values.get(name), str) or not values[name].strip()
        ]
        if missing:
            raise ValueError(f"Missing required credential field(s): {', '.join(missing)}")
        return cls(**{name: values[name] for name in names})  # type: ignore[arg-type]

    def auth_token(self) -> str:
        """Create a short-lived token from the configured issuer and private key."""
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        payload = _b64url(
            json.dumps(
                {"iss": self.issuer, "exp": int(time.time()) + 120},
                separators=(",", ":"),
            ).encode()
        )
        unsigned = f"{header}.{payload}".encode()
        key = serialization.load_pem_private_key(self.private_key.encode(), password=None)
        signature = key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
        return f"{unsigned.decode()}.{_b64url(signature)}"
