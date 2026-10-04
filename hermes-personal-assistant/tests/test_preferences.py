import pytest

from assistant.tools.preferences import normalize_phone, validate_preference


@pytest.mark.parametrize("raw, expected", [
    ("+1 555-123-4567", "+15551234567"),
    ("(555) 123-4567", "+15551234567"),
    ("15551234567", "+15551234567"),
    ("+44 20 7946 0958", "+442079460958"),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "hello", "user@icloud.com"])
def test_normalize_phone_rejects(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


@pytest.mark.parametrize("raw, expected", [("Driving", "drive"), ("bike", "bicycle"), ("transit", "transit")])
def test_travel_mode_aliases(raw, expected):
    assert validate_preference("travel_mode", raw) == expected


def test_travel_mode_rejects_unknown():
    with pytest.raises(ValueError, match="travel_mode"):
        validate_preference("travel_mode", "teleport")


def test_buffer_minutes():
    assert validate_preference("buffer_minutes", " 15 ") == 15
    for bad in ("-1", "121", "ten"):
        with pytest.raises(ValueError):
            validate_preference("buffer_minutes", bad)


@pytest.mark.parametrize("value", [20, 20.0, "20"])
def test_buffer_minutes_accepts_numbers_the_model_sends(value):
    assert validate_preference("buffer_minutes", value) == 20


@pytest.mark.parametrize("value", [20.5, True, None, "1.5"])
def test_buffer_minutes_rejects_non_whole_numbers(value):
    with pytest.raises(ValueError, match="whole number"):
        validate_preference("buffer_minutes", value)


def test_timezone():
    assert validate_preference("timezone", "America/Chicago") == "America/Chicago"
    with pytest.raises(ValueError, match="timezone"):
        validate_preference("timezone", "Mars/Base")


def test_address_collapses_whitespace_and_limits_length():
    assert validate_preference("home_address", "  1  Main St\n Springfield ") == "1 Main St Springfield"
    with pytest.raises(ValueError):
        validate_preference("home_address", "   ")
    with pytest.raises(ValueError):
        validate_preference("home_address", "x" * 301)


def test_unknown_key_rejected():
    with pytest.raises(ValueError, match="Unknown preference"):
        validate_preference("google_token", "x")
