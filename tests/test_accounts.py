"""Unit tests for accounts, sessions, and level clears."""

import os

import pytest


@pytest.fixture()
def accounts_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("AUTH_SECRET", "test-secret-for-accounts")
    import app.accounts as accounts

    accounts.reset_conn_for_tests()
    yield accounts
    accounts.reset_conn_for_tests()


def test_signup_login_and_token(accounts_db):
    user = accounts_db.create_user("Alice_Juror", "secret12")
    assert user["username"] == "Alice_Juror"

    with pytest.raises(ValueError, match="already taken"):
        accounts_db.create_user("alice_juror", "otherpass")

    assert accounts_db.authenticate("Alice_Juror", "wrong") is None
    ok = accounts_db.authenticate("alice_juror", "secret12")
    assert ok is not None
    assert ok["id"] == user["id"]

    token = accounts_db.issue_token(user["id"])
    assert accounts_db.verify_token(token) == user["id"]
    assert accounts_db.verify_token("bad.token.here") is None


def test_level_unlock_progression(accounts_db):
    user = accounts_db.create_user("Progress", "secret12")
    uid = user["id"]

    assert accounts_db.is_level_unlocked(uid, 1) is True
    assert accounts_db.is_level_unlocked(uid, 2) is False
    assert accounts_db.is_level_unlocked(uid, 3) is False

    newly = accounts_db.credit_level_clears([uid], 1, "manor_poison")
    assert newly == [uid]
    assert accounts_db.is_level_unlocked(uid, 2) is True
    assert accounts_db.is_level_unlocked(uid, 3) is False

    # Idempotent
    again = accounts_db.credit_level_clears([uid], 1, "manor_poison")
    assert again == []

    accounts_db.record_level_clear(uid, 2, "station_sabotage")
    assert accounts_db.is_level_unlocked(uid, 3) is True
    assert accounts_db.max_cleared_level(uid) == 2


def test_username_validation(accounts_db):
    with pytest.raises(ValueError):
        accounts_db.create_user("a", "secret12")
    with pytest.raises(ValueError):
        accounts_db.create_user("bad name!", "secret12")
    with pytest.raises(ValueError):
        accounts_db.create_user("ok_name", "short")
