"""Tests for maintenance task registry and handlers."""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


class TestRegistry:
    def test_registry_discovers_handlers(self):
        from api_modular.maintenance_tasks import registry
        tasks = registry.list_all()
        names = [t["name"] for t in tasks]
        assert "db_vacuum" in names
        assert "db_integrity" in names
        assert "db_backup" in names
        assert "library_scan" in names
        assert "hash_verify" in names

    def test_get_known_task(self):
        from api_modular.maintenance_tasks import registry
        task = registry.get("db_vacuum")
        assert task is not None
        assert task.name == "db_vacuum"

    def test_get_unknown_task(self):
        from api_modular.maintenance_tasks import registry
        assert registry.get("nonexistent_task") is None

    def test_validate_is_callable(self):
        from api_modular.maintenance_tasks import registry
        task = registry.get("db_vacuum")
        result = task.validate({})
        assert hasattr(result, "ok")
        assert hasattr(result, "message")

    def test_list_all_has_required_fields(self):
        from api_modular.maintenance_tasks import registry
        for task_info in registry.list_all():
            assert "name" in task_info
            assert "display_name" in task_info
            assert "description" in task_info
