"""MCP server control and proxy routes"""

from typing import Union
from flask import Blueprint, render_template, request, jsonify, Response, flash
from flask_login import login_required
from flask_wtf.csrf import CSRFProtect
from mcp_router.models import get_connection_status
from mcp_router.logging_config import get_logger
from mcp_router.models import set_auth_type

logger = get_logger(__name__)

# Create blueprint
mcp_bp = Blueprint("mcp", __name__)


# Helper to register CSRF exemptions after app creation
def register_csrf_exemptions(csrf: CSRFProtect) -> None:
    """Register CSRF exemptions for MCP routes

    Args:
        csrf: CSRFProtect instance to configure
    """
    # No CSRF exemptions needed - MCP routes are handled by ASGI layer
    pass


# Note: MCP requests are now handled directly by the mounted FastMCP app in the ASGI layer
# No proxy route needed since the MCP app is mounted at /mcp in the ASGI application


# MCP Control route removed - functionality moved to main status panel on home page


@mcp_bp.route("/api/mcp/status", methods=["GET"])
@login_required
def get_mcp_status() -> Union[str, Response]:
    """Get current MCP server status

    Returns:
        HTML template for htmx requests or JSON for API requests
    """
    status = get_connection_status(request=request)

    # Return HTML for htmx requests
    if request.headers.get("HX-Request"):
        return render_template("partials/mcp_status.html", status=status)

    # Return JSON for API requests
    return jsonify(status)


@mcp_bp.route("/api/mcp/auth-type", methods=["POST"])
@login_required
def update_auth_type() -> Union[str, Response]:
    """Update authentication type preference

    Returns:
        Updated status HTML for HTMX or JSON response
    """
    try:
        # Get and validate auth type parameter
        auth_type = request.form.get("auth_type", "").strip().lower()
        if auth_type not in ("oauth", "api_key"):
            if request.headers.get("HX-Request"):
                flash(f"Invalid auth type '{auth_type}'. Must be 'oauth' or 'api_key'.", "error")
                status = get_connection_status(request=request)
                return render_template("partials/mcp_status.html", status=status)
            else:
                return (
                    jsonify({"error": "Invalid auth type", "valid_values": ["oauth", "api_key"]}),
                    400,
                )

        # Update auth type in database
        set_auth_type(auth_type)

        logger.info(f"Auth type updated to: {auth_type}")

        # Get updated status
        status = get_connection_status(request=request)

        if request.headers.get("HX-Request"):
            flash(f"Authentication type switched to {auth_type.upper()}", "success")
            return render_template("partials/mcp_status.html", status=status)
        else:
            return jsonify({"message": f"Auth type updated to {auth_type}", "status": status})

    except Exception as e:
        logger.error(f"Error updating auth type: {e}")
        if request.headers.get("HX-Request"):
            flash("Error updating authentication type", "error")
            status = get_connection_status(request=request)
            return render_template("partials/mcp_status.html", status=status)
        else:
            return jsonify({"error": "Internal server error"}), 500
