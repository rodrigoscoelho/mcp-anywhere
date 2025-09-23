"""Pydantic models for form validation."""

import re

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class ServerFormData(BaseModel):
    """Form data for adding/editing MCP servers."""

    name: str = Field(..., min_length=2, max_length=100)
    github_url: str = Field(..., max_length=500)
    description: str | None = Field(None, max_length=500)
    runtime_type: str = Field(...)
    install_command: str | None = Field(None, max_length=500)
    start_command: str = Field(...)
    env_variables: list[dict] = Field(default_factory=list)

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v):
        """Validate GitHub URL format."""
        if v:
            pattern = r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$"
            if not re.match(pattern, v.rstrip("/")):
                raise ValueError("Please enter a valid GitHub repository URL")
        return v

    @field_validator("runtime_type")
    @classmethod
    def validate_runtime_type(cls, v):
        """Validate runtime type is one of allowed values."""
        allowed_types = ["uvx", "npx"]
        if v not in allowed_types:
            raise ValueError(f"Runtime type must be one of: {', '.join(allowed_types)}")
        return v

    @field_validator("install_command")
    @classmethod
    def validate_install_command(cls, v, info: ValidationInfo):
        """Ensure install command requirements match the selected runtime."""
        runtime_type = info.data.get("runtime_type") if info.data else None

        if v is not None:
            v = v.strip()
            if not v:
                v = None

        if runtime_type == "npx" and not v:
            raise ValueError(
                "Install command is required for npx servers. "
                "For npx: 'npm install -g @org/package'"
            )

        return v


class AnalyzeFormData(BaseModel):
    """Form data for analyzing a GitHub repository."""

    github_url: str = Field(..., max_length=500)

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v):
        """Validate GitHub URL format."""
        if v:
            pattern = r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$"
            if not re.match(pattern, v.rstrip("/")):
                raise ValueError("Please enter a valid GitHub repository URL")
        return v
