"""管理面关键路由的静态冒烟测试。

这些测试不启动数据库或浏览器，只验证应用装配不会让固定路径被动态参数路由吞掉。
"""
from __future__ import annotations

from app.main import create_app


def _http_routes():
    return [route for route in create_app().routes if hasattr(route, "methods")]


def test_provider_connection_route_precedes_provider_id_route() -> None:
    routes = _http_routes()
    test_index = next(
        index
        for index, route in enumerate(routes)
        if route.path == "/api/v2/providers/test-connection" and "POST" in route.methods
    )
    dynamic_index = next(
        index
        for index, route in enumerate(routes)
        if route.path == "/api/v2/providers/{provider_id}" and "PUT" in route.methods
    )
    assert test_index < dynamic_index


def test_management_routes_are_registered() -> None:
    paths = {route.path for route in _http_routes()}
    assert {
        "/api/v2/accounts/all",
        "/api/v2/providers/test-connection",
        "/api/v2/system/database",
        "/api/v2/system/infrastructure",
        "/api/v2/system/logs",
        "/api/v2/system/redis",
        "/api/v2/system/redis/test",
        "/api/v2/system/summary/stats",
        "/api/v2/tasks",
        "/api/v2/auth/qq/qrcode",
        "/api/v2/auth/xhs/qrcode",
    } <= paths


def test_legacy_routes_are_not_exposed() -> None:
    paths = {route.path for route in create_app().routes}
    assert "/api/proxy/image" not in paths
    assert "/ws/client" not in paths
    assert not any(
        path.startswith(prefix)
        for path in paths
        for prefix in (
            "/api/qq",
            "/api/xhs",
            "/api/auth",
            "/api/chat",
            "/api/tasks",
            "/api/ai-config",
            "/api/system",
        )
    )
