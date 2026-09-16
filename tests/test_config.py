from core.config import AppConfig


def test_default_workspace_is_inside_project():
    config = AppConfig()
    assert config.workspace_path().exists()
