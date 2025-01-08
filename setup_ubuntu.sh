#!/bin/bash

# Exit on error
set -e

echo "Setting up VM environment on Ubuntu (ARM)..."

# Install required packages
sudo apt-get update
sudo apt-get install -y \
    qemu-system-arm \
    qemu-efi-aarch64 \
    qemu-utils \
    libvirt-daemon-system \
    libvirt-clients \
    bridge-utils \
    genisoimage \
    python3-libvirt \
    python3-pip \
    iptables-persistent

# Add user to required groups
sudo usermod -aG libvirt $USER
sudo usermod -aG kvm $USER

# Create required directories
sudo mkdir -p /var/lib/libvirt/images
sudo chown $USER:$USER /var/lib/libvirt/images

# Enable and start libvirtd
sudo systemctl enable libvirtd
sudo systemctl start libvirtd

# Enable IP forwarding
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-libvirt.conf
echo "net.ipv4.conf.all.forwarding=1" | sudo tee -a /etc/sysctl.d/99-libvirt.conf
sudo sysctl -p /etc/sysctl.d/99-libvirt.conf

# Create default network if it doesn't exist
if ! sudo virsh net-list --all | grep -q "default"; then
    cat << EOF > /tmp/default-network.xml
<network>
  <name>default</name>
  <forward mode='nat'/>
  <bridge name='virbr0' stp='on' delay='0'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.122.2' end='192.168.122.254'/>
    </dhcp>
  </ip>
</network>
EOF
    sudo virsh net-define /tmp/default-network.xml
    sudo virsh net-autostart default
    sudo virsh net-start default
fi

# Install Python dependencies
pip3 install -r requirements.txt

echo "Setup complete! Please log out and log back in for group changes to take effect." 