import pytest

from assistant.tools.google_auth import GoogleAuthError, credentials_from_token


@pytest.mark.parametrize("token", [None, ""])
def test_missing_token_reports_not_connected(token):
    with pytest.raises(GoogleAuthError, match="not connected"):
        credentials_from_token(token)
