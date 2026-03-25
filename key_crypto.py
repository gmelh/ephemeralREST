################################################################################
#                                                                              #
#  Ephemeral.REST — Swiss Ephemeris REST API                                   #
#  Copyright (C) 2026  Ephemeral.REST contributors                             #
#                                                                              #
#  This program is free software: you can redistribute it and/or modify       #
#  it under the terms of the GNU Affero General Public License as published   #
#  by the Free Software Foundation, either version 3 of the License, or       #
#  (at your option) any later version.                                         #
#                                                                              #
#  This program is distributed in the hope that it will be useful,            #
#  but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the              #
#  GNU Affero General Public License for more details.                         #
#                                                                              #
#  You should have received a copy of the GNU Affero General Public License   #
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.    #
#                                                                              #
#  ADDITIONAL NOTICE — Swiss Ephemeris dependency:                             #
#  This software uses the Swiss Ephemeris library developed by                #
#  Astrodienst AG, Zurich, Switzerland. The Swiss Ephemeris is licensed       #
#  under the GNU Affero General Public License (AGPL) v3. Use of this        #
#  software therefore requires compliance with the AGPL v3, which includes    #
#  the obligation to make source code available to users who interact with    #
#  this software over a network.                                              #
#  See https://www.astro.com/swisseph/ for full details.                      #
#                                                                              #
################################################################################
################################################################################
# key_crypto.py                                                               #
################################################################################

"""
Symmetric encryption for API keys using Fernet (AES-128-CBC + HMAC-SHA256).

The SECRET_KEY from .env is used to derive a Fernet-compatible key via SHA-256.
Encrypted values are safe to store in the database — they cannot be decrypted
without the SECRET_KEY.

Usage:
    from key_crypto import KeyCrypto
    crypto = KeyCrypto(secret_key)

    encrypted = crypto.encrypt("my-api-key")
    plaintext = crypto.decrypt(encrypted)
    prefix    = crypto.prefix("my-api-key")   # first 8 chars for fast lookup
"""

import base64
import hashlib
import secrets
import string
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class KeyCrypto:
    """Handles API key encryption and decryption using a shared secret."""

    # Length of the key prefix stored in plaintext for fast lookup
    PREFIX_LENGTH = 8

    # Generated key character set — URL-safe, no ambiguous chars
    KEY_CHARS = string.ascii_letters + string.digits + "-_"
    KEY_LENGTH = 43  # produces a readable ~43-char key

    def __init__(self, secret_key: str):
        """
        Initialise the crypto engine.

        Args:
            secret_key: The SECRET_KEY value from .env
        """
        if not secret_key:
            raise ValueError("SECRET_KEY is required for key encryption")

        # Derive a 32-byte Fernet key from the secret
        digest = hashlib.sha256(secret_key.encode('utf-8')).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext API key.

        Args:
            plaintext: The raw API key string

        Returns:
            Fernet-encrypted ciphertext (URL-safe base64 string)
        """
        return self._fernet.encrypt(plaintext.encode('utf-8')).decode('utf-8')

    def decrypt(self, ciphertext: str) -> Optional[str]:
        """
        Decrypt an encrypted API key.

        Args:
            ciphertext: The stored Fernet ciphertext

        Returns:
            Decrypted plaintext key, or None if decryption fails
        """
        try:
            return self._fernet.decrypt(ciphertext.encode('utf-8')).decode('utf-8')
        except InvalidToken:
            return None
        except Exception:
            return None

    def verify(self, plaintext: str, ciphertext: str) -> bool:
        """
        Check whether a plaintext key matches a stored ciphertext.

        Args:
            plaintext:  The key provided in the X-API-Key header
            ciphertext: The stored encrypted value

        Returns:
            True if they match, False otherwise
        """
        decrypted = self.decrypt(ciphertext)
        if decrypted is None:
            return False
        # Constant-time comparison to prevent timing attacks
        return secrets.compare_digest(plaintext, decrypted)

    def prefix(self, plaintext: str) -> str:
        """
        Return the key prefix used for fast database lookup.
        Stored in plaintext — not sensitive, just used to narrow candidates.

        Args:
            plaintext: The raw API key string

        Returns:
            First PREFIX_LENGTH characters of the key
        """
        return plaintext[:self.PREFIX_LENGTH]

    @staticmethod
    def generate_key() -> str:
        """
        Generate a new random API key.

        Returns a cryptographically secure random string in the format:
            t_Cu2m-_Cej96t9TG4JqBpXKU0_6Yy3jGYK-St1k9oY
        (43 characters, URL-safe)
        """
        chars = string.ascii_letters + string.digits + "-_"
        return ''.join(secrets.choice(chars) for _ in range(KeyCrypto.KEY_LENGTH))