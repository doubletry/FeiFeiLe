#!/usr/bin/env bash
set -euo pipefail

# FeiFeiLe Nuitka 单文件打包脚本（Linux x86_64）
# 用法: bash build.sh
# 产物: dist/feifeile
#
# 为保证 Debian 10+ 兼容性，请在 glibc <= 2.28 的环境中执行：
#   docker run --rm -v "$PWD":/src -w /src debian:buster bash build.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---------- 1. 系统构建依赖（Debian/Ubuntu） ----------
if command -v apt-get &>/dev/null; then
    echo ">>> 安装系统构建依赖..."
    apt-get update -qq
    apt-get install -y -qq \
        wget curl gcc g++ patchelf make ccache ca-certificates \
        zlib1g-dev libssl-dev libffi-dev libbz2-dev libreadline-dev \
        libsqlite3-dev liblzma-dev > /dev/null
fi

# ---------- 2. Python 3.12（通过 Miniconda 获取） ----------
if ! python3 --version 2>/dev/null | grep -q "3\.12"; then
    echo ">>> 安装 Miniconda 获取 Python 3.12..."
    wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
    bash /tmp/miniconda.sh -b -p /opt/miniconda
    eval "$(/opt/miniconda/bin/conda shell.bash hook)"
    conda create -y -q -n build python=3.12
    conda activate build
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
    --static-libpython=yes \
    --output-dir=dist \
    --output-filename=feifeile \
    --include-package=feifeile \
    --include-package=pydantic \
    --include-package=pydantic_settings \
    --include-package=httpx \
    --include-package=click \
    --include-package=loguru \
    --include-package=dotenv \
    --include-package=certifi \
    --include-package=httpcore \
    --include-package=anyio \
    --include-package=sniffio \
    --include-package=idna \
    --include-package=h11 \
    --enable-plugin=no-qt \
    --assume-yes-for-downloads \
    --remove-output \
    feifeile/cli.py

echo ">>> 打包完成"
ls -lh dist/feifeile

# ---------- 7. 冒烟测试 ----------
echo ">>> 冒烟测试..."
dist/feifeile --help
echo ">>> 测试通过 ✓"
