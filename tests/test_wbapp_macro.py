#   Python test originally created or extracted from Hannah's work (https://github.com/hannahbee91/nxbt).
#   Some modifications might have been made to adapt to my own project.

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_nxbt():
    mock = MagicMock()
    mock.state = {
        0: {
            "state": "connected",
            "finished_macros": [],
            "errors": [],
        }
    }
    mock.get_switch_addresses.return_value = []
    mock.create_controller.return_value = 0
    mock.macro.return_value = "macro_id_123"
    return mock


@pytest.fixture
def web_app(mock_nxbt):
    from nxbt.web.app import WebApp

    return WebApp(nxbt=mock_nxbt)


def test_state_emission(web_app):
    """Test that on_state reads state and emits it."""
    mock_emit = AsyncMock()

    with patch.object(web_app.sio, "emit", mock_emit):
        web_app.on_state("test_sid")

    mock_emit.assert_awaited_once()
    event, state_data = mock_emit.await_args.args
    assert event == "state"
    assert mock_emit.await_args.kwargs["to"] == "test_sid"
    assert 0 in state_data
    assert state_data[0]["state"] == "connected"


def test_controller_creation(web_app, mock_nxbt):
    """Test that on_create_controller creates a controller."""
    mock_emit = AsyncMock()
    web_app._user_info["test_sid"] = {}

    with (
        patch.object(web_app.sio, "emit", mock_emit),
        patch.object(
            web_app,
            "_get_adapter_availability",
            return_value={"adapters": ["hci-socket:0"], "has_permissions": True},
        ),
    ):
        asyncio.run(web_app.on_create_controller("test_sid"))

    mock_nxbt.create_controller.assert_called_once()
    events = [call.args[0] for call in mock_emit.await_args_list]
    assert "create_pro_controller" in events


def test_controller_creation_no_adapters(web_app, mock_nxbt):
    """Test that missing adapters emits no_adapters."""
    mock_emit = AsyncMock()
    web_app._user_info["test_sid"] = {}

    with (
        patch.object(web_app.sio, "emit", mock_emit),
        patch.object(
            web_app,
            "_get_adapter_availability",
            return_value={"adapters": [], "has_permissions": False},
        ),
    ):
        asyncio.run(web_app.on_create_controller("test_sid"))

    mock_nxbt.create_controller.assert_not_called()
    payload = mock_emit.await_args.args[1]
    assert payload["title"] == "Permissions Required"
    assert "required permissions" in payload["message"]


def test_controller_creation_no_adapters_detected(web_app, mock_nxbt):
    """Test that no detected adapters emits the no-adapters payload."""
    mock_emit = AsyncMock()
    web_app._user_info["test_sid"] = {}

    with (
        patch.object(web_app.sio, "emit", mock_emit),
        patch.object(
            web_app,
            "_get_adapter_availability",
            return_value={"adapters": [], "has_permissions": True},
        ),
    ):
        asyncio.run(web_app.on_create_controller("test_sid"))

    mock_nxbt.create_controller.assert_not_called()
    payload = mock_emit.await_args.args[1]
    assert payload["title"] == "No Adapters Available"


def test_handle_macro(web_app, mock_nxbt):
    """Test that handle_macro passes the correct args to nxbt.macro."""
    macro_payload = json.dumps([0, "B 0.1s\nA 0.1s"])
    web_app.handle_macro("test_sid", macro_payload)

    mock_nxbt.macro.assert_called_once()
    call_args = mock_nxbt.macro.call_args
    assert call_args[0][0] == 0
    assert "B" in call_args[0][1]
    assert "A" in call_args[0][1]


def test_handle_input_ignores_missing_controller(web_app, mock_nxbt):
    mock_nxbt.set_controller_input.side_effect = ValueError(
        "Specified controller does not exist"
    )
    web_app.handle_input("test_sid", json.dumps([0, {}]))


def test_handle_macro_does_not_block_and_reports_id(web_app, mock_nxbt):
    with patch.object(web_app, "_emit_to") as emit:
        web_app.handle_macro("sid", json.dumps([0, "A 0.1s"]))

    assert mock_nxbt.macro.call_args.kwargs["block"] is False
    emit.assert_called_once_with("sid", "macro_started", "macro_id_123")


def test_stop_and_clear_macros(web_app, mock_nxbt):
    web_app.handle_stop_macro("sid", json.dumps([0, "macro_id_123"]))
    web_app.handle_clear_macros("sid", 0)

    mock_nxbt.stop_macro.assert_called_once_with(0, "macro_id_123", block=False)
    mock_nxbt.clear_macros.assert_called_once_with(0)


def test_invalid_macro_is_reported_not_run(web_app, mock_nxbt):
    with patch.object(web_app, "_emit_to") as emit:
        web_app.handle_macro("sid", json.dumps([0, "HOM\n"]))

    mock_nxbt.macro.assert_not_called()
    assert emit.call_args.args[1] == "macro_error"
    assert emit.call_args.args[2].startswith("Line 1: ")
