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
