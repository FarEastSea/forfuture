import pytest

from app.browser.kernel import _restore_storage_state


@pytest.mark.asyncio
async def test_restore_storage_state_for_persistent_context():
    calls = {}

    class Context:
        async def add_cookies(self, cookies):
            calls["cookies"] = cookies

        async def add_init_script(self, script):
            calls["script"] = script

    state = {
        "cookies": [{"name": "session", "value": "value", "domain": ".example.com"}],
        "origins": [
            {
                "origin": "https://www.example.com",
                "localStorage": [{"name": "token", "value": "local-value"}],
            }
        ],
    }

    await _restore_storage_state(Context(), state)

    assert calls["cookies"] == state["cookies"]
    assert '"https://www.example.com"' in calls["script"]
    assert '"token": "local-value"' in calls["script"]
