"""
User Preferences API Module (v8)

Key-value preferences system for browsing, playback, and accessibility settings.
Requires authentication — unauthenticated users get hardcoded defaults from the frontend.

Endpoints:
    GET   /api/user/preferences         - Get all preferences (with defaults)
    PATCH /api/user/preferences         - Update one or more preferences
    DELETE /api/user/preferences/<key>  - Reset a single preference to default
    POST  /api/user/preferences/reset   - Reset all preferences to defaults
    GET   /api/user/preferences/defaults - Get default values (no auth required)
"""

from auth import UserSettingsRepository
from flask import Blueprint, jsonify, request

from .auth import get_auth_db, login_required, require_current_user

preferences_bp = Blueprint("preferences", __name__, url_prefix="/api/user/preferences")


def init_preferences_routes() -> None:
    """No-op initializer for consistency with other modules."""


@preferences_bp.route("", methods=["GET"])
@login_required
def get_preferences():
    """Get all preferences for the current user (defaults merged)."""
    user = require_current_user()
    auth_db = get_auth_db()
    repo = UserSettingsRepository(auth_db)
    settings = repo.get_all(user.ensured_id)
    return jsonify(settings)


@preferences_bp.route("", methods=["PATCH"])
@login_required
def update_preferences():
    """
    Update one or more preferences.

    JSON body: flat object of key-value pairs, e.g.:
        {"font_size": "18", "playback_speed": "1.5"}

    Unknown keys are silently ignored.
    Returns the full updated preferences object.
    """
    user = require_current_user()
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "JSON object required"}), 400

    auth_db = get_auth_db()
    repo = UserSettingsRepository(auth_db)

    # Filter to valid string values only
    valid_updates = {}
    for key, value in data.items():
        if key in UserSettingsRepository.VALID_KEYS and isinstance(value, str):
            valid_updates[key] = value

    if not valid_updates:
        return jsonify({"error": "No valid preference keys provided"}), 400

    repo.set_many(user.ensured_id, valid_updates)
    settings = repo.get_all(user.ensured_id)
    return jsonify(settings)


@preferences_bp.route("/<key>", methods=["DELETE"])
@login_required
def reset_preference(key: str):
    """Reset a single preference to its default value."""
    if key not in UserSettingsRepository.VALID_KEYS:
        return jsonify({"error": f"Unknown preference key: {key}"}), 400

    user = require_current_user()
    if user.id is None:
        return jsonify({"error": "User not found"}), 401
    auth_db = get_auth_db()
    repo = UserSettingsRepository(auth_db)
    repo.delete(user.id, key)

    return jsonify({"success": True, "key": key, "value": UserSettingsRepository.DEFAULTS[key]})


@preferences_bp.route("/reset", methods=["POST"])
@login_required
def reset_all_preferences():
    """Reset all preferences to defaults."""
    user = require_current_user()
    auth_db = get_auth_db()
    repo = UserSettingsRepository(auth_db)
    count = repo.delete_all(user.ensured_id)

    return jsonify(
        {
            "success": True,
            "reset_count": count,
            "preferences": dict(UserSettingsRepository.DEFAULTS),
        }
    )


@preferences_bp.route("/defaults", methods=["GET"])
def get_defaults():
    """
    Get the default preference values (no auth required).

    Useful for unauthenticated users or frontend initialization.
    """
    return jsonify(dict(UserSettingsRepository.DEFAULTS))
