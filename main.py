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
        self.vpc_manager = VPCManager()
        self.vm_metadata_file = self.vm_dir / "vm_metadata.json"
        self.vm_metadata: Dict = self._load_vm_metadata()

    def _load_vm_metadata(self) -> Dict:
        if self.vm_metadata_file.exists():
            return json.loads(self.vm_metadata_file.read_text())
        return {}

    def _save_vm_metadata(self):
        self.vm_metadata_file.write_text(json.dumps(self.vm_metadata, indent=2))

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

        if img_file.exists() and not force:
            self.log(f"Ubuntu image already exists at {img_file}")
            return

        if force:
            self.log("Force download enabled. Removing existing files...")
            img_file.unlink(missing_ok=True)

        url = "https://cloud-images.ubuntu.com/releases/focal/release/ubuntu-20.04-server-cloudimg-arm64.img"
        
        print(f"Downloading Ubuntu Cloud Image from: {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(img_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        self.log("Ubuntu 20.04 ARM64 Cloud Image downloaded")

    def create_snapshot(self):
        img_file = self.vm_dir / "ubuntu-20.04-server-cloudimg-arm64.img"
        qcow2_file = self.vm_dir / "ubuntu-20.04-server.qcow2"
        subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', 
                       str(img_file), str(qcow2_file)], check=True)
        subprocess.run(['qemu-img', 'resize', str(qcow2_file), '20G'], check=True)

        self.log("Ubuntu 20.04 ARM64 Cloud Image Snapshot prepared")

    def create_cloud_init_config(self, vm_name: str, vpc_name: str) -> None:
        cloud_init_dir = self.vm_dir / "cloud-init" / vm_name
        cloud_init_dir.mkdir(parents=True, exist_ok=True)

        vpc = self.vpc_manager.get_vpc(vpc_name)
        if not vpc:
            self.error(f"VPC {vpc_name} does not exist")

        ip_config = vpc.allocate_ip()
        
        # Store VM metadata
        self.vm_metadata[vm_name] = {
            "vpc": vpc_name,
            "private_ip": ip_config["private_ip"],
            "public_ip": ip_config["public_ip"]
        }
        self._save_vm_metadata()

        ssh_key_path = self.home_dir / '.ssh' / 'id_rsa.pub'
        if not ssh_key_path.exists():
            subprocess.run(['ssh-keygen', '-t', 'rsa', '-N', '', '-f', 
                          str(ssh_key_path).replace('.pub', '')], check=True)

        ssh_key = ssh_key_path.read_text().strip()

        user_data = f"""#cloud-config
hostname: {vm_name}
fqdn: {vm_name}.local

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
        
        meta_data = f"""instance-id: {vm_name}
local-hostname: {vm_name}"""
        (cloud_init_dir / "meta-data").write_text(meta_data)

        network_config = f"""version: 2
ethernets:
  eth0:
    addresses:
      - {ip_config["private_ip"]}/24
    dhcp4: false"""
        (cloud_init_dir / "network-config").write_text(network_config)

        subprocess.run(['mkisofs', '-output', str(self.vm_dir / f'{vm_name}-cloud-init.iso'),
                       '-volid', 'cidata', '-joliet', '-rock', '-input-charset', 'utf-8',
                       str(cloud_init_dir / 'user-data'),
                       str(cloud_init_dir / 'meta-data'),
                       str(cloud_init_dir / 'network-config')], check=True)

    def _find_free_port(self, start_port: int = 2222) -> int:
        """Find a free port starting from start_port"""
        import socket
        import psutil
        
        # Get all used ports
        used_ports = set()
        for conn in psutil.net_connections():
            used_ports.add(conn.laddr.port)
        
        # Also check VM metadata for reserved ports
        metadata = self._load_vm_metadata()
        for vm_data in metadata.values():
            if 'ssh_port' in vm_data:
                used_ports.add(int(vm_data['ssh_port']))
        
        # Find first available port
        port = start_port
        while port < 65535:
            if port not in used_ports:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.bind(('127.0.0.1', port))
                        return port
                except OSError:
                    pass
            port += 1
        raise Exception("No free ports available")

    def create_start_script(self, vm_name: str) -> None:
        start_script = self.vm_dir / f"start-{vm_name}.sh"
        
        # Find a free port for SSH
        ssh_port = self._find_free_port()
        
        # Store the port in metadata
        metadata = self._load_vm_metadata()
        if vm_name in metadata:
            metadata[vm_name]['ssh_port'] = ssh_port
            self._save_vm_metadata()
        
        script_content = f"""#!/bin/bash

VM_PATH="{self.vm_dir}"
QEMU_PATH="/opt/homebrew/bin/qemu-system-aarch64"

if [ ! -f "$VM_PATH/{vm_name}.qcow2" ]; then
    echo "VM disk image not found: $VM_PATH/{vm_name}.qcow2"
    exit 1
fi

if [ ! -f "$VM_PATH/{vm_name}-cloud-init.iso" ]; then
    echo "Cloud-init ISO not found: $VM_PATH/{vm_name}-cloud-init.iso"
    exit 1
fi

if [ ! -f "/opt/homebrew/share/qemu/edk2-aarch64-code.fd" ]; then
    echo "QEMU firmware not found: /opt/homebrew/share/qemu/edk2-aarch64-code.fd"
    exit 1
fi

# Try to bind to the port to make sure it's free
if ! nc -z localhost {ssh_port} 2>/dev/null; then
    exec $QEMU_PATH \\
        -M virt,highmem=off \\
        -accel hvf \\
        -cpu host \\
        -smp 2 \\
        -m 2048 \\
        -bios /opt/homebrew/share/qemu/edk2-aarch64-code.fd \\
        -drive file="$VM_PATH/{vm_name}.qcow2",if=virtio,format=qcow2 \\
        -drive file="$VM_PATH/{vm_name}-cloud-init.iso",if=virtio,format=raw,media=cdrom \\
        -device virtio-net-pci,netdev=net0 \\
        -netdev user,id=net0,hostfwd=tcp::{ssh_port}-:22 \\
        -nographic \\
        -serial mon:stdio
else
    echo "Port {ssh_port} is already in use"
    exit 1
fi
"""
        start_script.write_text(script_content)
        start_script.chmod(0o755)
        self.log(f"Created start script for VM {vm_name} with SSH port {ssh_port}")

    def create_vm(self, vm_name: str, vpc_name: str) -> None:
        """Create a new VM in the specified VPC"""
        if not self.vpc_manager.get_vpc(vpc_name):
            self.error(f"VPC {vpc_name} does not exist")

        # Create VM-specific snapshot
        img_file = self.vm_dir / "ubuntu-20.04-server-cloudimg-arm64.img"
        qcow2_file = self.vm_dir / f"{vm_name}.qcow2"
        subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', 
                       str(img_file), str(qcow2_file)], check=True)
        subprocess.run(['qemu-img', 'resize', str(qcow2_file), '20G'], check=True)

        self.create_cloud_init_config(vm_name, vpc_name)
        self.create_start_script(vm_name)
        self.log(f"VM {vm_name} created in VPC {vpc_name}")

    def start_vm(self) -> None:
        print("\nStarting the VM...")
        print("The VM console will appear directly in this terminal.")
        print("To exit the VM console, use: Ctrl+A X")
        print("\nWaiting for VM to boot...\n")
        
        subprocess.run([str(self.vm_dir / "start-vm.sh")], check=True)

    def list_vms(self) -> None:
        print("\nVMs and their VPC assignments:")
        for vm_name, metadata in self.vm_metadata.items():
            print(f"\nVM: {vm_name}")
            print(f"  VPC: {metadata['vpc']}")
            print(f"  Private IP: {metadata['private_ip']}")
            print(f"  Public IP: {metadata['public_ip']}")

        print("\nRunning VMs:")
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

    # VPC commands
    vpc_parser = subparsers.add_parser('vpc', help='VPC management commands')
    vpc_subparsers = vpc_parser.add_subparsers(dest='vpc_command')
    
    vpc_create = vpc_subparsers.add_parser('create', help='Create a new VPC')
    vpc_create.add_argument('name', help='Name of the VPC')
    vpc_create.add_argument('--cidr', default="192.168.0.0/16", help='CIDR block for the VPC')
    
    vpc_list = vpc_subparsers.add_parser('list', help='List all VPCs')
    vpc_delete = vpc_subparsers.add_parser('delete', help='Delete a VPC')
    vpc_delete.add_argument('name', help='Name of the VPC to delete')

    # VM commands
    setup_parser = subparsers.add_parser('setup', help='Set up a new VM')
    setup_parser.add_argument('-f', '--force', action='store_true', 
                            help='Force download of Ubuntu image')
    setup_parser.add_argument('--name', required=True, help='Name of the VM')
    setup_parser.add_argument('--vpc', required=True, help='Name of the VPC to create the VM in')

    subparsers.add_parser('list', help='List running VMs')

    stop_parser = subparsers.add_parser('stop', help='Stop a running VM')
    stop_parser.add_argument('pid', help='PID of the VM to stop')

    args = parser.parse_args()

    vm_manager = VMManager()

    try:
        if args.command == 'vpc':
            if args.vpc_command == 'create':
                vpc = vm_manager.vpc_manager.create_vpc(args.name, args.cidr)
                print(f"Created VPC {vpc.name} with CIDR {vpc.cidr}")
            elif args.vpc_command == 'list':
                vpcs = vm_manager.vpc_manager.list_vpcs()
                if vpcs:
                    print("Available VPCs:")
                    for vpc_name in vpcs:
                        vpc = vm_manager.vpc_manager.get_vpc(vpc_name)
                        print(f"  {vpc_name} ({vpc.cidr})")
                else:
                    print("No VPCs found")
            elif args.vpc_command == 'delete':
                vm_manager.vpc_manager.delete_vpc(args.name)
                print(f"Deleted VPC {args.name}")
        elif args.command == 'setup':
            print(f"{Colors.YELLOW}Starting M1 Mac VM Setup{Colors.NC}")
            vm_manager.check_homebrew()
            vm_manager.install_packages()
            vm_manager.download_ubuntu_iso(args.force)
            vm_manager.create_vm(args.name, args.vpc)
            print(f"""{Colors.GREEN}
=============================================
🚀 VM Setup Complete! 
=============================================

Your VM {args.name} has been created in VPC {args.vpc}.

To start the VM:
{vm_manager.vm_dir}/start-{args.name}.sh

VM Configuration:
- 2 CPU cores
- 2GB RAM
- 20GB disk
- VPC: {args.vpc}
- SSH port forwarding: localhost:2222

Controls:
- Exit VM console: Ctrl+A X
- Force shutdown: Ctrl+A X, then type 'quit' and press Enter
- QEMU monitor: Ctrl+A C
""")
            vm_manager.start_vm()
        elif args.command == 'list':
            vm_manager.list_vms()
        elif args.command == 'stop':
            vm_manager.stop_vm(args.pid)
        else:
            parser.print_help()

    except Exception as e:
        print(f"{Colors.RED}Error: {str(e)}{Colors.NC}")
        sys.exit(1)

if __name__ == "__main__":
    main()