#!/bin/bash

# ===================== ROOT CHECK =====================
if [[ $EUID -ne 0 ]]; then
   echo "This script requires administrative privileges."
   sudo "$0" "$@"
   exit $?
fi

# ===================== CONFIG & PATHS =====================
SCRIPT_PATH="/usr/local/bin/toggle-vfio-logic.sh"
SERVICE_PATH="/etc/systemd/system/toggle-vfio.service"
SERVICE_NAME="toggle-vfio.service"

# ===================== HARDWARE DETECTION =====================
AUTO_GPU=$(lspci -Dnn | grep -i "VGA" | grep -i "NVIDIA" | head -n 1 | awk '{print $1}')
AUTO_AUDIO=$(lspci -Dnn | grep -i "Audio" | grep -i "NVIDIA" | head -n 1 | awk '{print $1}')

# ===================== HELPERS =====================
get_current_driver() {
    local dev=$1
    [ -z "$dev" ] || [ "$dev" == "None" ] && echo "none" && return
    local drv_path="/sys/bus/pci/devices/$dev/driver"
    [ -L "$drv_path" ] && basename "$(readlink "$drv_path")" || echo "none"
}

check_file() {
    [ -f "$1" ] && echo -e "\e[32mOK\e[0m" || echo -e "\e[31mMissing\e[0m"
}

# ===================== LOGIC =====================
show_status() {
    clear
    local target_gpu=${MANUAL_GPU:-$AUTO_GPU}
    local target_audio=${MANUAL_AUDIO:-$AUTO_AUDIO}
    local drv=$(get_current_driver "$target_gpu")
    
    echo -e "\e[41m\e[97m  ⚠️  WARNING: REQUIREMENTS CHECK  \e[0m"
    echo -e "\e[33m1. Secondary GPU required for Host OS display.\e[0m"
    echo -e "\e[33m2. IOMMU must be enabled (intel_iommu=on / amd_iommu=on).\e[0m"
    echo -e "\e[33m3. Display Manager will restart. SAVE YOUR WORK!\e[0m"
    echo "=========================================="
    echo "       DYNAMIC GPU TOGGLE PANEL"
    echo "=========================================="
    
    if [[ "$drv" == nvidia* ]]; then
        echo -e "CURRENT MODE   : \e[32mHOST / GAMING (NVIDIA)\e[0m"
    elif [[ "$drv" == "vfio-pci" ]]; then
        echo -e "CURRENT MODE   : \e[35mPASSTHROUGH (VFIO)\e[0m"
    else
        echo -e "CURRENT MODE   : \e[33mUNBOUND / IDLE\e[0m"
    fi

    echo "------------------------------------------"
    echo -e "Video PCI ID   : \e[1m${target_gpu:-Not Found}\e[0m $([[ -n $MANUAL_GPU ]] && echo -e "\e[34m(Manual)\e[0m")"
    echo -e "Audio PCI ID   : \e[1m${target_audio:-Not Found}\e[0m $([[ -n $MANUAL_AUDIO ]] && echo -e "\e[34m(Manual)\e[0m")"
    echo -e "Active Driver  : \e[34m$drv\e[0m"
    echo -e "Include Audio  : \e[36m${INCLUDE_AUDIO:-Yes}\e[0m"
    echo "------------------------------------------"
    echo -e "Setup Status   : $(check_file $SCRIPT_PATH)"
    echo "=========================================="
}

do_setup() {
    local target_gpu=${MANUAL_GPU:-$AUTO_GPU}
    local target_audio=""
    [[ "$INCLUDE_AUDIO" != "No" ]] && target_audio=${MANUAL_AUDIO:-$AUTO_AUDIO}

    if [ -z "$target_gpu" ]; then
        echo "Error: No GPU detected. Set a manual ID."
        read; return
    fi

    echo "Applying configuration for GPU: $target_gpu"
    
cat << EOF > $SCRIPT_PATH
#!/bin/bash
GPU_ID="$target_gpu"
AUDIO_ID="$target_audio"

DM_SERVICE=\$(systemctl list-units --type=service | grep -E 'gdm|sddm|lightdm|display-manager' | head -n 1 | awk '{print \$1}')

get_drv() { basename "\$(readlink /sys/bus/pci/devices/\$1/driver)" 2>/dev/null || echo "none"; }
CUR=\$(get_drv "\$GPU_ID")

systemctl stop \$DM_SERVICE || true
sleep 1

if [[ "\$CUR" == nvidia* ]]; then
    modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia || true
    modprobe vfio-pci
    for D in \$GPU_ID \$AUDIO_ID; do
        if [ -n "\$D" ] && [ -e /sys/bus/pci/devices/\$D ]; then
            [ -e /sys/bus/pci/devices/\$D/driver ] && echo "\$D" > /sys/bus/pci/devices/\$D/driver/unbind
            echo "\$D" > /sys/bus/pci/drivers/vfio-pci/bind
        fi
    done
else
    for D in \$GPU_ID \$AUDIO_ID; do 
        if [ -n "\$D" ] && [ -e /sys/bus/pci/devices/\$D/driver ]; then
            echo "\$D" > /sys/bus/pci/devices/\$D/driver/unbind
        fi
    done
    modprobe nvidia nvidia_modeset nvidia_uvm nvidia_drm
    echo "\$GPU_ID" > /sys/bus/pci/drivers/nvidia/bind || true
fi

systemctl start \$DM_SERVICE
EOF
    chmod +x $SCRIPT_PATH

cat << EOF > $SERVICE_PATH
[Unit]
Description=Toggle GPU Driver Mode
After=network.target

[Service]
Type=oneshot
ExecStart=$SCRIPT_PATH
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    echo "Setup finished. Press Enter."
    read
}

# ===================== MENU LOOP =====================
while true; do
    show_status
    echo "1) Toggle GPU (Switch Mode)"
    echo "2) Install / Update Config (Apply Settings)"
    echo "3) Set Manual VIDEO PCI ID (Current: ${MANUAL_GPU:-Auto})"
    echo "4) Set Manual AUDIO PCI ID (Current: ${MANUAL_AUDIO:-Auto})"
    echo "5) Reset IDs to Auto-Detect"
    echo "6) Toggle Audio Support (Yes/No)"
    echo "7) Refresh"
    echo "8) Exit"
    echo -n "Select option: "
    read -r opt

    case $opt in
        1) 
            if [ ! -f "$SCRIPT_PATH" ]; then
                echo "Error: Run Option 2 first!"; sleep 2
            else
                systemctl restart "$SERVICE_NAME" 
            fi
            ;;
        2) do_setup ;;
        3) 
            echo -n "Enter Video PCI ID (e.g. 0000:01:00.0): "
            read -r MANUAL_GPU ;;
        4) 
            echo -n "Enter Audio PCI ID (e.g. 0000:01:00.1): "
            read -r MANUAL_AUDIO ;;
        5) 
            MANUAL_GPU=""
            MANUAL_AUDIO=""
            echo "IDs reset to auto-detect."
            sleep 1 ;;
        6) 
            [[ "$INCLUDE_AUDIO" == "No" ]] && INCLUDE_AUDIO="Yes" || INCLUDE_AUDIO="No" ;;
        7) continue ;;
        8) exit 0 ;;
    esac
done
