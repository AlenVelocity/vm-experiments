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
        img_file = self.vm_dir / "ubuntu-cloudimg-arm64.img"

        if img_file.exists() and not force:
            self.log(f"Ubuntu image already exists at {img_file}")
            return

        if force:
            self.log("Force download enabled. Removing existing files...")
            img_file.unlink(missing_ok=True)

        url = "https://cloud-images.ubuntu.com/current/ubuntu-current-cloudimg-arm64.img"
        
        print(f"Downloading Ubuntu Cloud Image from: {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(img_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        self.log("Latest Ubuntu ARM64 Cloud Image downloaded")

    def create_snapshot(self):
        img_file = self.vm_dir / "ubuntu-cloudimg-arm64.img"
        qcow2_file = self.vm_dir / "ubuntu-server.qcow2"
        subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', 
                       str(img_file), str(qcow2_file)], check=True)
        subprocess.run(['qemu-img', 'resize', str(qcow2_file), '20G'], check=True)

        self.log("Ubuntu ARM64 Cloud Image Snapshot prepared")

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
            "public_ip": ip_config["public_ip"],
            "gateway": ip_config["gateway"],
            "netmask": ip_config["netmask"]
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
  - net-tools
  - iproute2

write_files:
  - path: /etc/netplan/50-cloud-init.yaml
    content: |
      network:
        version: 2
        ethernets:
          eth0:
            addresses:
              - {ip_config["private_ip"]}/{ip_config["netmask"]}
            routes:
              - to: 0.0.0.0/0
                via: {ip_config["gateway"]}
            nameservers:
              addresses: [8.8.8.8, 8.8.4.4]

runcmd:
  - netplan apply
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
      - {ip_config["private_ip"]}/{ip_config["netmask"]}
    routes:
      - to: 0.0.0.0/0
        via: {ip_config["gateway"]}
    nameservers:
      addresses: [8.8.8.8, 8.8.4.4]"""
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
        vnc_port = self._find_free_port(start_port=5900)
        
        # Get VM metadata
        metadata = self._load_vm_metadata()
        vm_data = metadata.get(vm_name, {})
        
        # Store the ports in metadata
        if vm_name in metadata:
            metadata[vm_name].update({
                'ssh_port': ssh_port,
                'vnc_port': vnc_port
            })
            self._save_vm_metadata()
        
        script_content = f"""#!/bin/bash

VM_PATH="{self.vm_dir}"
QEMU_PATH="/opt/homebrew/bin/qemu-system-aarch64"

# Check if VM is already running
if pgrep -f "qemu.*{vm_name}.qcow2" > /dev/null; then
    echo "VM {vm_name} is already running"
    exit 1
fi

# Check required files
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

# Create tap device if it doesn't exist
if ! ip link show tap0 >/dev/null 2>&1; then
    sudo ip tuntap add tap0 mode tap user $USER
    sudo ip link set tap0 up
fi

# Try to bind to the ports
if ! nc -z localhost {ssh_port} 2>/dev/null && ! nc -z localhost {vnc_port} 2>/dev/null; then
    exec $QEMU_PATH \\
        -M virt,highmem=on \\
        -accel hvf \\
        -cpu host \\
        -smp 4 \\
        -m 4G \\
        -bios /opt/homebrew/share/qemu/edk2-aarch64-code.fd \\
        -drive file="$VM_PATH/{vm_name}.qcow2",if=virtio,format=qcow2,cache=writethrough \\
        -drive file="$VM_PATH/{vm_name}-cloud-init.iso",if=virtio,format=raw,media=cdrom \\
        -device virtio-net-pci,netdev=net0 \\
        -netdev user,id=net0,hostfwd=tcp::{ssh_port}-:22 \\
        -device virtio-rng-pci \\
        -vnc :{vnc_port - 5900} \\
        -monitor telnet:127.0.0.1:{vnc_port + 1},server,nowait \\
        -nographic \\
        -serial mon:stdio
else
    echo "Port {ssh_port} or {vnc_port} is already in use"
    exit 1
fi
"""
        start_script.write_text(script_content)
        start_script.chmod(0o755)
        self.log(f"Created start script for VM {vm_name} with SSH port {ssh_port} and VNC port {vnc_port}")

    def create_vm(self, vm_name: str, vpc_name: str) -> None:
        """Create a new VM in the specified VPC"""
        if not vm_name or not vpc_name:
            self.error("VM name and VPC name are required")
            
        if not self.vpc_manager.get_vpc(vpc_name):
            self.error(f"VPC {vpc_name} does not exist")
            
        # Check if VM already exists
        if vm_name in self._load_vm_metadata():
            self.error(f"VM {vm_name} already exists")

        try:
            # Create VM-specific snapshot
            img_file = self.vm_dir / "ubuntu-cloudimg-arm64.img"
            qcow2_file = self.vm_dir / f"{vm_name}.qcow2"
            
            if not img_file.exists():
                self.error("Base Ubuntu image not found. Run setup with --force to download it.")
                
            # Create VM disk
            subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', 
                        str(img_file), str(qcow2_file)], check=True)
            subprocess.run(['qemu-img', 'resize', str(qcow2_file), '20G'], check=True)

            # Create cloud-init config and start script
            self.create_cloud_init_config(vm_name, vpc_name)
            self.create_start_script(vm_name)
            
            # Add VM info to metadata
            metadata = self._load_vm_metadata()
            if vm_name not in metadata:
                metadata[vm_name] = {
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "created",
                    "vpc": vpc_name
                }
                self._save_vm_metadata()

            self.log(f"VM {vm_name} created successfully in VPC {vpc_name}")
            
        except subprocess.CalledProcessError as e:
            self.cleanup_failed_vm(vm_name)
            self.error(f"Failed to create VM: {str(e)}")
        except Exception as e:
            self.cleanup_failed_vm(vm_name)
            self.error(f"Unexpected error while creating VM: {str(e)}")

    def cleanup_failed_vm(self, vm_name: str) -> None:
        """Clean up resources if VM creation fails"""
        try:
            # Remove VM disk
            qcow2_file = self.vm_dir / f"{vm_name}.qcow2"
            qcow2_file.unlink(missing_ok=True)
            
            # Remove cloud-init files
            cloud_init_iso = self.vm_dir / f"{vm_name}-cloud-init.iso"
            cloud_init_iso.unlink(missing_ok=True)
            
            cloud_init_dir = self.vm_dir / "cloud-init" / vm_name
            if cloud_init_dir.exists():
                shutil.rmtree(cloud_init_dir)
            
            # Remove start script
            start_script = self.vm_dir / f"start-{vm_name}.sh"
            start_script.unlink(missing_ok=True)
            
            # Remove from metadata
            metadata = self._load_vm_metadata()
            if vm_name in metadata:
                vpc_name = metadata[vm_name].get("vpc")
                if vpc_name:
                    # Release IPs back to VPC
                    vpc = self.vpc_manager.get_vpc(vpc_name)
                    if vpc:
                        vpc.release_ip(
                            metadata[vm_name].get("private_ip", ""),
                            metadata[vm_name].get("public_ip", "")
                        )
                metadata.pop(vm_name)
                self._save_vm_metadata()
                
        except Exception as e:
            print(f"Warning: Error during cleanup: {str(e)}")

    def start_vm(self) -> None:
        print("\nStarting the VM...")
        print("The VM console will appear directly in this terminal.")
        print("To exit the VM console, use: Ctrl+A X")
        print("\nWaiting for VM to boot...\n")
        
        subprocess.run([str(self.vm_dir / "start-vm.sh")], check=True)

    def list_vms(self) -> None:
        """List all VMs and their status"""
        metadata = self._load_vm_metadata()
        running_vms = self._get_running_vms()

        print("\nVM Status:")
        print("=" * 50)
        
        for vm_name, vm_data in metadata.items():
            status = "Stopped"
            ports = []
            
            # Check if VM is running
            if vm_name in running_vms:
                status = "Running"
                pid = running_vms[vm_name]["pid"]
                ports = [
                    f"SSH: localhost:{vm_data.get('ssh_port', 'N/A')}",
                    f"VNC: localhost:{vm_data.get('vnc_port', 'N/A')}"
                ]
                print(f"\nVM: {vm_name} (PID: {pid})")
            else:
                print(f"\nVM: {vm_name}")
                
            print(f"Status: {status}")
            print(f"VPC: {vm_data.get('vpc', 'N/A')}")
            print(f"Private IP: {vm_data.get('private_ip', 'N/A')}")
            print(f"Public IP: {vm_data.get('public_ip', 'N/A')}")
            if ports:
                print("Ports:")
                for port in ports:
                    print(f"  - {port}")

    def _get_running_vms(self) -> Dict[str, Dict]:
        """Get dictionary of running VMs with their PIDs"""
        running_vms = {}
        try:
            result = subprocess.run(
                ['pgrep', '-fa', 'qemu-system-aarch64'], 
                capture_output=True, 
                text=True
            )
            
            for line in result.stdout.splitlines():
                for vm_name in self._load_vm_metadata().keys():
                    if f"{vm_name}.qcow2" in line:
                        pid = line.split()[0]
                        running_vms[vm_name] = {
                            "pid": pid,
                            "command": line
                        }
        except subprocess.CalledProcessError:
            pass
        return running_vms

    def stop_vm(self, vm_name: str, force: bool = False) -> None:
        """Stop a running VM"""
        running_vms = self._get_running_vms()
        
        if vm_name not in running_vms:
            self.error(f"VM {vm_name} is not running")
            
        pid = running_vms[vm_name]["pid"]
        try:
            if force:
                subprocess.run(['kill', '-9', pid], check=True)
                self.log(f"Force stopped VM {vm_name} (PID: {pid})")
            else:
                subprocess.run(['kill', '-TERM', pid], check=True)
                self.log(f"Gracefully stopping VM {vm_name} (PID: {pid})")
                
                # Wait for VM to stop
                for _ in range(30):  # Wait up to 30 seconds
                    if not self._is_process_running(pid):
                        break
                    time.sleep(1)
                else:
                    self.log(f"VM {vm_name} is taking too long to stop. Use --force to force stop.")
        except subprocess.CalledProcessError as e:
            self.error(f"Failed to stop VM {vm_name}: {str(e)}")

    def _is_process_running(self, pid: str) -> bool:
        """Check if a process is running"""
        try:
            subprocess.run(['ps', '-p', pid], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

def main():
    parser = argparse.ArgumentParser(description='M1 Mac VM Manager')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # VPC commands
    vpc_parser = subparsers.add_parser('vpc', help='VPC management commands')
    vpc_subparsers = vpc_parser.add_subparsers(dest='vpc_command')
    
    vpc_create = vpc_subparsers.add_parser('create', help='Create a new VPC')
    vpc_create.add_argument('name', help='Name of the VPC')
    vpc_create.add_argument('--cidr', default="10.0.0.0/24", help='CIDR block for the VPC')
    
    vpc_list = vpc_subparsers.add_parser('list', help='List all VPCs')
    vpc_delete = vpc_subparsers.add_parser('delete', help='Delete a VPC')
    vpc_delete.add_argument('name', help='Name of the VPC to delete')

    # VM commands
    vm_parser = subparsers.add_parser('vm', help='VM management commands')
    vm_subparsers = vm_parser.add_subparsers(dest='vm_command')

    # Create VM command
    vm_create = vm_subparsers.add_parser('create', help='Create a new VM')
    vm_create.add_argument('--name', required=True, help='Name of the VM')
    vm_create.add_argument('--vpc', required=True, help='Name of the VPC')
    vm_create.add_argument('-f', '--force', action='store_true', 
                          help='Force download of Ubuntu image')

    # List VMs command
    vm_subparsers.add_parser('list', help='List all VMs')

    # Start VM command
    vm_start = vm_subparsers.add_parser('start', help='Start a VM')
    vm_start.add_argument('name', help='Name of the VM to start')

    # Stop VM command
    vm_stop = vm_subparsers.add_parser('stop', help='Stop a VM')
    vm_stop.add_argument('name', help='Name of the VM to stop')
    vm_stop.add_argument('-f', '--force', action='store_true',
                        help='Force stop the VM')

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
        elif args.command == 'vm':
            if args.vm_command == 'create':
                print(f"{Colors.YELLOW}Creating new VM...{Colors.NC}")
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
- 4 CPU cores
- 4GB RAM
- 20GB disk
- VPC: {args.vpc}

For SSH access:
- Check VM status for SSH port: ./vm-manager.py vm list
- Then connect using: ssh ubuntu@localhost -p <ssh_port>

For VNC access:
- Check VM status for VNC port
- Use any VNC client to connect to localhost:<vnc_port>

Controls:
- Exit VM console: Ctrl+A X
- Force shutdown: Ctrl+A X, then type 'quit' and press Enter
- QEMU monitor: Ctrl+A C
""")
            elif args.vm_command == 'list':
                vm_manager.list_vms()
            elif args.vm_command == 'start':
                script_path = vm_manager.vm_dir / f"start-{args.name}.sh"
                if not script_path.exists():
                    vm_manager.error(f"Start script not found for VM {args.name}")
                subprocess.run([str(script_path)], check=True)
            elif args.vm_command == 'stop':
                vm_manager.stop_vm(args.name, args.force)
            else:
                parser.print_help()
        else:
            parser.print_help()

    except Exception as e:
        print(f"{Colors.RED}Error: {str(e)}{Colors.NC}")
        sys.exit(1)

if __name__ == "__main__":
    main()