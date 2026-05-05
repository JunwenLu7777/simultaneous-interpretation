const toast = document.querySelector("#toast");

function showToast(message) {
  toast.textContent = message;
  document.title = `异常 - Teams 同传控制台`;
}

async function callControl(action) {
  const response = await fetch(`/api/control/${action}`, { method: "POST" });
  if (!response.ok) {
    showToast("发生了什么：控制操作失败。下一步如何做：请查看状态后重试。");
    return;
  }
  render(await response.json());
}

function render(payload) {
  const uplink = payload.latest_uplink || {};
  const downlink = payload.latest_downlink || {};
  document.querySelector("#uplink-source").textContent = uplink.source_text || "";
  document.querySelector("#uplink-target").textContent = uplink.target_text || "";
  document.querySelector("#downlink-source").textContent = downlink.source_text || "";
  document.querySelector("#downlink-target").textContent = downlink.target_text || "";
  if (payload.services_health && Object.values(payload.services_health).includes("unavailable")) {
    showToast("发生了什么：服务不可用。下一步如何做：系统正在重试。");
  }
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => callControl(button.dataset.action));
});

const socket = new WebSocket(`ws://${location.host}/ws/status`);
socket.onmessage = (event) => render(JSON.parse(event.data));
