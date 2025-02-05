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
from vpc import VPCManager, VPC, VPCError
import logging
from datetime import datetime
import socket
from tqdm import tqdm

logger = logging.getLogger(__name__)

class VMError(Exception):
    """Base exception for VM-related errors"""
    pass

class Colors:
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    NC = '\033[0m'

class VMManager:
    def __init__(self):
        self.vm_dir = Path("vms")
        self.vm_dir.mkdir(parents=True, exist_ok=True)
        self.vpc_manager = VPCManager()
        self._metadata_file = self.vm_dir / "vm_metadata.json"
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load VM metadata from file"""
        try:
            if self._metadata_file.exists():
                self._metadata = json.loads(self._metadata_file.read_text())
            else:
                self._metadata = {}
        except json.JSONDecodeError:
            logger.error("Invalid metadata file format")
            self._metadata = {}
        except Exception as e:
            logger.error(f"Error loading metadata: {str(e)}")
            self._metadata = {}

    def _save_metadata(self) -> None:
        """Save VM metadata to file"""
        try:
            self._metadata_file.write_text(json.dumps(self._metadata, indent=2))
        except Exception as e:
            logger.error(f"Error saving metadata: {str(e)}")
            raise VMError(f"Failed to save metadata: {str(e)}")

    def log(self, message: str) -> None:
        print(f"{Colors.GREEN}[VMManager] {message}{Colors.NC}")

    def error(self, message: str) -> None:
        print(f"{Colors.RED}[Error] {message}{Colors.NC}")
        sys.exit(1)

    def warn(self, message: str) -> None:
        print(f"{Colors.YELLOW}[Warning] {message}{Colors.NC}")

    def check_homebrew(self) -> None:
        """Check if Homebrew is installed"""
        try:
            subprocess.run(['which', 'brew'], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            self.error("Homebrew is required. Please install it from https://brew.sh")

    def install_packages(self) -> None:
        """Install required packages via Homebrew"""
        try:
            packages = ['qemu']
            subprocess.run(['brew', 'install'] + packages, check=True)
            self.log("Required packages installed successfully")
        except subprocess.CalledProcessError as e:
            self.error(f"Failed to install packages: {e}")

    def download_ubuntu_iso(self, force: bool = False) -> None:
        """Download Ubuntu cloud image"""
        img_file = self.vm_dir / "ubuntu-cloudimg-arm64.img"
        
        if img_file.exists() and not force:
            return
            
        url = "https://cloud-images.ubuntu.com/releases/jammy/release/ubuntu-22.04-server-cloudimg-arm64.img"
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            total = int(response.headers.get('content-length', 0))
            with img_file.open('wb') as f, tqdm(
                desc="Downloading Ubuntu image",
                total=total,
                unit='iB',
                unit_scale=True
            ) as pbar:
                for data in response.iter_content(chunk_size=1024):
                    size = f.write(data)
                    pbar.update(size)
                    
            self.log("Ubuntu image downloaded successfully")
        except Exception as e:
            self.error(f"Failed to download Ubuntu image: {e}")

    def _find_free_port(self, start_port: int = 2222) -> int:
        """Find a free port starting from start_port"""
        port = start_port
        while port < 65535:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                port += 1
        raise VMError("No free ports available")

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

    def create_cloud_init_config(self, vm_name: str, vpc_name: str) -> None:
        """Create cloud-init configuration for the VM"""
        vpc = self.vpc_manager.get_vpc(vpc_name)
        if not vpc:
            raise VMError(f"VPC {vpc_name} not found")
            
        # Allocate IPs
        try:
            ips = vpc.allocate_ip()
            private_ip = ips["private_ip"]
            public_ip = ips["public_ip"]
            
            # Store IPs in metadata
            if vm_name not in self._metadata:
                self._metadata[vm_name] = {}
            self._metadata[vm_name].update({
                "private_ip": private_ip,
                "public_ip": public_ip,
                "vpc": vpc_name,
                "allocated_at": ips["allocated_at"]
            })
            self._save_metadata()
            
        except Exception as e:
            raise VMError(f"Failed to allocate IPs: {str(e)}")

        cloud_init_dir = self.vm_dir / "cloud-init" / vm_name
        cloud_init_dir.mkdir(parents=True, exist_ok=True)

        # Create meta-data
        meta_data = f"""instance-id: {vm_name}
local-hostname: {vm_name}
network-interfaces: |
  auto eth0
  iface eth0 inet static
  address {private_ip}
  netmask 255.255.255.0
  gateway {vpc.network[1]}
  dns-nameservers 8.8.8.8 8.8.4.4
"""
        (cloud_init_dir / "meta-data").write_text(meta_data)

        # Create user-data with improved networking
        user_data = f"""#cloud-config
packages:
  - net-tools
  - iproute2
  - iptables
  - netcat

write_files:
- path: /etc/netplan/50-cloud-init.yaml
  content: |
    network:
      version: 2
      ethernets:
        eth0:
          addresses: [{private_ip}/24]
          gateway4: {vpc.network[1]}
          nameservers:
            addresses: [8.8.8.8, 8.8.4.4]
          routes:
            - to: {vpc.network}
              via: {vpc.network[1]}

runcmd:
  - netplan apply
  - iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
  - echo 1 > /proc/sys/net/ipv4/ip_forward
"""
        (cloud_init_dir / "user-data").write_text(user_data)

        try:
            # Create cloud-init ISO
            subprocess.run([
                'mkisofs',
                '-output', str(self.vm_dir / f"{vm_name}-cloud-init.iso"),
                '-volid', 'cidata',
                '-joliet',
                '-rock',
                str(cloud_init_dir / "user-data"),
                str(cloud_init_dir / "meta-data")
            ], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise VMError(f"Failed to create cloud-init ISO: {e.stderr.decode()}")

    def create_vm(self, vm_name: str, vpc_name: str) -> None:
        """Create a new VM in the specified VPC"""
        if not vm_name or not vpc_name:
            raise VMError("VM name and VPC name are required")
            
        if not self.vpc_manager.get_vpc(vpc_name):
            raise VMError(f"VPC {vpc_name} does not exist")
            
        # Check if VM already exists
        if vm_name in self._metadata:
            raise VMError(f"VM {vm_name} already exists")

        try:
            # Create VM-specific snapshot
            img_file = self.vm_dir / "ubuntu-cloudimg-arm64.img"
            qcow2_file = self.vm_dir / f"{vm_name}.qcow2"
            
            if not img_file.exists():
                raise VMError("Base Ubuntu image not found. Run setup with --force to download it.")
                
            # Create VM disk
            subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', 
                        str(img_file), str(qcow2_file)], check=True, capture_output=True)
            subprocess.run(['qemu-img', 'resize', str(qcow2_file), '20G'], check=True, capture_output=True)

            # Initialize metadata
            self._metadata[vm_name] = {
                "created_at": datetime.now().isoformat(),
                "status": "created",
                "vpc": vpc_name
            }
            self._save_metadata()

            # Create cloud-init config
            self.create_cloud_init_config(vm_name, vpc_name)

            self.log(f"VM {vm_name} created successfully in VPC {vpc_name}")
            
        except subprocess.CalledProcessError as e:
            self.cleanup_failed_vm(vm_name)
            raise VMError(f"Failed to create VM: {e.stderr.decode()}")
        except Exception as e:
            self.cleanup_failed_vm(vm_name)
            raise VMError(f"Unexpected error while creating VM: {str(e)}")

    def cleanup_failed_vm(self, vm_name: str) -> None:
        """Clean up resources when VM creation fails"""
        try:
            # Remove VM files
            qcow2_file = self.vm_dir / f"{vm_name}.qcow2"
            if qcow2_file.exists():
                qcow2_file.unlink()

            cloud_init_dir = self.vm_dir / "cloud-init" / vm_name
            if cloud_init_dir.exists():
                shutil.rmtree(cloud_init_dir)

            cloud_init_iso = self.vm_dir / f"{vm_name}-cloud-init.iso"
            if cloud_init_iso.exists():
                cloud_init_iso.unlink()

            # Release IPs if allocated
            if vm_name in self._metadata:
                vpc_name = self._metadata[vm_name].get("vpc")
                if vpc_name:
                    vpc = self.vpc_manager.get_vpc(vpc_name)
                    if vpc:
                        vpc.release_ip(
                            self._metadata[vm_name].get("private_ip"),
                            self._metadata[vm_name].get("public_ip")
                        )
                del self._metadata[vm_name]
                self._save_metadata()

        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

    def get_vm_status(self, name: str) -> Optional[Dict]:
        """Get current status of a VM"""
        if name not in self._metadata:
            return None
            
        vm_data = self._metadata[name]
        qcow2_file = self.vm_dir / f"{name}.qcow2"
        
        if not qcow2_file.exists():
            vm_data["status"] = "deleted"
            self._save_metadata()
            return vm_data
            
        # Try to get process info
        try:
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            vm_data["status"] = "running" if f'-name {name}' in result.stdout else "stopped"
        except subprocess.CalledProcessError:
            vm_data["status"] = "unknown"
            
        return vm_data

    def start_vm(self, name: str) -> None:
        """Start a VM"""
        vm_data = self.get_vm_status(name)
        if not vm_data:
            raise VMError(f"VM '{name}' not found")
            
        if vm_data["status"] == "running":
            self.warn(f"VM '{name}' is already running")
            return
            
        try:
            qcow2_file = self.vm_dir / f"{name}.qcow2"
            cloud_init_iso = self.vm_dir / f"{name}-cloud-init.iso"
            
            if not all([qcow2_file.exists(), cloud_init_iso.exists()]):
                raise VMError(f"VM '{name}' files are missing")

            # Find a free port for SSH
            ssh_port = self._find_free_port()

            # Start the VM
            cmd = [
                'qemu-system-aarch64',
                '-name', name,
                '-m', '2048',
                '-smp', '2',
                '-drive', f'file={qcow2_file},if=virtio',
                '-drive', f'file={cloud_init_iso},if=virtio',
                '-net', 'nic,model=virtio',
                '-net', f'user,hostfwd=tcp::{ssh_port}-:22',
                '-nographic'
            ]
            
            # Start in background
            subprocess.Popen(cmd, 
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            
            # Update metadata
            self._metadata[name]["ssh_port"] = ssh_port
            self._metadata[name]["last_started"] = datetime.now().isoformat()
            self._save_metadata()
            
            self.log(f"VM '{name}' started. SSH available on port {ssh_port}")
            
        except Exception as e:
            raise VMError(f"Failed to start VM: {str(e)}")

    def stop_vm(self, name: str, force: bool = False) -> None:
        """Stop a VM"""
        vm_data = self.get_vm_status(name)
        if not vm_data:
            raise VMError(f"VM '{name}' not found")
            
        if vm_data["status"] != "running":
            self.warn(f"VM '{name}' is not running")
            return
            
        try:
            # Find the QEMU process
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if f'-name {name}' in line:
                    pid = line.split()[1]
                    signal = 9 if force else 15  # SIGKILL if force, else SIGTERM
                    subprocess.run(['kill', f'-{signal}', pid], check=True)
                    
                    # Update metadata
                    self._metadata[name]["last_stopped"] = datetime.now().isoformat()
                    self._save_metadata()
                    
                    self.log(f"VM '{name}' stopped")
                    return
                    
        except subprocess.CalledProcessError as e:
            raise VMError(f"Failed to stop VM: {str(e)}")

    def delete_vm(self, name: str) -> None:
        """Delete a VM"""
        vm_data = self.get_vm_status(name)
        if not vm_data:
            raise VMError(f"VM '{name}' not found")
            
        try:
            # Stop the VM if running
            if vm_data["status"] == "running":
                self.stop_vm(name, force=True)

            # Clean up resources
            self.cleanup_failed_vm(name)
            self.log(f"VM '{name}' deleted")
            
        except Exception as e:
            raise VMError(f"Failed to delete VM: {str(e)}")

    def list_vms(self) -> List[Dict]:
        """List all VMs with their status"""
        vm_list = []
        for name in self._metadata:
            status = self.get_vm_status(name)
            if status:
                vm_list.append({
                    "name": name,
                    **status
                })
        return vm_list

def main():
    logging.basicConfig(level=logging.INFO)
    
    parser = argparse.ArgumentParser(description="VM Management Tool")
    parser.add_argument("command", choices=["setup", "create", "start", "stop", "delete", "list"],
                      help="Command to execute")
    parser.add_argument("name", nargs="?", help="VM name")
    parser.add_argument("--vpc", help="VPC name for create command")
    parser.add_argument("--force", action="store_true", help="Force operation")
    
    args = parser.parse_args()
    
    vm_manager = VMManager()
    
    try:
        if args.command == "setup":
            vm_manager.setup()
        elif args.command == "create":
            if not args.name or not args.vpc:
                parser.error("create command requires --vpc option")
            vm_manager.create_vm(args.name, args.vpc)
        elif args.command == "start":
            if not args.name:
                parser.error("start command requires VM name")
            vm_manager.start_vm(args.name)
        elif args.command == "stop":
            if not args.name:
                parser.error("stop command requires VM name")
            vm_manager.stop_vm(args.name, args.force)
        elif args.command == "delete":
            if not args.name:
                parser.error("delete command requires VM name")
            vm_manager.delete_vm(args.name)
        elif args.command == "list":
            for vm in vm_manager.list_vms():
                print(f"{vm['name']}: {vm['status']}")
                if "private_ip" in vm:
                    print(f"  Private IP: {vm['private_ip']}")
                if "public_ip" in vm:
                    print(f"  Public IP: {vm['public_ip']}")
                if "ssh_port" in vm:
                    print(f"  SSH Port: {vm['ssh_port']}")
    except (VMError, VPCError) as e:
        vm_manager.error(str(e))
    except Exception as e:
        vm_manager.error(f"Unexpected error: {str(e)}")

if __name__ == "__main__":
    main()