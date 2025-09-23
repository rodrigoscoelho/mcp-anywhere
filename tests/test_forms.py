"""Tests for form validation models."""

import pytest
from pydantic import ValidationError

from mcp_anywhere.web.forms import ServerFormData


def _base_form_data(**overrides):
    """Helper to build base server form data with overrides."""

    data = {
        "name": "Demo Server",
        "github_url": "https://github.com/example/demo",
        "runtime_type": "uvx",
        "install_command": None,
        "start_command": "uvx demo start",
        "env_variables": [],
    }
    data.update(overrides)
    return data


def test_uvx_install_command_is_optional():
    """UVX servers should not require an install command."""

    form = ServerFormData(**_base_form_data(install_command="   "))
    assert form.install_command is None


def test_npx_requires_install_command():
    """NPX servers must provide an install command."""

    with pytest.raises(ValidationError) as exc:
        ServerFormData(**_base_form_data(runtime_type="npx"))

    assert "Install command is required for npx servers." in str(exc.value)
