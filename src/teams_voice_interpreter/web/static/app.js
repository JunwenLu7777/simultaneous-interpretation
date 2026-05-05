const toast = document.querySelector("#toast");
const defaultTitle = document.title || "Teams 同传控制台";
const actionButtons = Array.from(document.querySelectorAll("[data-action]"));
const actionLabels = {
  start: "开始",
  pause: "暂停",
  resume: "继续",
  stop: "停止",
};

function showToast(message, variant = "info") {
  toast.textContent = message;
  toast.dataset.variant = variant;
  if (variant === "error") {
    document.title = `异常 - Teams 同传控制台`;
  } else {
    document.title = defaultTitle;
  }
}

function labelFor(action) {
  return actionLabels[action] || action;
}

function setControlsBusy(action) {
  actionButtons.forEach((button) => {
    button.disabled = true;
    button.setAttribute("aria-busy", button.dataset.action === action ? "true" : "false");
  });
}

function setControlsReady() {
  actionButtons.forEach((button) => {
    button.disabled = false;
    button.setAttribute("aria-busy", "false");
  });
}

function formatControlError(action, payload) {
  const detail = payload && payload.detail;
  if (detail && typeof detail === "object" && detail.what_happened && detail.next_action) {
    return `${detail.what_happened}\n${detail.next_action}`;
  }
  if (typeof detail === "string" && detail.length > 0) {
    return `发生了什么：${detail}。\n下一步如何做：请刷新状态后再重试。`;
  }
  return `发生了什么：「${labelFor(action)}」操作失败。\n下一步如何做：请查看状态后重试。`;
}

async function readJson(response) {
  const text = await response.text();
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

async function callControl(action) {
  if (location.protocol !== "http:" && location.protocol !== "https:") {
    showToast(
      "发生了什么：当前页面不是由本地 Web 服务打开。\n下一步如何做：请访问 http://localhost:8765 后重试。",
      "error",
    );
    return;
  }

  setControlsBusy(action);
  showToast(`已收到：正在处理「${labelFor(action)}」操作。`, "info");
  try {
    const response = await fetch(`/api/control/${action}`, { method: "POST" });
    const payload = await readJson(response);
    if (!response.ok) {
      showToast(formatControlError(action, payload), "error");
      return;
    }
    render(payload);
    showToast(`已完成：「${labelFor(action)}」操作已生效。`, "info");
  } catch {
    showToast(
      "发生了什么：控制台无法连接本地后端。\n下一步如何做：请确认服务仍在运行，然后刷新页面重试。",
      "error",
    );
  } finally {
    setControlsReady();
  }
}

function render(payload) {
  const uplink = payload.latest_uplink || {};
  const downlink = payload.latest_downlink || {};
  document.querySelector("#uplink-source").textContent = uplink.source_text || "";
  document.querySelector("#uplink-target").textContent = uplink.target_text || "";
  document.querySelector("#downlink-source").textContent = downlink.source_text || "";
  document.querySelector("#downlink-target").textContent = downlink.target_text || "";
  if (payload.services_health && Object.values(payload.services_health).includes("unavailable")) {
    showToast("发生了什么：服务不可用。\n下一步如何做：系统正在重试。", "error");
  }
}

actionButtons.forEach((button) => {
  button.addEventListener("click", () => callControl(button.dataset.action));
});

if (location.protocol === "http:" || location.protocol === "https:") {
  const socketProtocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${socketProtocol}://${location.host}/ws/status`);
  socket.onmessage = (event) => render(JSON.parse(event.data));
}
