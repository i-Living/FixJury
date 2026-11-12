from fixjury.config import Settings
from fixjury.models import Base, RejectionCode, RunStatus


def test_settings_reads_explicit_value() -> None:
    url = "postgresql+asyncpg://user:pass@127.0.0.1:55432/fixjury"
    assert Settings(database_url=url).database_url == url


def test_settings_use_env_prefix() -> None:
    assert Settings.model_config["env_prefix"] == "FIXJURY_"


def test_models_declare_six_tables() -> None:
    assert set(Base.metadata.tables) == {
        "tasks",
        "runs",
        "verdicts",
        "rejections",
        "review_batches",
        "metric_snapshots",
    }


def test_enum_values_match_codes() -> None:
    assert RunStatus.infra_failed.value == "infra_failed"
    assert RejectionCode.EXTRA_FILES.value == "EXTRA_FILES"
