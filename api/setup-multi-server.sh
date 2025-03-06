#!/bin/bash

# Setup script for multi-server VM infrastructure
# This script sets up the necessary packages, directories, and configurations
# for the multi-server VM infrastructure across all servers.

set -e  # Exit on any error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]
  then echo -e "${RED}Please run as root${NC}"
  exit 1
fi

echo -e "${GREEN}Setting up multi-server VM infrastructure...${NC}"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Install required packages
install_packages() {
    echo -e "${YELLOW}Installing required packages...${NC}"
    apt-get update
    apt-get install -y qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst
    apt-get install -y python3-pip python3-venv
    apt-get install -y qemu-utils cloud-image-utils
    apt-get install -y iptables-persistent
    apt-get install -y wget curl jq
}

# Setup libvirt network
setup_libvirt_network() {
    echo -e "${YELLOW}Setting up libvirt network...${NC}"
    
    # Create default network if it doesn't exist
    if ! virsh net-info default >/dev/null 2>&1; then
        echo "Creating default network..."
        cat > /tmp/default-network.xml <<EOF
<network>
  <name>default</name>
  <bridge name="virbr0"/>
  <forward mode="nat"/>
  <ip address="192.168.122.1" netmask="255.255.255.0">
    <dhcp>
      <range start="192.168.122.2" end="192.168.122.254"/>
    </dhcp>
  </ip>
</network>
EOF
        virsh net-define /tmp/default-network.xml
        virsh net-autostart default
        virsh net-start default
        rm /tmp/default-network.xml
    fi
    
    # Create the overlay network for VM experiments
    cat > /tmp/vm-overlay.xml <<EOF
<network>
  <name>vm-overlay</name>
  <bridge name="virbr1"/>
  <forward mode="nat"/>
  <ip address="10.10.0.1" netmask="255.255.0.0">
    <dhcp>
      <range start="10.10.0.2" end="10.10.255.254"/>
    </dhcp>
  </ip>
</network>
EOF
    
    if ! virsh net-info vm-overlay >/dev/null 2>&1; then
        echo "Creating vm-overlay network..."
        virsh net-define /tmp/vm-overlay.xml
        virsh net-autostart vm-overlay
        virsh net-start vm-overlay
    else
        echo "Updating vm-overlay network..."
        virsh net-destroy vm-overlay || true
        virsh net-undefine vm-overlay
        virsh net-define /tmp/vm-overlay.xml
        virsh net-autostart vm-overlay
        virsh net-start vm-overlay
    fi
    
    rm /tmp/vm-overlay.xml
}

# Setup storage directories
setup_storage() {
    echo -e "${YELLOW}Setting up storage directories...${NC}"
    
    # Create the main directory for VM experiments
    VM_DIR="/var/lib/libvirt/images/vm-experiments"
    mkdir -p $VM_DIR
    chown -R libvirt-qemu:kvm $VM_DIR
    chmod -R 777 $VM_DIR
    
    # Create subdirectories
    mkdir -p $VM_DIR/images
    mkdir -p $VM_DIR/disks
    mkdir -p $VM_DIR/volumes
    
    chown -R libvirt-qemu:kvm $VM_DIR/images
    chown -R libvirt-qemu:kvm $VM_DIR/disks
    chown -R libvirt-qemu:kvm $VM_DIR/volumes
    
    chmod -R 777 $VM_DIR/images
    chmod -R 777 $VM_DIR/disks
    chmod -R 777 $VM_DIR/volumes
    
    # Create symlink from app directory
    APP_DIR="/home/ubuntu/vm-experiments/vm_data"
    mkdir -p $APP_DIR
    chown ubuntu:ubuntu $APP_DIR
    chmod 777 $APP_DIR
    
    # Create symlink if it doesn't exist
    if [ ! -L "$APP_DIR/images" ]; then
        ln -s $VM_DIR/images $APP_DIR/images
    fi
    
    if [ ! -L "$APP_DIR/disks" ]; then
        ln -s $VM_DIR/disks $APP_DIR/disks
    fi
    
    if [ ! -L "$APP_DIR/volumes" ]; then
        ln -s $VM_DIR/volumes $APP_DIR/volumes
    fi
}

# Configure networking
configure_networking() {
    echo -e "${YELLOW}Configuring networking...${NC}"
    
    # Enable IP forwarding
    echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-ip-forward.conf
    sysctl -p /etc/sysctl.d/99-ip-forward.conf
    
    # Set up NAT for VMs
    iptables -t nat -A POSTROUTING -s 10.10.0.0/16 -o eth0 -j MASQUERADE
    iptables -t nat -A POSTROUTING -s 192.168.122.0/24 -o eth0 -j MASQUERADE
    
    # Save iptables rules
    iptables-save > /etc/iptables/rules.v4
}

# Download base images
download_base_images() {
    echo -e "${YELLOW}Downloading base images...${NC}"
    
    # Ubuntu 20.04 LTS
    UBUNTU_2004_URL="https://cloud-images.ubuntu.com/releases/focal/release/ubuntu-20.04-server-cloudimg-amd64.img"
    UBUNTU_2004_PATH="$VM_DIR/images/ubuntu-20.04-server-cloudimg-amd64.img"
    
    if [ ! -f "$UBUNTU_2004_PATH" ]; then
        echo "Downloading Ubuntu 20.04 LTS..."
        wget -O "$UBUNTU_2004_PATH" "$UBUNTU_2004_URL"
        chown libvirt-qemu:kvm "$UBUNTU_2004_PATH"
        chmod 666 "$UBUNTU_2004_PATH"
    fi
    
    # Ubuntu 22.04 LTS
    UBUNTU_2204_URL="https://cloud-images.ubuntu.com/releases/jammy/release/ubuntu-22.04-server-cloudimg-amd64.img"
    UBUNTU_2204_PATH="$VM_DIR/images/ubuntu-22.04-server-cloudimg-amd64.img"
    
    if [ ! -f "$UBUNTU_2204_PATH" ]; then
        echo "Downloading Ubuntu 22.04 LTS..."
        wget -O "$UBUNTU_2204_PATH" "$UBUNTU_2204_URL"
        chown libvirt-qemu:kvm "$UBUNTU_2204_PATH"
        chmod 666 "$UBUNTU_2204_PATH"
    fi
}

# Setup AppArmor for QEMU
setup_apparmor() {
    echo -e "${YELLOW}Setting up AppArmor for QEMU...${NC}"
    
    # Create custom AppArmor profile
    cat > /etc/apparmor.d/local/usr.sbin.libvirtd <<EOF
# Allow libvirtd to access VM files in our custom directory
/var/lib/libvirt/images/vm-experiments/** rw,
/home/ubuntu/vm-experiments/vm_data/** rw,
EOF

    # Reload AppArmor
    systemctl reload apparmor
}

# Setup SSH keys for inter-server communication
setup_ssh_keys() {
    echo -e "${YELLOW}Setting up SSH keys for inter-server communication...${NC}"
    
    # Create SSH key for the ubuntu user if it doesn't exist
    if [ ! -f "/home/ubuntu/.ssh/id_rsa" ]; then
        sudo -u ubuntu ssh-keygen -t rsa -N "" -f /home/ubuntu/.ssh/id_rsa
    fi
    
    # Add the key to the authorized_keys file
    sudo -u ubuntu cp /home/ubuntu/.ssh/id_rsa.pub /home/ubuntu/.ssh/authorized_keys
    
    # Display the public key
    echo -e "${GREEN}Add this public key to authorized_keys on other servers:${NC}"
    cat /home/ubuntu/.ssh/id_rsa.pub
}

# Main execution
echo "Starting multi-server setup..."

# Run all setup functions
install_packages
setup_libvirt_network
setup_storage
configure_networking
download_base_images
setup_apparmor
setup_ssh_keys

echo -e "${GREEN}Multi-server VM infrastructure setup complete!${NC}"
echo -e "${YELLOW}To register additional servers, add their information through the API.${NC}"
echo -e "${YELLOW}Ensure that SSH access is configured correctly between servers.${NC}" 