from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BackendUnavailableError,
    NotFoundError,
    VaultError,
)


def test_vault_error_to_dict():
    err = VaultError(code="test_error", message="Something broke", status=500)
    d = err.to_dict()
    assert d["error"]["code"] == "test_error"
    assert d["error"]["message"] == "Something broke"
    assert d["error"]["status"] == 500
    assert "details" not in d["error"]


def test_vault_error_with_details():
    err = VaultError(code="x", message="y", status=400, details={"hint": "try again"})
    d = err.to_dict()
    assert d["error"]["details"]["hint"] == "try again"


def test_authentication_error_defaults():
    err = AuthenticationError()
    assert err.status == 401
    assert err.code == "authentication_required"


def test_authorization_error_defaults():
    err = AuthorizationError()
    assert err.status == 403


def test_not_found_error_defaults():
    err = NotFoundError()
    assert err.status == 404


def test_backend_unavailable_error_defaults():
    err = BackendUnavailableError()
    assert err.status == 503
    assert "suggestion" in err.details
