#!/usr/bin/env python3
import sys
import os
import subprocess
from PyQt5.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel,
    QVBoxLayout, QHBoxLayout, QMessageBox, QGroupBox
)
from PyQt5.QtCore import QTimer

# ===================== PATHS =====================
SCRIPT_PATH = "/usr/local/bin/toggle-nvidia-vfio.sh"
SERVICE_PATH = "/etc/systemd/system/toggle-nvidia-vfio.service"
SERVICE_NAME = "toggle-nvidia-vfio.service"
PCI_DRIVER_PATH = "/sys/bus/pci/devices/0000:01:00.0/driver"

# ===================== TOGGLE SCRIPT =====================
TOGGLE_SCRIPT = r"""#!/bin/bash
set -e

NVIDIA_VGA="0000:01:00.0"
NVIDIA_AUDIO="0000:01:00.1"
AMD_VGA="0000:05:00.0"

get_driver() {
    basename "$(readlink /sys/bus/pci/devices/$1/driver)" 2>/dev/null || echo "none"
}

CURRENT_DRIVER=$(get_driver "$NVIDIA_VGA")
echo "Current NVIDIA driver: $CURRENT_DRIVER"

if [[ "$CURRENT_DRIVER" == nvidia* ]]; then
    echo "Switching NVIDIA → VFIO"
    systemctl stop display-manager || true

    modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia || true
    modprobe vfio-pci

    for DEV in $NVIDIA_VGA $NVIDIA_AUDIO; do
        [ -e /sys/bus/pci/devices/$DEV/driver ] && \
            echo "$DEV" > /sys/bus/pci/devices/$DEV/driver/unbind
        echo "$DEV" > /sys/bus/pci/drivers/vfio-pci/bind
    done

    modprobe amdgpu
    [ ! -e /sys/bus/pci/devices/$AMD_VGA/driver ] && \
        echo "$AMD_VGA" > /sys/bus/pci/drivers/amdgpu/bind

    systemctl start display-manager

elif [[ "$CURRENT_DRIVER" == "vfio-pci" ]]; then
    echo "Switching VFIO → NVIDIA"
    systemctl stop display-manager || true

    for DEV in $NVIDIA_VGA $NVIDIA_AUDIO; do
        echo "$DEV" > /sys/bus/pci/devices/$DEV/driver/unbind
    done

    modprobe nvidia nvidia_modeset nvidia_uvm nvidia_drm

    for DEV in $NVIDIA_VGA $NVIDIA_AUDIO; do
        echo "$DEV" > /sys/bus/pci/drivers/nvidia/bind || true
    done

    systemctl start display-manager
else
    echo "Unknown state: $CURRENT_DRIVER"
    exit 1
fi

echo "Final NVIDIA driver: $(get_driver "$NVIDIA_VGA")"
"""

# ===================== SYSTEMD SERVICE =====================
SERVICE_FILE = r"""[Unit]
Description=Toggle NVIDIA GPU between VFIO and Host
After=multi-user.target
Conflicts=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/toggle-nvidia-vfio.sh
StandardOutput=journal
StandardError=journal
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

# ===================== HELPERS =====================
def get_gpu_driver():
    try:
        return os.path.basename(os.readlink(PCI_DRIVER_PATH))
    except Exception:
        return "unknown"

def exists(path):
    return "✅" if os.path.exists(path) else "❌"

def service_active():
    return subprocess.run(
        ["systemctl", "is-active", SERVICE_NAME],
        stdout=subprocess.DEVNULL
    ).returncode == 0

# ===================== GUI =====================
class GPUToggle(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GPU Toggle Control Panel")
        self.setFixedSize(440, 320)

        self.gpu_status = QLabel()
        self.script_status = QLabel()
        self.service_status = QLabel()
        self.systemd_status = QLabel()

        box = QGroupBox("System Status")
        layout = QVBoxLayout()
        layout.addWidget(self.gpu_status)
        layout.addWidget(self.script_status)
        layout.addWidget(self.service_status)
        layout.addWidget(self.systemd_status)
        box.setLayout(layout)

        self.setup_btn = QPushButton("Setup / Repair")
        self.toggle_btn = QPushButton("Toggle GPU")
        self.refresh_btn = QPushButton("Refresh")

        self.setup_btn.clicked.connect(self.setup)
        self.toggle_btn.clicked.connect(self.toggle)
        self.refresh_btn.clicked.connect(self.refresh)

        btns = QHBoxLayout()
        btns.addWidget(self.setup_btn)
        btns.addWidget(self.toggle_btn)
        btns.addWidget(self.refresh_btn)

        main = QVBoxLayout()
        main.addWidget(box)
        main.addLayout(btns)
        self.setLayout(main)

        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)

    # =====================
    def refresh(self):
        drv = get_gpu_driver()
        if drv == "vfio-pci":
            self.gpu_status.setText("GPU Mode: VFIO (VM Ready)")
        elif drv.startswith("nvidia"):
            self.gpu_status.setText("GPU Mode: NVIDIA (Host)")
        else:
            self.gpu_status.setText(f"GPU Mode: {drv}")

        self.script_status.setText(f"Toggle Script: {exists(SCRIPT_PATH)}")
        self.service_status.setText(f"Systemd Service: {exists(SERVICE_PATH)}")
        self.systemd_status.setText(
            f"Service State: {'🟢 active' if service_active() else '🟡 inactive'}"
        )

        self.toggle_btn.setEnabled(
            os.path.exists(SCRIPT_PATH) and os.path.exists(SERVICE_PATH)
        )

    # =====================
    def setup(self):
        if QMessageBox.question(
            self, "Setup",
            "Create or repair script and systemd service?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        try:
            subprocess.run([
                "pkexec", "bash", "-c",
                f"""
cat << 'EOF' > {SCRIPT_PATH}
{TOGGLE_SCRIPT}
EOF
chmod +x {SCRIPT_PATH}

cat << 'EOF' > {SERVICE_PATH}
{SERVICE_FILE}
EOF

systemctl daemon-reload
"""
            ], check=True)
            QMessageBox.information(self, "Success", "Setup completed.")
        except subprocess.CalledProcessError:
            QMessageBox.critical(self, "Error", "Setup failed.")

        self.refresh()

    # =====================
    def toggle(self):
        if QMessageBox.question(
            self, "Toggle GPU",
            "Display session will restart.\nContinue?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        try:
            subprocess.run(
                ["pkexec", "systemctl", "restart", SERVICE_NAME],
                check=True
            )
        except subprocess.CalledProcessError:
            QMessageBox.critical(self, "Error", "Toggle failed.")

        self.refresh()

# =====================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = GPUToggle()
    win.show()
    sys.exit(app.exec_())
