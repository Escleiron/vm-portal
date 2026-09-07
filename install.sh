#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# VM Portal - Installation Script
# ============================================================

APP_DIR="/opt/vm-portal"
CONFIG_DIR="/etc/vm-portal"
STATE_DIR="/var/lib/vm-portal"
LOG_DIR="/var/log/vm-portal"
LIBVIRT_IMAGE_DIR="/home/libvirt-images"

ENV_FILE="${CONFIG_DIR}/vm-portal.env"
SERVICE_FILE="/etc/systemd/system/vm-portal.service"

DEFAULT_ADMIN_USER="unixadm"
DEFAULT_PORTAL_PORT="8087"

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

info() {
    echo
    echo "==> $*"
}

warn() {
    echo
    echo "WARNING: $*" >&2
}

error() {
    echo
    echo "ERROR: $*" >&2
    exit 1
}

require_root() {
    [[ "${EUID}" -eq 0 ]] || error "Run this script as root."
}

ask_with_default() {
    local prompt="$1"
    local default="$2"
    local answer

    read -r -p "${prompt} [${default}]: " answer
    echo "${answer:-$default}"
}

ask_yes_no() {
    local prompt="$1"
    local default="${2:-y}"
    local answer

    while true; do
        if [[ "${default}" == "y" ]]; then
            read -r -p "${prompt} [Y/n]: " answer
            answer="${answer:-y}"
        else
            read -r -p "${prompt} [y/N]: " answer
            answer="${answer:-n}"
        fi

        case "${answer,,}" in
            y|yes) return 0 ;;
            n|no)  return 1 ;;
            *)     echo "Please answer yes or no." ;;
        esac
    done
}

# ------------------------------------------------------------
# Initial checks
# ------------------------------------------------------------

require_root

[[ -d "${APP_DIR}" ]] || error "VM Portal source directory not found: ${APP_DIR}"
[[ -f "${APP_DIR}/web/app.py" ]] || error "VM Portal application not found: ${APP_DIR}/web/app.py"
[[ -f "${APP_DIR}/requirements.txt" ]] || error "requirements.txt not found: ${APP_DIR}/requirements.txt"

ADMIN_USER="$(ask_with_default "User that will manage/run the VM Portal" "${DEFAULT_ADMIN_USER}")"
PORTAL_PORT="$(ask_with_default "Portal TCP port" "${DEFAULT_PORTAL_PORT}")"

id "${ADMIN_USER}" >/dev/null 2>&1 || error "User '${ADMIN_USER}' does not exist."

ADMIN_HOME="$(getent passwd "${ADMIN_USER}" | cut -d: -f6)"
[[ -n "${ADMIN_HOME}" && -d "${ADMIN_HOME}" ]] || error "Could not determine home directory for ${ADMIN_USER}."

SSH_DIR="${ADMIN_HOME}/.ssh"
SSH_PRIVATE_KEY="${SSH_DIR}/id_rsa"
SSH_PUBLIC_KEY="${SSH_PRIVATE_KEY}.pub"

echo
echo "============================================================"
echo " VM Portal installation"
echo "============================================================"
echo "Application : ${APP_DIR}"
echo "Admin user  : ${ADMIN_USER}"
echo "Portal port : ${PORTAL_PORT}"
echo "Config      : ${CONFIG_DIR}"
echo "State       : ${STATE_DIR}"
echo "Logs        : ${LOG_DIR}"
echo "Libvirt data: ${LIBVIRT_IMAGE_DIR}"
echo "============================================================"

ask_yes_no "Continue with installation?" "y" || exit 0

# ------------------------------------------------------------
# Operating system
# ------------------------------------------------------------

info "Checking operating system"

if [[ -f /etc/redhat-release ]]; then
    echo "Detected: $(cat /etc/redhat-release)"
else
    warn "This does not appear to be a Red Hat based system."
    ask_yes_no "Continue anyway?" "n" || exit 1
fi

# ------------------------------------------------------------
# Packages
# ------------------------------------------------------------

info "Checking required packages"

PACKAGES=(
    ansible-core
    libvirt-client
    libvirt-daemon
    libvirt-daemon-driver-qemu
    qemu-img
    virt-install
    genisoimage
    xorriso
    python3
    python3-pip
    python3-devel
    openssl
    git
    openssh-clients
)

MISSING_PACKAGES=()

for package in "${PACKAGES[@]}"; do
    if ! rpm -q "${package}" >/dev/null 2>&1; then
        MISSING_PACKAGES+=("${package}")
    fi
done

if (( ${#MISSING_PACKAGES[@]} > 0 )); then
    info "Installing missing packages: ${MISSING_PACKAGES[*]}"
    dnf install -y "${MISSING_PACKAGES[@]}"
else
    echo "All required packages are already installed."
fi

# ------------------------------------------------------------
# Ansible Galaxy dependencies
# ------------------------------------------------------------

info "Checking Ansible Galaxy requirements"

ANSIBLE_REQUIREMENTS="${APP_DIR}/requirements.yml"

if [[ -f "${ANSIBLE_REQUIREMENTS}" ]]; then
    ansible-galaxy collection install \
        -r "${ANSIBLE_REQUIREMENTS}"
else
    echo "No Ansible Galaxy requirements.yml found."
fi

# ------------------------------------------------------------
# Libvirt
# ------------------------------------------------------------

info "Checking libvirt"

if systemctl list-unit-files libvirtd.service >/dev/null 2>&1; then
    if ! systemctl is-active --quiet libvirtd; then
        systemctl enable --now libvirtd
    fi
else
    warn "libvirtd.service was not found."
fi

if ! virsh -c qemu:///system uri >/dev/null 2>&1; then
    error "Cannot connect to libvirt using qemu:///system."
fi

echo "Libvirt connection OK: qemu:///system"

# ------------------------------------------------------------
# Admin user / libvirt access
# ------------------------------------------------------------

info "Configuring ${ADMIN_USER}"

if getent group libvirt >/dev/null 2>&1; then
    usermod -aG libvirt "${ADMIN_USER}"
    echo "Added ${ADMIN_USER} to group libvirt."
else
    warn "Group libvirt does not exist."
fi

# ------------------------------------------------------------
# SSH key
# ------------------------------------------------------------

info "Checking SSH key"

install -d -m 0700 -o "${ADMIN_USER}" -g "${ADMIN_USER}" "${SSH_DIR}"

if [[ -f "${SSH_PRIVATE_KEY}" || -f "${SSH_PUBLIC_KEY}" ]]; then
    if [[ -f "${SSH_PRIVATE_KEY}" && -f "${SSH_PUBLIC_KEY}" ]]; then
        echo "Existing SSH key found: ${SSH_PRIVATE_KEY}"
        echo "It will NOT be overwritten."
    else
        error "Incomplete SSH key pair found. Expected both:"
        echo "  ${SSH_PRIVATE_KEY}"
        echo "  ${SSH_PUBLIC_KEY}"
    fi
else
    if ask_yes_no "No ${SSH_PRIVATE_KEY} key exists. Generate one?" "y"; then
        sudo -u "${ADMIN_USER}" ssh-keygen \
            -t rsa \
            -b 4096 \
            -f "${SSH_PRIVATE_KEY}" \
            -N ""
        echo "SSH key generated: ${SSH_PRIVATE_KEY}"
    else
        error "An SSH key is required by the current provisioning roles."
    fi
fi

chmod 0600 "${SSH_PRIVATE_KEY}"
chmod 0644 "${SSH_PUBLIC_KEY}"
chown "${ADMIN_USER}:${ADMIN_USER}" "${SSH_PRIVATE_KEY}" "${SSH_PUBLIC_KEY}"

# ------------------------------------------------------------
# Runtime directories
# ------------------------------------------------------------

info "Creating runtime directories"

install -d -m 0755 "${CONFIG_DIR}"
install -d -m 0755 "${STATE_DIR}"
install -d -m 0755 "${LOG_DIR}"

# ------------------------------------------------------------
# Libvirt storage layout
# ------------------------------------------------------------

info "Creating libvirt storage layout"

install -d -m 0755 "${LIBVIRT_IMAGE_DIR}"
install -d -m 0755 "${LIBVIRT_IMAGE_DIR}/templates"
install -d -m 0755 "${LIBVIRT_IMAGE_DIR}/iso"
install -d -m 0755 "${LIBVIRT_IMAGE_DIR}/vms"
install -d -m 0755 "${LIBVIRT_IMAGE_DIR}/work"
install -d -m 0755 "${LIBVIRT_IMAGE_DIR}/trash"

chown "${ADMIN_USER}:${ADMIN_USER}" \
    "${LIBVIRT_IMAGE_DIR}/vms" \
    "${LIBVIRT_IMAGE_DIR}/work" \
    "${LIBVIRT_IMAGE_DIR}/trash"

# ------------------------------------------------------------
# VM state
# ------------------------------------------------------------

info "Initializing VM state"

if [[ ! -f "${STATE_DIR}/vms.yml" ]]; then
    cat > "${STATE_DIR}/vms.yml" <<'EOF'
vms: []
EOF
    chmod 0644 "${STATE_DIR}/vms.yml"
fi

chown "${ADMIN_USER}:${ADMIN_USER}" "${STATE_DIR}/vms.yml"

# ------------------------------------------------------------
# Environment file / delete key
# ------------------------------------------------------------

info "Configuring portal environment"

if [[ -f "${ENV_FILE}" ]]; then
    echo "Existing environment file found: ${ENV_FILE}"
    echo "It will NOT be overwritten."
else
    DELETE_KEY="$(openssl rand -hex 32)"

    cat > "${ENV_FILE}" <<EOF
# VM Portal local configuration
VM_DELETE_KEY=${DELETE_KEY}
VM_PORT=${PORTAL_PORT}
VM_PORTAL_ADMIN_USER=${ADMIN_USER}
EOF

    chmod 0600 "${ENV_FILE}"
    chown root:root "${ENV_FILE}"

    echo "Created ${ENV_FILE}"
fi

if ! grep -q '^VM_PORT=' "${ENV_FILE}"; then
    echo "VM_PORT=${PORTAL_PORT}" >> "${ENV_FILE}"
fi

if ! grep -q '^VM_PORTAL_ADMIN_USER=' "${ENV_FILE}"; then
    echo "VM_PORTAL_ADMIN_USER=${ADMIN_USER}" >> "${ENV_FILE}"
fi

# ------------------------------------------------------------
# Python virtual environment
# ------------------------------------------------------------

info "Creating Python virtual environment"

VENV_DIR="${APP_DIR}/venv"

if [[ ! -d "${VENV_DIR}" ]]; then
    sudo -u "${ADMIN_USER}" python3 -m venv "${VENV_DIR}"
fi

sudo -u "${ADMIN_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip
sudo -u "${ADMIN_USER}" "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"

chown -R "${ADMIN_USER}:${ADMIN_USER}" "${VENV_DIR}"

# ------------------------------------------------------------
# Provisioning resources
# ------------------------------------------------------------

info "Checking VM Portal provisioning resources"

RHEL_ISO="${LIBVIRT_IMAGE_DIR}/iso/rhel-9.8-x86_64-dvd.iso"
GOLD_IMAGE="${LIBVIRT_IMAGE_DIR}/templates/rhel9-gold.qcow2"

if [[ -f "${RHEL_ISO}" ]]; then
    echo "RHEL installation ISO found:"
    echo "  ${RHEL_ISO}"
else
    warn "RHEL installation ISO not found:"
    echo "  ${RHEL_ISO}"
fi

if [[ -f "${GOLD_IMAGE}" ]]; then
    echo "Gold image found:"
    echo "  ${GOLD_IMAGE}"
else
    warn "Gold image not found:"
    echo "  ${GOLD_IMAGE}"
fi

# ------------------------------------------------------------
# Inventory
# ------------------------------------------------------------

info "Generating Ansible inventory"

INVENTORY_GENERATOR="${APP_DIR}/scripts/generate_inventory.py"

if [[ -x "${INVENTORY_GENERATOR}" ]]; then
    sudo -u "${ADMIN_USER}" "${INVENTORY_GENERATOR}" || \
        warn "Inventory generation failed. It can be generated later."
else
    warn "Inventory generator not found or not executable:"
    echo "  ${INVENTORY_GENERATOR}"
fi

# ------------------------------------------------------------
# Systemd service
# ------------------------------------------------------------

info "Installing systemd service"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=RHEL 9 Virtual Machines Portal
After=network-online.target libvirtd.service
Wants=network-online.target
Requires=libvirtd.service

[Service]
Type=simple
User=${ADMIN_USER}
Group=${ADMIN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=${VENV_DIR}/bin/python ${APP_DIR}/web/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

chmod 0644 "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable vm-portal.service
systemctl restart vm-portal.service

# ------------------------------------------------------------
# Final checks
# ------------------------------------------------------------

info "Running final checks"

sleep 2

if systemctl is-active --quiet vm-portal.service; then
    echo "VM Portal service: OK"
else
    warn "VM Portal service is not active."
    systemctl --no-pager --full status vm-portal.service || true
fi

if ss -lnt 2>/dev/null | grep -q ":${PORTAL_PORT} "; then
    echo "Portal listening on TCP port ${PORTAL_PORT}: OK"
else
    warn "Portal is not currently listening on TCP port ${PORTAL_PORT}."
fi

if command -v ansible-galaxy >/dev/null 2>&1; then
    echo "Ansible Galaxy: OK"
fi

if [[ -x "${INVENTORY_GENERATOR}" ]]; then
    echo "Inventory generator: OK"
else
    warn "Inventory generator: NOT READY"
fi

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------

echo
echo "============================================================"
echo " VM Portal installation completed"
echo "============================================================"
echo
echo "Portal:"
echo "  http://<HOSTNAME_OR_IP>:${PORTAL_PORT}/"
echo
echo "Application:"
echo "  ${APP_DIR}"
echo
echo "Configuration:"
echo "  ${ENV_FILE}"
echo
echo "VM state:"
echo "  ${STATE_DIR}/vms.yml"
echo
echo "Logs:"
echo "  ${LOG_DIR}"
echo
echo "Libvirt storage:"
echo "  ${LIBVIRT_IMAGE_DIR}/templates/"
echo "  ${LIBVIRT_IMAGE_DIR}/iso/"
echo "  ${LIBVIRT_IMAGE_DIR}/vms/"
echo "  ${LIBVIRT_IMAGE_DIR}/work/"
echo "  ${LIBVIRT_IMAGE_DIR}/trash/"
echo
echo "Provisioning sources:"
echo "  Gold      : ${GOLD_IMAGE}"
echo "  Kickstart : ${RHEL_ISO}"
echo
echo "Service:"
echo "  systemctl status vm-portal.service"
echo
echo "IMPORTANT:"
echo "  A new login/session may be required for ${ADMIN_USER}"
echo "  to pick up the libvirt group membership."
echo "============================================================"

