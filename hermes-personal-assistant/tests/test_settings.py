import json

from assistant.config.settings import load_photon_credentials


def test_photon_credentials_prefer_hermes_env(tmp_path):
    (tmp_path / ".env").write_text("PHOTON_PROJECT_ID=env-id\nPHOTON_PROJECT_SECRET=env-secret\n")
    (tmp_path / "auth.json").write_text(json.dumps(
        {"credential_pool": {"photon_project": [{"spectrum_project_id": "auth-id", "project_secret": "auth-secret"}]}}))
    assert load_photon_credentials(tmp_path) == ("env-id", "env-secret")


def test_photon_credentials_fall_back_to_auth_json(tmp_path):
    (tmp_path / "auth.json").write_text(json.dumps(
        {"credential_pool": {"photon_project": [{"spectrum_project_id": "auth-id", "project_secret": "auth-secret"}]}}))
    assert load_photon_credentials(tmp_path) == ("auth-id", "auth-secret")


def test_photon_credentials_missing(tmp_path):
    assert load_photon_credentials(tmp_path) == (None, None)
