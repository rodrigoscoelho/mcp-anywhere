"""Forms for MCP Router web interface"""

import re
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, TextAreaField, BooleanField
from wtforms.validators import DataRequired, URL, Length, ValidationError


# Custom validators that can be reused across forms
def validate_github_url(form, field):
    """Validate GitHub URL format.

    Args:
        form: The form instance
        field: The GitHub URL field to validate

    Raises:
        ValidationError: If URL is not a valid GitHub repository URL
    """
    if field.data:
        pattern = r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$"
        if not re.match(pattern, field.data.rstrip("/")):
            raise ValidationError("Please enter a valid GitHub repository URL")


class EnvironmentVariableForm(FlaskForm):
    """Subform for environment variable configuration"""

    key = StringField("Key", validators=[DataRequired(), Length(max=100)])
    value = StringField("Value", validators=[Length(max=500)])
    description = StringField("Description", validators=[Length(max=200)])
    required = BooleanField("Required")

    class Meta:
        csrf = False


class ServerForm(FlaskForm):
    """Form for adding/editing MCP servers"""

    name = StringField(
        "Server Name",
        validators=[DataRequired(), Length(min=2, max=100)],
        render_kw={"placeholder": "my-mcp-server"},
    )

    github_url = StringField(
        "GitHub URL",
        validators=[DataRequired(), URL(), validate_github_url],
        render_kw={"placeholder": "https://github.com/owner/repo"},
    )

    description = TextAreaField(
        "Description",
        validators=[Length(max=500)],
        render_kw={"placeholder": "Brief description of what this server does..."},
    )

    runtime_type = SelectField(
        "Runtime Type",
        choices=[
            ("npx", "Node.js (npx)"),
            ("uvx", "Python (uvx)"),
            ("python-module", "Python Module"),
        ],
        validators=[DataRequired()],
    )

    install_command = StringField(
        "Install Command",
        validators=[Length(max=500)],
        render_kw={"placeholder": "npm install -g @org/package or pip install package-name"},
    )

    start_command = StringField(
        "Start Command",
        validators=[DataRequired(), Length(max=500)],
        render_kw={"placeholder": "npx @org/package or python -m package"},
    )

    def validate_runtime_type(self, field: SelectField) -> None:
        """Validate runtime type is one of allowed values.

        Args:
            field: The runtime type field to validate

        Raises:
            ValidationError: If runtime type is not valid
        """
        allowed_types = ["npx", "uvx", "python-module"]
        if field.data not in allowed_types:
            raise ValidationError(f'Runtime type must be one of: {", ".join(allowed_types)}')

    def validate_install_command(self, field: StringField) -> None:
        """Ensure install command is provided for npx/uvx servers.

        Args:
            field: The install command field to validate

        Raises:
            ValidationError: If install command is missing for npx/uvx servers
        """
        # Check if runtime_type is npx or uvx and install_command is empty
        if hasattr(self, "runtime_type") and self.runtime_type.data in ["npx", "uvx"]:
            if not field.data or not field.data.strip():
                raise ValidationError(
                    f"Install command is required for {self.runtime_type.data} servers. "
                    f"For npx: 'npm install -g @org/package', for uvx: 'pip install package-name'"
                )


class AnalyzeForm(FlaskForm):
    """Simple form for analyzing a GitHub repository"""

    github_url = StringField(
        "GitHub URL",
        validators=[DataRequired(), URL(), validate_github_url],
        render_kw={"placeholder": "https://github.com/owner/repo"},
    )
