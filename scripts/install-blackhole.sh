#!/usr/bin/env bash
set -euo pipefail

if ! command -v brew >/dev/null 2>&1; then
  echo "发生了什么：未找到 Homebrew。"
  echo "下一步如何做：请先安装 Homebrew，再运行 scripts/install-blackhole.sh。"
  exit 1
fi

brew install blackhole-2ch

echo "发生了什么：BlackHole 2ch 已安装或已是最新版本。"
echo "下一步如何做：请重启 macOS，然后在「音频 MIDI 设置」中创建包含 BlackHole 2ch 与耳机的聚合设备。"
