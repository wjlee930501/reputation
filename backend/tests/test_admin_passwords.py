import pytest

from app.services.admin_passwords import hash_admin_password, verify_admin_password


def test_admin_password_hash_verifies_only_original_password():
    stored_hash = hash_admin_password("correct horse battery staple")

    assert stored_hash.startswith("pbkdf2_sha256$")
    assert verify_admin_password("correct horse battery staple", stored_hash)
    assert not verify_admin_password("correct horse battery wrong", stored_hash)


def test_admin_password_rejects_short_passwords():
    with pytest.raises(ValueError, match="at least 14"):
        hash_admin_password("too-short")


def test_admin_password_verification_rejects_malformed_hashes():
    assert not verify_admin_password("anything", "")
    assert not verify_admin_password("anything", "argon2$1$salt$digest")
    assert not verify_admin_password("anything", "pbkdf2_sha256$not-int$salt$digest")
