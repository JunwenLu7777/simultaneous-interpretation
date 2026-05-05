"""Web 控制台入口与点击反馈回归测试。"""

from fastapi.testclient import TestClient

from teams_voice_interpreter.web.server import create_app


def test_web_console_is_served_from_documented_root() -> None:
    """文档声明的根路径必须直接打开控制台页面。"""
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Teams 同传控制台" in response.text
    assert 'data-action="start"' in response.text


def test_web_console_click_script_has_immediate_feedback() -> None:
    """点击控制按钮后必须先给出可见处理中反馈，并保留失败提示路径。"""
    client = TestClient(create_app())

    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "aria-busy" in response.text
    assert "正在处理" in response.text
    assert "catch" in response.text


def test_status_websocket_allows_client_disconnect() -> None:
    """浏览器刷新或离开页面时 WebSocket 断开不得触发服务端异常。"""
    client = TestClient(create_app())

    with client.websocket_connect("/ws/status") as websocket:
        websocket.close()
