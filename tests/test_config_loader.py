from pathlib import Path

from pydantic import BaseModel

from dfjsim_shared_tools.config_loader import ConfigLoader


class PathsConfig(BaseModel):
    installer: str | None = None


class AppConfig(BaseModel):
    logging_level: int = 3
    paths: PathsConfig = PathsConfig()


def test_config_loader_merges_default_and_user_config(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config_default.toml").write_text(
        'logging_level = 2\n\n[paths]\ninstaller = "default/path"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        'logging_level = 4\n\n[paths]\ninstaller = "override/path"\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    loader = ConfigLoader()
    config = loader.get_config(AppConfig)

    assert config.logging_level == 4
    assert config.paths.installer == "override/path"
