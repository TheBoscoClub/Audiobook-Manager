#!/bin/bash
# Optional: Set up the local GPU Whisper transcription service.
#
# This is NOT part of the standard Audiobook Manager installation.
# Only install this if:
#   1. You use the localization/subtitle features (non-English locales)
#   2. Your host has an AMD GPU with ROCm support
#   3. You want local GPU-accelerated transcription instead of cloud providers
#
# Requires: python-pytorch-opt-rocm, python-openai-whisper (Arch/CachyOS)
#
# Usage: sudo ./setup.sh [--uninstall]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="whisper-gpu"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
INSTALL_DIR="/opt/whisper-gpu"
SERVICE_SRC="${SCRIPT_DIR}/../../library/localization/stt/whisper_gpu_service.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: Run as root (sudo ./setup.sh)${NC}"
    exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
    echo "Removing whisper-gpu service…"
    systemctl disable --now "${SERVICE_NAME}.service" 2>/dev/null || true
    rm -f "${SERVICE_FILE}"
    systemctl daemon-reload
    rm -rf /opt/whisper-gpu
    echo -e "${GREEN}whisper-gpu service removed.${NC}"
    echo "Note: ROCm/PyTorch packages are NOT removed (other tools may use them)."
    exit 0
fi

# Check prerequisites
echo "Checking prerequisites…"

if ! python3 -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
    echo -e "${RED}Error: PyTorch with ROCm is not installed or no GPU detected.${NC}"
    echo ""
    echo "On CachyOS/Arch, install with:"
    echo "  sudo pacman -S python-pytorch-opt-rocm python-openai-whisper"
    echo ""
    echo "Then verify GPU detection:"
    echo "  python3 -c \"import torch; print(torch.cuda.get_device_name(0))\""
    exit 1
fi

if ! python3 -c "import whisper" 2>/dev/null; then
    echo -e "${RED}Error: OpenAI Whisper is not installed.${NC}"
    echo "  sudo pacman -S python-openai-whisper"
    exit 1
fi

if [[ ! -f "${SERVICE_SRC}" ]]; then
    echo -e "${RED}Error: whisper_gpu_service.py not found at ${SERVICE_SRC}${NC}"
    echo "Run this script from the Audiobook Manager project directory."
    exit 1
fi

GPU_NAME=$(python3 -c "import torch; print(torch.cuda.get_device_name(0))" 2>/dev/null)
echo -e "${GREEN}GPU detected: ${GPU_NAME}${NC}"

# Install service script and prepare model cache
echo "Installing whisper-gpu to ${INSTALL_DIR}…"
mkdir -p "${INSTALL_DIR}/models"
cp "${SERVICE_SRC}" "${INSTALL_DIR}/whisper_gpu_service.py"
chown -R audiobooks:audiobooks "${INSTALL_DIR}"

# Install systemd unit
echo "Installing systemd service…"
cp "${SCRIPT_DIR}/whisper-gpu.service" "${SERVICE_FILE}"
systemctl daemon-reload

# Ensure audiobooks user can access GPU
if ! groups audiobooks 2>/dev/null | grep -qE '\b(render|video)\b'; then
    echo "Adding audiobooks user to render and video groups…"
    usermod -aG render,video audiobooks
fi

echo "Enabling and starting whisper-gpu service…"
systemctl enable --now "${SERVICE_NAME}.service"

# Wait for health check
echo -n "Waiting for service to start"
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:8765/health 2>/dev/null | grep -q '"status"'; then
        echo ""
        echo -e "${GREEN}whisper-gpu service is running.${NC}"
        echo ""
        echo "The service listens on 0.0.0.0:8765 and is accessible from VMs"
        echo "on the libvirt network at 192.168.122.1:8765."
        echo ""
        echo "To configure the audiobook app to use it, add to /etc/audiobooks/audiobooks.conf:"
        echo "  AUDIOBOOKS_WHISPER_GPU_HOST=192.168.122.1"
        echo "  AUDIOBOOKS_WHISPER_GPU_PORT=8765"
        echo ""
        echo "The app auto-detects the service — if it's running, it will be"
        echo "preferred over cloud providers for transcription."
        exit 0
    fi
    echo -n "."
    sleep 2
done

echo ""
echo -e "${YELLOW}Service installed but may still be loading the model (this can take 30-60s).${NC}"
echo "Check status: systemctl status whisper-gpu"
echo "Check logs:   journalctl -u whisper-gpu -f"
