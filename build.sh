#!/usr/bin/env bash
set -euo pipefail

# FeiFeiLe Nuitka 单文件打包脚本（Linux x86_64）
# 用法: bash build.sh
# 产物: dist/feifeile
#
# 需要 Python 3.12+，推荐在官方 Python 镜像中执行：
#   docker run --rm -v "$PWD":/src -w /src python:3.12-bullseye bash build.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---------- 1. 系统构建依赖（Debian/Ubuntu） ----------
if command -v apt-get &>/dev/null; then
    echo ">>> 安装系统构建依赖..."
    apt-get update -qq
    apt-get install -y -qq \
        gcc g++ patchelf make ccache ca-certificates \
        zlib1g-dev libssl-dev libffi-dev libbz2-dev libreadline-dev \
        libsqlite3-dev liblzma-dev > /dev/null
fi

# ---------- 2. 检查 Python 3.12+ ----------
echo ">>> 检查 Python 版本..."
python3 --version
if ! python3 --version 2>/dev/null | grep -qE "Python 3\.(1[2-9]|[2-9][0-9])\.[0-9]+"; then
    echo "错误: 需要 Python 3.12+，请在 python:3.12-bullseye 容器中运行" >&2
    exit 1
fi

# ---------- 3. Poetry ----------
if ! command -v poetry &>/dev/null; then
    echo ">>> 安装 Poetry..."
    pip install --quiet poetry
fi

# ---------- 4. 项目依赖（通过 Poetry） ----------
echo ">>> 安装项目依赖..."
poetry install --no-interaction --only main

# ---------- 5. Nuitka 构建工具 ----------
echo ">>> 安装 Nuitka 构建工具..."
poetry run pip install --quiet nuitka ordered-set zstandard

# ---------- 6. Nuitka 打包 ----------
mkdir -p dist

echo ">>> 开始 Nuitka 打包..."
poetry run python -m nuitka \
    --onefile \
    --output-dir=dist \
    --output-filename=feifeile \
    --include-package=feifeile \
    --assume-yes-for-downloads \
    --remove-output \
    feifeile/cli.py

echo ">>> 打包完成"
ls -lh dist/feifeile

# ---------- 7. 冒烟测试 ----------
echo ">>> 冒烟测试..."
dist/feifeile --help
echo ">>> 测试通过 ✓"
