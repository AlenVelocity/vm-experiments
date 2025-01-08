#!/bin/bash

# Exit on error
set -e

echo "Installing dependencies for Ubuntu..."

# Install system packages
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    libvirt-dev \
    pkg-config \
    qemu-kvm \
    libvirt-daemon-system \
    libvirt-clients \
    bridge-utils \
    genisoimage \
    iptables-persistent \
    cpu-checker

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r api/requirements.txt

# Install frontend dependencies
cd frontend
npm install

echo "Dependencies installed successfully!" 