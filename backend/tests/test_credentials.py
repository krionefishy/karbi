from cryptography.fernet import Fernet

from backend.shared.security import CredentialCipher


def test_credential_cipher_can_rotate_encrypted_values() -> None:
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    old_cipher = CredentialCipher((old_key,), "fingerprint-key")
    rotating_cipher = CredentialCipher((new_key, old_key), "fingerprint-key")

    old_token = old_cipher.encrypt("wb-api-key")
    rotated_token = rotating_cipher.rotate(old_token)

    assert rotating_cipher.decrypt(rotated_token) == "wb-api-key"
    assert rotating_cipher.fingerprint("wb-api-key") != "wb-api-key"
