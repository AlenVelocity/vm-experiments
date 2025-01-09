#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
import time
import json
from pathlib import Path
import shutil
import requests
from typing import Optional, List, Dict
from vpc import VPCManager, VPC
import logging

logger = logging.getLogger(__name__)

class Colors:
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    NC = '\033[0m'

class VMManager:
    def __init__(self, vm_dir: Path):
        self.vm_dir = vm_dir
        self.vm_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        print(f"[VMManager] {message}")

    def _install_dependencies(self) -> None:
        """Install required system packages."""
        try:
            # Update package list
            subprocess.run(['sudo', 'apt-get', 'update'], check=True)
            
            # Install required packages
            packages = [
                'qemu-system-aarch64',  # QEMU for ARM64
                'qemu-utils',           # QEMU utilities
                'cloud-image-utils',    # For cloud image manipulation
                'mkisofs',              # For ISO creation
            ]
            
            subprocess.run(['sudo', 'apt-get', 'install', '-y'] + packages, check=True)
            self.log("Dependencies installed successfully")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install dependencies: {e}")
            raise

    def setup(self) -> None:
        """Set up the VM environment."""
        self._install_dependencies()
        self.log("Setup completed successfully")

    def _prepare_cloud_image(self, image_url: str) -> Path:
        """Download and prepare the cloud image."""
        try:
            image_path = self.vm_dir / "ubuntu-22.04-server-cloudimg-arm64.img"
            
            if not image_path.exists():
                # Download the image
                subprocess.run(['wget', '-O', str(image_path), image_url], check=True)
            
            # Convert and resize the image
            qcow2_file = self.vm_dir / "ubuntu-22.04-server-cloudimg-arm64.qcow2"
            subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2',
                          str(image_path), str(qcow2_file)], check=True)
            subprocess.run(['qemu-img', 'resize', str(qcow2_file), '20G'], check=True)
            
            return qcow2_file
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to prepare cloud image: {e}")
            raise

    def create_vm(self, name: str, memory_mb: int = 2048, vcpus: int = 2) -> None:
        """Create a new VM."""
        try:
            vm_path = self.vm_dir / name
            vm_path.mkdir(parents=True, exist_ok=True)

            # Generate SSH key pair
            key_path = vm_path / "id_rsa"
            subprocess.run(['ssh-keygen', '-t', 'rsa', '-N', '', '-f',
                          str(key_path)], check=True)

            # Prepare cloud-init configuration
            meta_data = f"""instance-id: {name}
local-hostname: {name}
"""
            user_data = f"""#cloud-config
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - {key_path.read_text().strip()}
"""
            # Write cloud-init files
            (vm_path / "meta-data").write_text(meta_data)
            (vm_path / "user-data").write_text(user_data)

            # Create cloud-init ISO
            subprocess.run(['mkisofs', '-output', str(vm_path / f'{name}-cloud-init.iso'),
                          '-volid', 'cidata', '-joliet', '-rock',
                          str(vm_path / "user-data"),
                          str(vm_path / "meta-data")], check=True)

            # Prepare the VM disk
            cloud_image = self._prepare_cloud_image("https://cloud-images.ubuntu.com/releases/22.04/release/ubuntu-22.04-server-cloudimg-arm64.img")
            vm_disk = vm_path / f"{name}.qcow2"
            
            subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2',
                          str(cloud_image), str(vm_disk)], check=True)
            subprocess.run(['qemu-img', 'resize', str(vm_disk), '20G'], check=True)

            # Create start script
            start_script = f"""#!/bin/bash

# Check for QEMU firmware
if [ ! -f "/usr/share/qemu/edk2-aarch64-code.fd" ]; then
    echo "QEMU firmware not found: /usr/share/qemu/edk2-aarch64-code.fd"
    exit 1
fi

# QEMU binary path
QEMU_PATH="/usr/bin/qemu-system-aarch64"

# Run QEMU
exec $QEMU_PATH \\
    -name {name} \\
    -machine virt \\
    -cpu cortex-a72 \\
    -smp {vcpus} \\
    -m {memory_mb} \\
    -bios /usr/share/qemu/edk2-aarch64-code.fd \\
    -device virtio-gpu-pci \\
    -device virtio-net-pci,netdev=net0 \\
    -netdev user,id=net0,hostfwd=tcp::2222-:22 \\
    -drive file={vm_disk},if=virtio,format=qcow2 \\
    -drive file={name}-cloud-init.iso,if=virtio,format=raw \\
    -nographic
"""
            start_script_path = vm_path / "start-vm.sh"
            start_script_path.write_text(start_script)
            start_script_path.chmod(0o755)

            self.log(f"VM '{name}' created successfully")
            
        except Exception as e:
            logger.error(f"Failed to create VM: {e}")
            raise

    def start_vm(self, name: str) -> None:
        """Start a VM."""
        try:
            vm_path = self.vm_dir / name
            if not vm_path.exists():
                raise ValueError(f"VM '{name}' not found")

            subprocess.run([str(vm_path / "start-vm.sh")], check=True)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start VM: {e}")
            raise

    def stop_vm(self, name: str) -> None:
        """Stop a VM."""
        try:
            # Find the QEMU process
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if f'-name {name}' in line:
                    pid = line.split()[1]
                    subprocess.run(['kill', '-TERM', pid], check=True)
                    self.log(f"VM '{name}' stopped")
                    return
            
            self.log(f"VM '{name}' not running")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to stop VM: {e}")
            raise

    def delete_vm(self, name: str) -> None:
        """Delete a VM."""
        try:
            vm_path = self.vm_dir / name
            if not vm_path.exists():
                raise ValueError(f"VM '{name}' not found")

            # Stop the VM if running
            self.stop_vm(name)

            # Remove VM directory
            shutil.rmtree(vm_path)
            self.log(f"VM '{name}' deleted")
            
        except Exception as e:
            logger.error(f"Failed to delete VM: {e}")
            raise

    def list_vms(self) -> List[str]:
        """List all VMs."""
        return [d.name for d in self.vm_dir.iterdir() if d.is_dir()]

def main():
    logging.basicConfig(level=logging.INFO)
    
    vm_dir = Path("vms")
    vm_manager = VMManager(vm_dir)
    
    if len(sys.argv) < 2:
        print("Usage: python main.py <command> [args...]")
        sys.exit(1)
    
    command = sys.argv[1]
    
    try:
        if command == "setup":
            vm_manager.setup()
        elif command == "create":
            if len(sys.argv) < 3:
                print("Usage: python main.py create <name>")
                sys.exit(1)
            vm_manager.create_vm(sys.argv[2])
        elif command == "start":
            if len(sys.argv) < 3:
                print("Usage: python main.py start <name>")
                sys.exit(1)
            vm_manager.start_vm(sys.argv[2])
        elif command == "stop":
            if len(sys.argv) < 3:
                print("Usage: python main.py stop <name>")
                sys.exit(1)
            vm_manager.stop_vm(sys.argv[2])
        elif command == "delete":
            if len(sys.argv) < 3:
                print("Usage: python main.py delete <name>")
                sys.exit(1)
            vm_manager.delete_vm(sys.argv[2])
        elif command == "list":
            for vm in vm_manager.list_vms():
                print(vm)
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()