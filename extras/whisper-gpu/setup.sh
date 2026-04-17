#!/bin/bash
# Optional: Set up the local GPU Whisper transcription service.
#
# This is NOT part of the standard Audiobook Manager installation.
# Only install this if:
#   1. You use the localization/subtitle features (non-English locales)
#   2. Your host has a GPU that is known-good for sustained AI inference:
#        - NVIDIA (RTX 30xx/40xx, A-series, L-series, H100, A100, L40S) + CUDA
#        - Enterprise AMD Instinct (MI-series / CDNA) + ROCm
#      Consumer AMD Radeon (RDNA 2 / RDNA 3) + ROCm is KNOWN-UNSTABLE under
#      sustained Whisper inference — see docs/MULTI-LANGUAGE-SETUP.md for the
#      maintainer's cautionary tale.
#   3. You want local GPU-accelerated transcription instead of remote providers
#
# Requires (Arch/CachyOS examples — adapt to your distro):
#   NVIDIA + CUDA: nvidia + cuda + python-pytorch-cuda + python-openai-whisper
#   Enterprise AMD + ROCm: rocm-hip-runtime + python-pytorch-opt-rocm + python-openai-whisper
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
    echo -e "${RED}Error: PyTorch with CUDA/ROCm is not installed or no GPU detected.${NC}"
    echo ""
    echo "On CachyOS/Arch:"
    echo "  NVIDIA + CUDA:          sudo pacman -S nvidia cuda python-pytorch-cuda python-openai-whisper"
    echo "  Enterprise AMD + ROCm:  sudo pacman -S rocm-hip-runtime python-pytorch-opt-rocm python-openai-whisper"
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

# Warn on consumer AMD Radeon RDNA 2/3 — known-unstable under sustained AI inference.
# RDNA 2: 6600/6650/6700/6750/6800/6900/6950 (and XT variants)
# RDNA 3: 7600/7700/7800/7900 (and XT/XTX variants)
if echo "${GPU_NAME}" | grep -qiE 'Radeon.*(RX ?(6[6789]|79|77|78)[0-9]{2}|7900)'; then
    echo ""
    echo -e "${YELLOW}WARNING: Consumer AMD Radeon RDNA 2/3 detected.${NC}"
    echo -e "${YELLOW}This hardware class is KNOWN-UNSTABLE under sustained Whisper inference with ROCm.${NC}"
    echo ""
    echo "The project maintainer experienced a catastrophic host crash on an RX 6800 XT"
    echo "(UEFI/BIOS config wiped, working tree corrupted). See docs/MULTI-LANGUAGE-SETUP.md"
    echo "for the full cautionary tale."
    echo ""
    echo "If you proceed:"
    echo "  - Run short test jobs first, not full-library batches"
    echo "  - Monitor GPU resets with: dmesg -w | grep amdgpu"
    echo "  - Keep your project under version control pushed to a remote"
    echo "  - Have filesystem/BIOS backups"
    echo ""
    read -r -p "Continue installing anyway? [y/N] " confirm
    case "${confirm}" in
        [yY] | [yY][eE][sS]) ;;
        *)
            echo "Aborting. Consider remote GPU (Vast.ai / RunPod) instead — see docs/MULTI-LANGUAGE-SETUP.md."
            exit 1
            ;;
    esac
fi

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
        echo "The service listens on 0.0.0.0:8765. Reach it from the app host at"
        echo "the IP/hostname that is routable for your deployment (e.g., the host's"
        echo "LAN IP, a libvirt bridge address, or 127.0.0.1 for same-host installs)."
        echo ""
        echo "To configure the audiobook app to use it, add to /etc/audiobooks/audiobooks.conf:"
        echo "  AUDIOBOOKS_WHISPER_GPU_HOST=<your-whisper-host>"
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
