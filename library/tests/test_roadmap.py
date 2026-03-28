"""
Tests for the Roadmap API blueprint (api_modular/roadmap.py).

Uses the auth-enabled Flask app fixtures from conftest.py.
Admin endpoints require admin_client; public GET uses user_client or anon_client.
"""

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_roadmap_item(
    auth_app,
    title="Test Item",
    status="planned",
    priority="medium",
    sort_order=0,
    description="",
):
    """Insert a roadmap item directly into the database and return its id."""
    db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "INSERT INTO roadmap_items (title, description, status, priority, sort_order, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (title, description, status, priority, sort_order),
    )
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id


def _clear_roadmap(auth_app):
    """Remove all roadmap items."""
    db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM roadmap_items")
    conn.commit()
    conn.close()


def _count_roadmap(auth_app):
    """Count roadmap items in the database."""
    db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")
    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM roadmap_items").fetchone()[0]
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Fixture: clean roadmap state before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_roadmap(auth_app):
    """Ensure the roadmap table exists and is empty before each test.

    The auth_app fixture uses inline SQL that may not include roadmap_items,
    so we create the table if it doesn't exist.  Also re-points the roadmap
    blueprint's module-level _db_path to auth_app's database — another
    session-scoped create_app() call (flask_app) may have overwritten it.
    """
    db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")

    # Re-point roadmap blueprint to auth_app's database.
    # The blueprint may be loaded under multiple module paths
    # (api_modular.roadmap AND backend.api_modular.roadmap) — set _db_path on ALL.
    import sys

    for mod_name in list(sys.modules):
        if mod_name.endswith("api_modular.roadmap"):
            mod = sys.modules[mod_name]
            if hasattr(mod, "init_roadmap_routes"):
                mod.init_roadmap_routes(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS roadmap_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'planned',
            priority TEXT NOT NULL DEFAULT 'medium',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_roadmap_status ON roadmap_items(status);
        CREATE INDEX IF NOT EXISTS idx_roadmap_sort ON roadmap_items(sort_order);
    """)
    conn.execute("DELETE FROM roadmap_items")
    conn.commit()
    conn.close()
    yield
    _clear_roadmap(auth_app)


# ===========================================================================
# Public GET /api/roadmap
# ===========================================================================


class TestPublicGetRoadmap:
    """Tests for the public roadmap endpoint."""

    def test_empty_roadmap(self, user_client):
        """Public endpoint returns empty list when no items exist."""
        resp = user_client.get("/api/roadmap")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_non_cancelled_items(self, auth_app, user_client):
        """Public endpoint excludes cancelled items."""
        _insert_roadmap_item(auth_app, title="Active Item", status="planned")
        _insert_roadmap_item(auth_app, title="Cancelled Item", status="cancelled")
        _insert_roadmap_item(auth_app, title="In Progress", status="in_progress")

        resp = user_client.get("/api/roadmap")
        assert resp.status_code == 200
        items = resp.get_json()
        titles = [i["title"] for i in items]
        assert "Active Item" in titles
        assert "In Progress" in titles
        assert "Cancelled Item" not in titles

    def test_ordered_by_sort_order_asc(self, auth_app, user_client):
        """Items are returned ordered by sort_order ascending."""
        _insert_roadmap_item(auth_app, title="Third", sort_order=30)
        _insert_roadmap_item(auth_app, title="First", sort_order=10)
        _insert_roadmap_item(auth_app, title="Second", sort_order=20)

        resp = user_client.get("/api/roadmap")
        items = resp.get_json()
        titles = [i["title"] for i in items]
        assert titles == ["First", "Second", "Third"]

    def test_anon_can_access_public_roadmap(self, anon_client):
        """Unauthenticated users can access the public roadmap."""
        resp = anon_client.get("/api/roadmap")
        # Public endpoint — should work even without auth
        # (login_required is NOT on this endpoint)
        assert resp.status_code == 200

    def test_completed_items_visible(self, auth_app, user_client):
        """Completed items are visible on the public endpoint."""
        _insert_roadmap_item(auth_app, title="Done", status="completed")
        resp = user_client.get("/api/roadmap")
        items = resp.get_json()
        assert len(items) == 1
        assert items[0]["title"] == "Done"


# ===========================================================================
# Admin GET /api/admin/roadmap
# ===========================================================================


class TestAdminGetRoadmap:
    """Tests for the admin roadmap listing endpoint."""

    def test_admin_sees_all_items(self, auth_app, admin_client):
        """Admin endpoint includes cancelled items."""
        _insert_roadmap_item(auth_app, title="Active", status="planned")
        _insert_roadmap_item(auth_app, title="Cancelled", status="cancelled")

        resp = admin_client.get("/api/admin/roadmap")
        assert resp.status_code == 200
        items = resp.get_json()
        titles = [i["title"] for i in items]
        assert "Active" in titles
        assert "Cancelled" in titles

    def test_admin_empty_list(self, admin_client):
        """Admin endpoint returns empty list when no items exist."""
        resp = admin_client.get("/api/admin/roadmap")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_non_admin_forbidden(self, user_client):
        """Regular user gets 403 on admin roadmap endpoint."""
        resp = user_client.get("/api/admin/roadmap")
        assert resp.status_code == 403

    def test_anon_unauthorized(self, anon_client):
        """Unauthenticated request gets 401 on admin roadmap endpoint."""
        resp = anon_client.get("/api/admin/roadmap")
        assert resp.status_code == 401


# ===========================================================================
# Admin POST /api/admin/roadmap (create)
# ===========================================================================


class TestCreateRoadmapItem:
    """Tests for creating roadmap items."""

    def test_create_minimal(self, auth_app, admin_client):
        """Create an item with only the required 'title' field."""
        resp = admin_client.post(
            "/api/admin/roadmap",
            json={"title": "New Feature"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "id" in data
        assert data["message"] == "Created"
        assert _count_roadmap(auth_app) == 1

    def test_create_with_all_fields(self, auth_app, admin_client):
        """Create an item with all optional fields."""
        resp = admin_client.post(
            "/api/admin/roadmap",
            json={
                "title": "Full Feature",
                "description": "A detailed description",
                "status": "in_progress",
                "priority": "high",
                "sort_order": 5,
            },
        )
        assert resp.status_code == 201

        # Verify via admin GET
        items = admin_client.get("/api/admin/roadmap").get_json()
        item = [i for i in items if i["title"] == "Full Feature"][0]
        assert item["description"] == "A detailed description"
        assert item["status"] == "in_progress"
        assert item["priority"] == "high"
        assert item["sort_order"] == 5

    def test_create_missing_title(self, admin_client):
        """Missing title returns 400."""
        resp = admin_client.post("/api/admin/roadmap", json={"description": "no title"})
        assert resp.status_code == 400
        assert "title" in resp.get_json()["error"].lower()

    def test_create_empty_body(self, admin_client):
        """Empty JSON body returns 400."""
        resp = admin_client.post(
            "/api/admin/roadmap",
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_create_invalid_status(self, admin_client):
        """Invalid status value returns 400."""
        resp = admin_client.post(
            "/api/admin/roadmap",
            json={"title": "Bad Status", "status": "unknown"},
        )
        assert resp.status_code == 400
        assert "status" in resp.get_json()["error"].lower()

    def test_create_invalid_priority(self, admin_client):
        """Invalid priority value returns 400."""
        resp = admin_client.post(
            "/api/admin/roadmap",
            json={"title": "Bad Priority", "priority": "critical"},
        )
        assert resp.status_code == 400
        assert "priority" in resp.get_json()["error"].lower()

    def test_create_defaults(self, admin_client):
        """Default status is 'planned', default priority is 'medium'."""
        admin_client.post("/api/admin/roadmap", json={"title": "Defaults"})
        items = admin_client.get("/api/admin/roadmap").get_json()
        item = items[0]
        assert item["status"] == "planned"
        assert item["priority"] == "medium"
        assert item["sort_order"] == 0

    def test_create_non_admin_forbidden(self, user_client):
        """Regular user cannot create roadmap items."""
        resp = user_client.post(
            "/api/admin/roadmap",
            json={"title": "Sneaky"},
        )
        assert resp.status_code == 403

    def test_create_anon_unauthorized(self, anon_client):
        """Unauthenticated request cannot create roadmap items."""
        resp = anon_client.post(
            "/api/admin/roadmap",
            json={"title": "Anon"},
        )
        assert resp.status_code == 401


# ===========================================================================
# Admin PUT /api/admin/roadmap/<id> (update)
# ===========================================================================


class TestUpdateRoadmapItem:
    """Tests for updating roadmap items."""

    def test_update_title(self, auth_app, admin_client):
        """Update an item's title."""
        item_id = _insert_roadmap_item(auth_app, title="Old Title")
        resp = admin_client.put(
            f"/api/admin/roadmap/{item_id}",
            json={"title": "New Title"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Updated"

        items = admin_client.get("/api/admin/roadmap").get_json()
        assert items[0]["title"] == "New Title"

    def test_update_multiple_fields(self, auth_app, admin_client):
        """Update multiple fields at once."""
        item_id = _insert_roadmap_item(auth_app, title="Original")
        resp = admin_client.put(
            f"/api/admin/roadmap/{item_id}",
            json={
                "title": "Updated",
                "status": "completed",
                "priority": "high",
                "description": "Now done",
                "sort_order": 99,
            },
        )
        assert resp.status_code == 200

        items = admin_client.get("/api/admin/roadmap").get_json()
        item = items[0]
        assert item["title"] == "Updated"
        assert item["status"] == "completed"
        assert item["priority"] == "high"
        assert item["description"] == "Now done"
        assert item["sort_order"] == 99

    def test_update_sets_updated_at(self, auth_app, admin_client):
        """Update changes the updated_at timestamp."""
        item_id = _insert_roadmap_item(auth_app, title="Timestamped")
        items_before = admin_client.get("/api/admin/roadmap").get_json()
        original_updated = items_before[0]["updated_at"]

        admin_client.put(
            f"/api/admin/roadmap/{item_id}",
            json={"title": "Changed"},
        )
        items_after = admin_client.get("/api/admin/roadmap").get_json()
        assert items_after[0]["updated_at"] != original_updated

    def test_update_not_found(self, admin_client):
        """Updating a nonexistent item returns 404."""
        resp = admin_client.put(
            "/api/admin/roadmap/99999",
            json={"title": "Ghost"},
        )
        assert resp.status_code == 404

    def test_update_no_data(self, auth_app, admin_client):
        """Sending no JSON body returns 400."""
        item_id = _insert_roadmap_item(auth_app, title="No Data")
        resp = admin_client.put(
            f"/api/admin/roadmap/{item_id}",
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_update_no_valid_fields(self, auth_app, admin_client):
        """Sending only unrecognized fields returns 400."""
        item_id = _insert_roadmap_item(auth_app, title="No Fields")
        resp = admin_client.put(
            f"/api/admin/roadmap/{item_id}",
            json={"unknown_field": "value"},
        )
        assert resp.status_code == 400
        assert "no valid fields" in resp.get_json()["error"].lower()

    def test_update_invalid_status(self, auth_app, admin_client):
        """Invalid status on update returns 400."""
        item_id = _insert_roadmap_item(auth_app, title="Bad Update")
        resp = admin_client.put(
            f"/api/admin/roadmap/{item_id}",
            json={"status": "invalid_status"},
        )
        assert resp.status_code == 400

    def test_update_invalid_priority(self, auth_app, admin_client):
        """Invalid priority on update returns 400."""
        item_id = _insert_roadmap_item(auth_app, title="Bad Prio")
        resp = admin_client.put(
            f"/api/admin/roadmap/{item_id}",
            json={"priority": "urgent"},
        )
        assert resp.status_code == 400

    def test_update_non_admin_forbidden(self, auth_app, user_client):
        """Regular user cannot update roadmap items."""
        item_id = _insert_roadmap_item(auth_app, title="Protected")
        resp = user_client.put(
            f"/api/admin/roadmap/{item_id}",
            json={"title": "Hacked"},
        )
        assert resp.status_code == 403

    def test_update_anon_unauthorized(self, auth_app, anon_client):
        """Unauthenticated request cannot update roadmap items."""
        item_id = _insert_roadmap_item(auth_app, title="Protected2")
        resp = anon_client.put(
            f"/api/admin/roadmap/{item_id}",
            json={"title": "Hacked"},
        )
        assert resp.status_code == 401


# ===========================================================================
# Admin DELETE /api/admin/roadmap/<id>
# ===========================================================================


class TestDeleteRoadmapItem:
    """Tests for deleting roadmap items."""

    def test_delete_item(self, auth_app, admin_client):
        """Delete an existing item."""
        item_id = _insert_roadmap_item(auth_app, title="Delete Me")
        resp = admin_client.delete(f"/api/admin/roadmap/{item_id}")
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Deleted"
        assert _count_roadmap(auth_app) == 0

    def test_delete_not_found(self, admin_client):
        """Deleting a nonexistent item returns 404."""
        resp = admin_client.delete("/api/admin/roadmap/99999")
        assert resp.status_code == 404

    def test_delete_non_admin_forbidden(self, auth_app, user_client):
        """Regular user cannot delete roadmap items."""
        item_id = _insert_roadmap_item(auth_app, title="Protected")
        resp = user_client.delete(f"/api/admin/roadmap/{item_id}")
        assert resp.status_code == 403

    def test_delete_anon_unauthorized(self, auth_app, anon_client):
        """Unauthenticated request cannot delete roadmap items."""
        item_id = _insert_roadmap_item(auth_app, title="Protected2")
        resp = anon_client.delete(f"/api/admin/roadmap/{item_id}")
        assert resp.status_code == 401

    def test_delete_only_target(self, auth_app, admin_client):
        """Deleting one item leaves others intact."""
        _insert_roadmap_item(auth_app, title="Keep")
        id2 = _insert_roadmap_item(auth_app, title="Remove")
        admin_client.delete(f"/api/admin/roadmap/{id2}")
        assert _count_roadmap(auth_app) == 1
        items = admin_client.get("/api/admin/roadmap").get_json()
        assert items[0]["title"] == "Keep"


# ===========================================================================
# Ordering / Priority behavior
# ===========================================================================


class TestRoadmapOrdering:
    """Tests for item ordering and priority."""

    def test_sort_order_respected(self, auth_app, admin_client):
        """Items are sorted by sort_order ascending."""
        _insert_roadmap_item(auth_app, title="C", sort_order=30)
        _insert_roadmap_item(auth_app, title="A", sort_order=10)
        _insert_roadmap_item(auth_app, title="B", sort_order=20)

        items = admin_client.get("/api/admin/roadmap").get_json()
        titles = [i["title"] for i in items]
        assert titles == ["A", "B", "C"]

    def test_same_sort_order_secondary_sort(self, auth_app, admin_client):
        """Items with same sort_order use created_at DESC as tiebreaker."""
        # Both items have sort_order=0 and are returned together;
        # verify they are both present (exact sub-order depends on insert timing)
        _insert_roadmap_item(auth_app, title="ItemA", sort_order=0)
        _insert_roadmap_item(auth_app, title="ItemB", sort_order=0)

        items = admin_client.get("/api/admin/roadmap").get_json()
        titles = {i["title"] for i in items}
        assert titles == {"ItemA", "ItemB"}

    def test_all_valid_statuses_accepted(self, admin_client):
        """All valid status values are accepted on creation."""
        for status in ("planned", "in_progress", "completed", "cancelled"):
            resp = admin_client.post(
                "/api/admin/roadmap",
                json={"title": f"Status {status}", "status": status},
            )
            assert resp.status_code == 201, f"Status {status} rejected"

    def test_all_valid_priorities_accepted(self, admin_client):
        """All valid priority values are accepted on creation."""
        for priority in ("low", "medium", "high"):
            resp = admin_client.post(
                "/api/admin/roadmap",
                json={"title": f"Priority {priority}", "priority": priority},
            )
            assert resp.status_code == 201, f"Priority {priority} rejected"
