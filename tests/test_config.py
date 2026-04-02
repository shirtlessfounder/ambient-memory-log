import pytest

from ambient_memory.config import Settings


def test_settings_require_database_and_bucket():
    with pytest.raises(Exception):
        Settings.model_validate({})
