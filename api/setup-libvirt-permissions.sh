#!/bin/bash

# This script sets up the permissions for libvirt directories to allow VMs to be created

# Exit on any error
set -e

# Define the VM directory
VM_DIR="/var/lib/libvirt/images/vm-experiments"

# Create the directory if it doesn't exist
sudo mkdir -p $VM_DIR

# Set permissions to allow libvirt to access
sudo chmod -R 777 $VM_DIR

# Set ownership to libvirt-qemu:kvm to ensure QEMU can access
sudo chown -R libvirt-qemu:kvm $VM_DIR

# Create a symlink from /var/lib/libvirt/images/vm-experiments to /home/ubuntu/vm-experiments/vm_data
SYMLINK_TARGET="/home/ubuntu/vm-experiments/vm_data"
mkdir -p $SYMLINK_TARGET
chmod 777 $SYMLINK_TARGET

echo "VM directory $VM_DIR has been created and permissions set correctly."
echo "You can now run the API server." 