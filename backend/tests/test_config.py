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
