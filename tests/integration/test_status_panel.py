"""US3 状态面板集成测试。"""

from fastapi.testclient import TestClient

from teams_voice_interpreter.web.server import create_app


def test_status_panel_rest_and_websocket() -> None:
    """状态接口应含双向字段，WebSocket 推送频率声明 ≥ 5 Hz。"""
    client = TestClient(create_app())

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert "latest_uplink" in payload
    assert "latest_downlink" in payload
    assert payload["ws_push_hz"] >= 5

    with client.websocket_connect("/ws/status") as websocket:
        pushed = websocket.receive_json()
    assert pushed["ws_push_hz"] >= 5


def test_start_is_idempotent_after_pause() -> None:
    """暂停后点击开始应恢复会话，不应返回 500。"""
    client = TestClient(create_app())

    assert client.post("/api/control/start").status_code == 200
    assert client.post("/api/control/pause").status_code == 200
    response = client.post("/api/control/start")

    assert response.status_code == 200
    assert response.json()["state"] == "active"
