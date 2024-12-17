#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
import shutil
import requests
from typing import Optional, List

class Colors:
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    NC = '\033[0m'

class VMManager:
    def __init__(self):
        self.home_dir = Path.home()
        self.vm_dir = Path("vms")
        self.vm_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        print(f"{Colors.GREEN}[✓]{Colors.NC} {message}")

    def error(self, message: str) -> None:
        print(f"{Colors.RED}[✗] Error: {message}{Colors.NC}")
        sys.exit(1)

    def check_homebrew(self) -> None:
        if not shutil.which('brew'):
            print("Installing Homebrew...")
            subprocess.run(['/bin/bash', '-c', 
                '$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)'],
                check=True)
            
            with open(self.home_dir / '.zprofile', 'a') as f:
                f.write('\neval "$(/opt/homebrew/bin/brew shellenv)"\n')
            
            subprocess.run(['/opt/homebrew/bin/brew', 'shellenv'], check=True)
        
        self.log("Homebrew installed and configured")

    def install_packages(self) -> None:
        packages = ['qemu', 'cdrtools', 'pkg-config', 'libvirt']
        for package in packages:
            subprocess.run(['brew', 'install', package], check=True)
        self.log("Required packages installed")

    def download_ubuntu_iso(self, force: bool = False) -> None:
        img_file = self.vm_dir / "ubuntu-20.04-server-cloudimg-arm64.img"
        qcow2_file = self.vm_dir / "ubuntu-20.04-server.qcow2"

        if qcow2_file.exists() and not force:
            self.log(f"Ubuntu image already exists at {qcow2_file}")
            return

        if force:
            self.log("Force download enabled. Removing existing files...")
            img_file.unlink(missing_ok=True)
            qcow2_file.unlink(missing_ok=True)

        url = "https://cloud-images.ubuntu.com/releases/focal/release/ubuntu-20.04-server-cloudimg-arm64.img"
        
        print(f"Downloading Ubuntu Cloud Image from: {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(img_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', 
                       str(img_file), str(qcow2_file)], check=True)
        subprocess.run(['qemu-img', 'resize', str(qcow2_file), '20G'], check=True)

        img_file.unlink()
        self.log("Ubuntu 20.04 ARM64 Cloud Image downloaded and prepared")

    def create_cloud_init_config(self) -> None:
        cloud_init_dir = self.vm_dir / "cloud-init"
        cloud_init_dir.mkdir(exist_ok=True)

        ssh_key_path = self.home_dir / '.ssh' / 'id_rsa.pub'
        if not ssh_key_path.exists():
            subprocess.run(['ssh-keygen', '-t', 'rsa', '-N', '', '-f', 
                          str(ssh_key_path).replace('.pub', '')], check=True)

        ssh_key = ssh_key_path.read_text().strip()

        user_data = f"""#cloud-config
hostname: ubuntu-server
fqdn: ubuntu-server.local

ssh_pwauth: true
ssh_deletekeys: true
ssh_genkeytypes: ['rsa', 'ecdsa', 'ed25519']

users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: users, admin
    shell: /bin/bash
    lock_passwd: false
    passwd: '$1$Salt$YhgRYajLPrYevs14poKBQ0'
    ssh_authorized_keys:
      - {ssh_key}

package_update: true
package_upgrade: true

packages:
  - qemu-guest-agent
  - openssh-server
  - cloud-init

runcmd:
  - systemctl enable ssh
  - systemctl start ssh
  - systemctl enable qemu-guest-agent
  - systemctl start qemu-guest-agent

chpasswd:
  expire: false

password:
  expire: false
"""
        (cloud_init_dir / "user-data").write_text(user_data)
        
        meta_data = """instance-id: ubuntu-server-1
local-hostname: ubuntu-server"""
        (cloud_init_dir / "meta-data").write_text(meta_data)

        network_config = """version: 2
ethernets:
  eth0:
    dhcp4: true"""
        (cloud_init_dir / "network-config").write_text(network_config)

        subprocess.run(['mkisofs', '-output', str(self.vm_dir / 'cloud-init.iso'),
                       '-volid', 'cidata', '-joliet', '-rock', '-input-charset', 'utf-8',
                       str(cloud_init_dir / 'user-data'),
                       str(cloud_init_dir / 'meta-data'),
                       str(cloud_init_dir / 'network-config')], check=True)

    def create_start_script(self) -> None:
        start_script = self.vm_dir / "start-vm.sh"
        script_content = f"""#!/bin/bash

VM_PATH="{self.vm_dir}"
QEMU_PATH="/opt/homebrew/bin/qemu-system-aarch64"

$QEMU_PATH \\
    -M virt,highmem=off \\
    -accel hvf \\
    -cpu host \\
    -smp 2 \\
    -m 2048 \\
    -bios /opt/homebrew/share/qemu/edk2-aarch64-code.fd \\
    -drive file="$VM_PATH/ubuntu-20.04-server.qcow2",if=virtio,format=qcow2 \\
    -drive file="$VM_PATH/cloud-init.iso",if=virtio,format=raw,media=cdrom \\
    -device virtio-net-pci,netdev=net0 \\
    -netdev user,id=net0,hostfwd=tcp::2222-:22 \\
    -nographic \\
    -serial mon:stdio
"""
        start_script.write_text(script_content)
        start_script.chmod(0o755)

    def start_vm(self) -> None:
        print("\nStarting the VM...")
        print("The VM console will appear directly in this terminal.")
        print("To exit the VM console, use: Ctrl+A X")
        print("\nWaiting for VM to boot...\n")
        
        subprocess.run([str(self.vm_dir / "start-vm.sh")], check=True)

    def list_vms(self) -> None:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if 'qemu-system-aarch64' in line and 'grep' not in line:
                pid = line.split()[1]
                name = "unknown"
                if "ubuntu-" in line:
                    name = line[line.find("ubuntu-"):].split()[0]
                print(f"VM: {name} (PID: {pid})")
                if "hostfwd=tcp::" in line:
                    port = line[line.find("hostfwd=tcp::"):].split("-")[0].split(":")[-1]
                    print(f"  SSH available on localhost:{port}")

    def stop_vm(self, pid: str) -> None:
        try:
            subprocess.run(['kill', '-TERM', pid], check=True)
            print(f"Stopped VM with PID {pid}")
        except subprocess.CalledProcessError:
            self.error(f"Failed to stop VM with PID {pid}")

def main():
    parser = argparse.ArgumentParser(description='M1 Mac VM Manager')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    setup_parser = subparsers.add_parser('setup', help='Set up a new VM')
    setup_parser.add_argument('-f', '--force', action='store_true', 
                            help='Force download of Ubuntu image')

    subparsers.add_parser('list', help='List running VMs')

    stop_parser = subparsers.add_parser('stop', help='Stop a running VM')
    stop_parser.add_argument('pid', help='PID of the VM to stop')

    args = parser.parse_args()

    vm_manager = VMManager()

    try:
        if args.command == 'setup':
            print(f"{Colors.YELLOW}Starting M1 Mac VM Setup{Colors.NC}")
            vm_manager.check_homebrew()
            vm_manager.install_packages()
            vm_manager.download_ubuntu_iso(args.force)
            vm_manager.create_cloud_init_config()
            vm_manager.create_start_script()
            vm_manager.start_vm()
            
            print(f"""{Colors.GREEN}
=============================================
🚀 VM Setup Complete! 
=============================================

Your Ubuntu 20.04 VM has been created.

To start the VM:
{vm_manager.vm_dir}/start-vm.sh

VM Configuration:
- 2 CPU cores
- 2GB RAM
- 20GB disk
- Shared network (user)
- SSH port forwarding: localhost:2222

Controls:
- Exit VM console: Ctrl+A X
- Force shutdown: Ctrl+A X, then type 'quit' and press Enter
- QEMU monitor: Ctrl+A C
{Colors.NC}""")

        elif args.command == 'list':
            vm_manager.list_vms()
        elif args.command == 'stop':
            vm_manager.stop_vm(args.pid)
        else:
            parser.print_help()

    except Exception as e:
        vm_manager.error(str(e))

if __name__ == '__main__':
    main()