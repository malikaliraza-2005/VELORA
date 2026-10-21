import pytest

from velora.config import Settings


def test_defaults_when_unset() -> None:
    settings = Settings.from_env({})
    assert settings == Settings(env="development", log_level="INFO")


def test_values_are_normalised() -> None:
    settings = Settings.from_env({"VELORA_ENV": " Production ", "VELORA_LOG_LEVEL": "debug"})
    assert settings == Settings(env="production", log_level="DEBUG")


@pytest.mark.parametrize(
    "environ",
    [{"VELORA_ENV": "prod"}, {"VELORA_LOG_LEVEL": "VERBOSE"}],
)
def test_invalid_values_are_rejected(environ: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings.from_env(environ)


def test_database_url_placeholder_counts_as_unset() -> None:
    assert Settings.from_env({"VELORA_DATABASE_URL": "CHANGE_ME"}).database_url is None
    assert Settings.from_env({"VELORA_DATABASE_URL": "postgresql://x"}).database_url == (
        "postgresql://x"
    )


def test_magic_links_point_at_the_dashboard_and_keep_the_token_in_the_fragment() -> None:
    settings = Settings.from_env({"VELORA_PUBLIC_BASE_URL": "https://dash.example.com/"})
    assert settings.public_origin == "https://dash.example.com"
    assert settings.magic_link_url("abc.def") == "https://dash.example.com/login#abc.def"


@pytest.mark.parametrize(
    "environ",
    [
        {"VELORA_PUBLIC_BASE_URL": "dash.example.com"},
        {"VELORA_PUBLIC_BASE_URL": "https://dash.example.com/app"},
        {"VELORA_ENV": "production", "VELORA_PUBLIC_BASE_URL": "http://dash.example.com"},
    ],
)
def test_bad_public_base_urls_are_rejected(environ: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings.from_env(environ)
