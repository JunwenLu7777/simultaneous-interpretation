#!/usr/bin/env bash
# 下载 Silero VAD ONNX 模型到本地缓存，并按 SHA256 锁定校验。
#
# 用法：
#   bash scripts/install-silero-vad.sh
#
# 产物：
#   ~/.cache/teams-voice-interpreter/vad/silero_vad.onnx
#
# 重新跑：已存在且 SHA256 匹配时直接跳过；不匹配则重下。

set -euo pipefail

VAD_VERSION="v5.1.2"
EXPECTED_SHA256="2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
DOWNLOAD_URL="https://raw.githubusercontent.com/snakers4/silero-vad/${VAD_VERSION}/src/silero_vad/data/silero_vad.onnx"

CACHE_DIR="${HOME}/.cache/teams-voice-interpreter/vad"
MODEL_PATH="${CACHE_DIR}/silero_vad.onnx"

# macOS / Linux 通用 SHA256 计算
sha256_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    echo "发生了什么：未找到 shasum 或 sha256sum 命令。" >&2
    echo "下一步如何做：在 macOS 上 shasum 应当已安装；如缺失请执行 \`xcode-select --install\`。" >&2
    exit 1
  fi
}

mkdir -p "$CACHE_DIR"

if [[ -f "$MODEL_PATH" ]]; then
  ACTUAL_SHA256="$(sha256_of "$MODEL_PATH")"
  if [[ "$ACTUAL_SHA256" == "$EXPECTED_SHA256" ]]; then
    echo "Silero VAD 模型已就位：${MODEL_PATH}（SHA256 匹配）。"
    exit 0
  fi
  echo "Silero VAD 模型 SHA256 不匹配，将重新下载。"
  echo "  预期：${EXPECTED_SHA256}"
  echo "  实际：${ACTUAL_SHA256}"
fi

echo "正在下载 Silero VAD ${VAD_VERSION} 模型 (~2.3 MB)..."
if ! curl -fsSL -o "$MODEL_PATH" "$DOWNLOAD_URL"; then
  echo "发生了什么：从 ${DOWNLOAD_URL} 下载失败。" >&2
  echo "下一步如何做：请检查网络后重试；如长期不可达可手动从 https://github.com/snakers4/silero-vad/releases/tag/${VAD_VERSION} 下载并放到 ${MODEL_PATH}。" >&2
  exit 1
fi

ACTUAL_SHA256="$(sha256_of "$MODEL_PATH")"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "发生了什么：下载的 Silero VAD 模型 SHA256 与锁定值不符，可能下载损坏或上游被替换。" >&2
  echo "  预期：${EXPECTED_SHA256}" >&2
  echo "  实际：${ACTUAL_SHA256}" >&2
  echo "下一步如何做：删除 ${MODEL_PATH} 后重试；若多次失败请提交 issue 附下载日志。" >&2
  rm -f "$MODEL_PATH"
  exit 1
fi

echo "Silero VAD 模型已下载并校验：${MODEL_PATH}"
