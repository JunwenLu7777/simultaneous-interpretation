#!/usr/bin/env bash
# 一键部署 Teams 双向语音同传桥到新 macOS（arm64）。
#
# 用法：
#   bash scripts/setup.sh
#
# 串起 6 步：
#   1. 平台校验（macOS + arm64）
#   2. Homebrew 检查 + Python 3.13 / uv 安装
#   3. uv 同步项目依赖（必要时重建 .venv）
#   4. Silero VAD ONNX 模型下载（scripts/install-silero-vad.sh）
#   5. config.toml 模板就位（首次运行从 example 复制）
#   6. 提示安装 BlackHole 2ch（需要 sudo 与重启，无法完全自动）
#
# 重跑安全：所有步骤幂等；已就位的资源会跳过。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

step() {
  echo
  echo "==== $1 ===="
}

# ----- 1. 平台校验 -----
step "1/6 平台校验"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "发生了什么：当前操作系统不是 macOS（$(uname -s)）。" >&2
  echo "下一步如何做：本项目仅支持 macOS。" >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "发生了什么：CPU 架构不是 arm64（实测 $(uname -m)）。" >&2
  echo "下一步如何做：本项目要求 Apple Silicon Mac；Intel Mac 暂不支持。" >&2
  exit 1
fi
echo "✓ macOS arm64（$(sw_vers -productVersion)）"

# ----- 2. Homebrew + Python 3.13 + uv -----
step "2/6 Homebrew / Python 3.13 / uv"
if ! command -v brew >/dev/null 2>&1; then
  echo "发生了什么：未找到 Homebrew。" >&2
  echo "下一步如何做：请先安装 Homebrew（https://brew.sh），完成后重跑本脚本。" >&2
  exit 1
fi
echo "✓ Homebrew $(brew --version | head -1)"

if ! brew list --formula python@3.13 >/dev/null 2>&1; then
  echo "  → 安装 python@3.13..."
  brew install python@3.13
fi
echo "✓ Python 3.13（$(brew --prefix python@3.13)）"

if ! command -v uv >/dev/null 2>&1; then
  echo "  → 安装 uv..."
  brew install uv
fi
echo "✓ uv $(uv --version | awk '{print $2}')"

# ----- 3. uv sync -----
step "3/6 项目依赖（uv sync --extra dev）"
if [[ -d .venv ]]; then
  CURRENT_PY="$(.venv/bin/python --version 2>/dev/null | awk '{print $2}' | cut -d. -f1-2 || echo unknown)"
  if [[ "$CURRENT_PY" != "3.13" ]]; then
    echo "  → 当前 .venv 用 Python $CURRENT_PY 与项目要求 3.13 不一致，重建 venv..."
    rm -rf .venv
  fi
fi
uv sync --extra dev
echo "✓ 依赖装齐"
uv run python -c "import av, onnxruntime, edge_tts, pywhispercpp; \
print(f'  - av {av.__version__}'); \
print(f'  - onnxruntime {onnxruntime.__version__}'); \
print('  - edge_tts ok'); \
print('  - pywhispercpp ok')"

# ----- 4. Silero VAD 模型 -----
step "4/6 Silero VAD ONNX 模型"
bash scripts/install-silero-vad.sh

# ----- 5. config.toml -----
step "5/6 本地配置 config.toml"
if [[ -f config.toml ]]; then
  echo "✓ config.toml 已存在（保留原值，未覆盖）"
else
  cp config.example.toml config.toml
  echo "✓ 已从 config.example.toml 创建 config.toml"
  echo "  → 请编辑 config.toml 把 deepseek_api_key 替换为你的真实 Key："
  echo "      \$EDITOR config.toml"
fi

# ----- 6. BlackHole（手动触发） -----
step "6/6 BlackHole 2ch 驱动检查"
BLACKHOLE_DRIVER="/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver"
if [[ -d "$BLACKHOLE_DRIVER" ]]; then
  echo "✓ BlackHole 2ch 驱动已就位（${BLACKHOLE_DRIVER}）"
else
  echo "⚠ 未检测到 BlackHole 2ch 驱动。"
  echo "  → 请单独运行（需要 sudo 与重启 macOS，本脚本不自动调用）："
  echo "      bash scripts/install-blackhole.sh"
  echo "  → 重启后回到「音频 MIDI 设置」创建包含 BlackHole 2ch 的聚合设备，详见 README。"
fi

# ----- 收尾指引 -----
step "安装完成，下一步"
cat <<'TIPS'
1. 如果尚未做：
   - 编辑 config.toml 填入 DeepSeek API Key
   - 运行 bash scripts/install-blackhole.sh + 重启 macOS（首次部署一次性动作）

2. 进入 Teams 前的硬门禁校验：
   uv run --extra dev tvi doctor --mode realtime --confirm-teams-route

3. 启动实时双向同传：
   uv run --extra dev tvi duplex --show-latency

4. 短句测试：
   uv run --extra dev tvi say "你好我们开始会议" --target blackhole

5. 启动本地 Web 控制台：
   uv run --extra dev tvi serve

完整说明见 README.md 与 specs/001-teams-voice-interpreter/quickstart.md。
TIPS
