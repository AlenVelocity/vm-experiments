import os
import sys
import subprocess
import shutil
import time
import json
import logging
import ipaddress
import requests
import uuid
import socket
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
import libvirt
import xml.etree.ElementTree as ET
from .networking import NetworkManager, NetworkType
from .ip_manager import IPManager
from .disk_manager import DiskManager
from datetime import datetime
from bs4 import BeautifulSoup
import re
import traceback
from .db import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class VMConfig:
    name: str
    cpu_cores: int = 2
    memory_mb: int = 2048
    disk_size_gb: int = 20
    network_name: Optional[str] = None
    cloud_init: Optional[Dict[str, Any]] = None
    image_id: Optional[str] = None

@dataclass
class VM:
    id: str
    name: str
    config: VMConfig
    network_info: Optional[Dict] = None
    ssh_port: Optional[int] = None

class LibvirtManager:
    def __init__(self):
        try:
            self.conn = libvirt.open('qemu:///session')
            if not self.conn:
                raise Exception("Failed to open connection to qemu:///session")
            
            self.vm_dir = Path(__file__).parent.parent / "vms"
            self.vm_dir.mkdir(parents=True, exist_ok=True)
            
            self._init_storage_pool()
            
            self.network_manager = NetworkManager(self.conn)
            self.ip_manager = IPManager()
            self.disk_manager = DiskManager(self.conn)
            
            self.vms = self._load_vms()
            
            logger.info("LibvirtManager initialized successfully")
            self.ubuntu_daily_base_url = "https://cloud-images.ubuntu.com/focal/current/"
        except Exception as e:
            logger.error(f"Failed to initialize LibvirtManager: {str(e)}")
            raise

    def _load_vms(self) -> Dict[str, VM]:
        vms = {}
        stored_vms = db.list_vms()
        for vm_data in stored_vms:
            config = VMConfig(
                name=vm_data['name'],
                cpu_cores=vm_data['cpu_cores'],
                memory_mb=vm_data['memory_mb'],
                disk_size_gb=vm_data['disk_size_gb'],
                network_name=vm_data['network_name'],
                cloud_init=vm_data.get('cloud_init'),
                image_id=vm_data.get('image_id')
            )
            vm = VM(
                id=vm_data['id'],
                name=vm_data['name'],
                config=config,
                network_info=vm_data.get('network_info'),
                ssh_port=vm_data.get('ssh_port')
            )
            vms[vm.id] = vm
        return vms

    def _create_cloud_init_config(self, vm: VM) -> None:
        vm_dir = self.vm_dir / vm.name
        vm_dir.mkdir(parents=True, exist_ok=True)

        default_cloud_init = {
            'hostname': vm.name,
            'preserve_hostname': False,
            'fqdn': f"{vm.name}.local",
            'prefer_fqdn_over_hostname': True,
            'users': [{
                'name': 'ubuntu',
                'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                'shell': '/bin/bash',
                'ssh_authorized_keys': [],
                'lock_passwd': False,
                'passwd': '$6$rounds=4096$saltsalt$3wXPEh7ICVxwpDwO1YlqX2SN3UQNEo0GpG8AOO7QXOOsQnGjwZz5xPHe6F0UXR3K0jcgZOXgkPFE0ebzL4.Kj1'  # Password: ubuntu
            }],
            'package_update': True,
            'package_upgrade': True,
            'packages': [
                'qemu-guest-agent',
                'cloud-init',
                'openssh-server',
                'net-tools',
                'curl',
                'wget',
                'vim',
                'htop',
                'iftop',
                'iotop',
                'nmon',
                'sysstat'
            ],
            'apt': {
                'primary': [{
                    'arches': ['arm64', 'default'],
                    'uri': 'http://ports.ubuntu.com/ubuntu-ports/'
                }]
            },
            'write_files': [
                {
                    'path': '/etc/netplan/50-cloud-init.yaml',
                    'content': '''network:
    version: 2
    ethernets:
        enp0s1:
            dhcp4: true
            dhcp4-overrides:
                use-dns: true
                use-ntp: true
            dhcp6: false
            optional: true
''',
                    'permissions': '0644'
                },
                {
                    'path': '/etc/systemd/system/qemu-guest-agent.service.d/override.conf',
                    'content': '''[Service]
Restart=always
RestartSec=0
''',
                    'permissions': '0644'
                }
            ],
            'runcmd': [
                'systemctl daemon-reload',
                'systemctl enable qemu-guest-agent',
                'systemctl start qemu-guest-agent',
                'systemctl enable ssh',
                'systemctl start ssh',
                'netplan apply',
                'echo "ubuntu:ubuntu" | chpasswd'
            ],
            'power_state': {
                'mode': 'reboot',
                'timeout': 30,
                'condition': True
            },
            'final_message': "Cloud-init has completed. The system is ready to use."
        }

        if vm.config.cloud_init:
            self._merge_cloud_init(default_cloud_init, vm.config.cloud_init)

        meta_data = f"""instance-id: {vm.id}
local-hostname: {vm.name}
network:
  version: 2
  ethernets:
    enp0s1:
      dhcp4: true
      dhcp6: false
"""
        (vm_dir / 'meta-data').write_text(meta_data)

        user_data = "#cloud-config\n" + json.dumps(default_cloud_init, indent=2)
        (vm_dir / 'user-data').write_text(user_data)

        network_config = """version: 2
ethernets:
    enp0s1:
        dhcp4: true
        dhcp4-overrides:
            use-dns: true
            use-ntp: true
        dhcp6: false
        optional: true
"""
        (vm_dir / 'network-config').write_text(network_config)

        # Create cloud-init ISO
        subprocess.run([
            'mkisofs',
            '-output', str(self.vm_dir / f"{vm.name}-cloud-init.iso"),
            '-volid', 'cidata',
            '-joliet',
            '-rock',
            str(vm_dir / 'user-data'),
            str(vm_dir / 'meta-data'),
            str(vm_dir / 'network-config')
        ], check=True)

    def create_vm(self, config: VMConfig) -> VM:
        try:
            vm_id = str(uuid.uuid4())[:8]
            vm = VM(id=vm_id, name=config.name, config=config)
            vm.ssh_port = self._find_free_port()

            # Create VM directory and download image
            vm_dir = self.vm_dir / vm_id
            vm_dir.mkdir(parents=True, exist_ok=True)

            image_path = self._download_ubuntu_image(config.image_id or 'default')
            if not image_path:
                raise Exception("Failed to download Ubuntu image")

            # Create disk
            disk_path = vm_dir / f"{config.name}.qcow2"
            subprocess.run([
                'qemu-img', 'create',
                '-f', 'qcow2',
                '-F', 'qcow2',
                '-b', str(image_path),
                str(disk_path),
                f"{config.disk_size_gb}G"
            ], check=True)

            # Get network info
            public_ip = self.ip_manager.get_available_ip()
            if public_ip:
                self.ip_manager.attach_ip(public_ip, vm_id)

            vm_number = int(vm_id[-4:], 16) % 254
            subnet = (vm_number // 254) + 1
            host = (vm_number % 254) + 1

            vm.network_info = {
                'private': {
                    'ip': f"192.168.{subnet}.{host}",
                    'subnet_mask': "255.255.255.0",
                    'gateway': f"192.168.{subnet}.1",
                    'network_name': f"{config.network_name}-private" if config.network_name else "default-private"
                }
            }

            if public_ip:
                vm.network_info['public'] = {
                    'ip': public_ip,
                    'subnet_mask': "255.255.255.0",
                    'gateway': f"10.{subnet}.{host}.1",
                    'network_name': f"{config.network_name}-public" if config.network_name else "default-public"
                }

            # Save VM to database
            db.save_vm({
                'id': vm.id,
                'name': vm.name,
                'cpu_cores': config.cpu_cores,
                'memory_mb': config.memory_mb,
                'disk_size_gb': config.disk_size_gb,
                'network_name': config.network_name,
                'ssh_port': vm.ssh_port,
                'network_info': vm.network_info,
                'cloud_init': config.cloud_init,
                'image_id': config.image_id,
                'state': 'creating'
            })

            # Create cloud-init config and start VM
            self._create_cloud_init_config(vm)
            self._start_vm(vm)

            # Update VM state
            db.save_vm({
                'id': vm.id,
                'state': 'running'
            })

            self.vms[vm_id] = vm
            return vm

        except Exception as e:
            logger.error(f"Error creating VM: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            if 'vm_id' in locals():
                self.delete_vm(vm_id)
            raise

    def _init_storage_pool(self):
        """Initialize the default storage pool for QEMU/KVM"""
        try:
            # Try to find existing pool
            pool = self.conn.storagePoolLookupByName('default')
            if not pool.isActive():
                pool.create()
            return pool
        except libvirt.libvirtError:
            # Create pool directory in user's home directory for session mode
            pool_path = Path.home() / '.local/share/libvirt/images'
            pool_path.mkdir(parents=True, exist_ok=True)
            
            # Define pool XML
            pool_xml = f"""
            <pool type='dir'>
                <name>default</name>
                <target>
                    <path>{str(pool_path)}</path>
                    <permissions>
                        <mode>0755</mode>
                    </permissions>
                </target>
            </pool>
            """
            
            # Create the pool
            pool = self.conn.storagePoolDefineXML(pool_xml)
            if not pool:
                raise Exception("Failed to create storage pool")
            
            # Start the pool
            pool.setAutostart(True)
            pool.create()
            
            logger.info(f"Created default storage pool at {pool_path}")
            return pool

    def _find_free_port(self, start_port: int = 2222) -> int:
        port = start_port
        while port < 65535:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                port += 1
        raise Exception("No free ports available")

    def _generate_domain_xml(self, vm: VM, disk_path: Path) -> str:
        """Generate libvirt domain XML for the VM."""
        root = ET.Element('domain', type='qemu')  # Use QEMU directly for M1
        
        # Basic metadata
        ET.SubElement(root, 'name').text = vm.name
        ET.SubElement(root, 'uuid').text = vm.id
        ET.SubElement(root, 'title').text = f"VM {vm.name}"
        
        # Use aarch64 for M1
        os = ET.SubElement(root, 'os')
        ET.SubElement(os, 'type', arch='aarch64', machine='virt').text = 'hvm'
        ET.SubElement(os, 'boot', dev='hd')
        
        # Memory and CPU configuration
        memory_kb = vm.config.memory_mb * 1024
        ET.SubElement(root, 'memory', unit='KiB').text = str(memory_kb)
        ET.SubElement(root, 'currentMemory', unit='KiB').text = str(memory_kb)
        
        vcpu = ET.SubElement(root, 'vcpu', placement='static')
        vcpu.text = str(vm.config.cpu_cores)
        
        # CPU configuration for M1
        cpu = ET.SubElement(root, 'cpu', mode='host-passthrough')
        ET.SubElement(cpu, 'topology', sockets='1', cores=str(vm.config.cpu_cores), threads='1')
        
        # Features
        features = ET.SubElement(root, 'features')
        ET.SubElement(features, 'acpi')
        ET.SubElement(features, 'gic', version='3')
        
        # Devices
        devices = ET.SubElement(root, 'devices')
        
        # Emulator
        ET.SubElement(devices, 'emulator').text = '/opt/homebrew/bin/qemu-system-aarch64'
        
        # Main disk
        disk = ET.SubElement(devices, 'disk', type='file', device='disk')
        ET.SubElement(disk, 'driver', name='qemu', type='qcow2')
        ET.SubElement(disk, 'source', file=str(disk_path))
        ET.SubElement(disk, 'target', dev='vda', bus='virtio')
        
        # Cloud-init disk
        cloud_init_disk = ET.SubElement(devices, 'disk', type='file', device='cdrom')
        ET.SubElement(cloud_init_disk, 'driver', name='qemu', type='raw')
        ET.SubElement(cloud_init_disk, 'source', file=str(self.vm_dir / f"{vm.name}-cloud-init.iso"))
        ET.SubElement(cloud_init_disk, 'target', dev='sda', bus='usb')  # Use USB for CDROM on ARM64
        ET.SubElement(cloud_init_disk, 'readonly')
        
        # Add virtio-serial for console
        serial = ET.SubElement(devices, 'serial', type='pty')
        ET.SubElement(serial, 'target', type='system-serial', port='0')
        
        console = ET.SubElement(devices, 'console', type='pty')
        ET.SubElement(console, 'target', type='serial', port='0')
        
        # Add VNC display
        graphics = ET.SubElement(devices, 'graphics', type='vnc', port='-1', autoport='yes', listen='0.0.0.0')
        ET.SubElement(graphics, 'listen', type='address', address='0.0.0.0')
        
        # Add video device
        video = ET.SubElement(devices, 'video')
        ET.SubElement(video, 'model', type='virtio', heads='1')
        
        # Add network interface with SSH port forwarding
        interface = ET.SubElement(devices, 'interface', type='user')
        ET.SubElement(interface, 'model', type='virtio')
        ET.SubElement(interface, 'hostfwd', protocol='tcp', port=str(vm.ssh_port), to='22')
        
        return ET.tostring(root, encoding='unicode', pretty_print=True)

    def _merge_cloud_init(self, base: dict, custom: dict) -> None:
        """Recursively merge custom cloud-init config into base config."""
        for key, value in custom.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_cloud_init(base[key], value)
            elif key in base and isinstance(base[key], list) and isinstance(value, list):
                base[key].extend(value)
            else:
                base[key] = value

    def _download_ubuntu_image(self, image_id: str) -> Optional[Path]:
        """Download Ubuntu daily image."""
        try:
            # Create images directory if it doesn't exist
            images_dir = self.vm_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            
            # Construct image URL
            image_url = f"{self.ubuntu_daily_base_url}{image_id}/focal-server-cloudimg-arm64.img"
            image_path = images_dir / f"ubuntu-{image_id}.img"
            
            # Download if not already exists
            if not image_path.exists():
                logger.info(f"Downloading Ubuntu image from {image_url}")
                response = requests.get(image_url, stream=True)
                response.raise_for_status()
                
                with open(image_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        
            return image_path
        except Exception as e:
            logger.error(f"Error downloading Ubuntu image: {e}")
            return None

    def _start_vm(self, vm: VM) -> None:
        """Start the VM using libvirt."""
        try:
            # Get the storage pool
            pool = self.conn.storagePoolLookupByName('default')
            if not pool.isActive():
                pool.create()

            # Get the disk path
            disk_path = Path(pool.getInfo()[0]) / f"{vm.name}.qcow2"

            # Generate the domain XML
            domain_xml = self._generate_domain_xml(vm, disk_path)

            # Create and start the domain
            domain = self.conn.defineXML(domain_xml)
            if not domain:
                raise Exception("Failed to define domain")

            domain.create()
            logger.info(f"Started VM {vm.name}")
        except Exception as e:
            raise Exception(f"Failed to start VM: {str(e)}")

    def _fetch_available_ubuntu_images(self) -> List[dict]:
        """Fetch available daily Ubuntu 20.04 images."""
        try:
            response = requests.get(self.ubuntu_daily_base_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            images = []
            
            # Look for directories with date pattern
            for link in soup.find_all('a'):
                href = link.get('href', '')
                if re.match(r'\d{8}/', href):  # Match pattern like "20241216/"
                    date_str = href.rstrip('/')
                    try:
                        date = datetime.strptime(date_str, '%Y%m%d')
                        images.append({
                            'id': date_str,
                            'date': date.strftime('%Y-%m-%d'),
                            'url': f"{self.ubuntu_daily_base_url}{href}",
                            'name': f"Ubuntu 20.04 LTS ({date.strftime('%Y-%m-%d')})"
                        })
                    except ValueError:
                        continue
            
            # Sort by date, most recent first
            return sorted(images, key=lambda x: x['date'], reverse=True)
        except Exception as e:
            logger.error(f"Error fetching Ubuntu images: {e}")
            return []
            
    def list_available_images(self) -> List[dict]:
        """List all available VM images including daily Ubuntu builds."""
        # Add default Ubuntu 20.04 ARM64 image
        images = [{
            'id': 'default',
            'date': 'current',
            'url': 'https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-arm64.img',
            'name': 'Ubuntu 20.04 LTS (Default ARM64)'
        }]
        
        # Add daily Ubuntu images
        ubuntu_images = self._fetch_available_ubuntu_images()
        images.extend(ubuntu_images)
        
        return images

    def delete_vm(self, vm_id: str) -> None:
        try:
            vm = self.vms.get(vm_id)
            if not vm:
                return

            # Stop VM if running
            try:
                domain = self.conn.lookupByName(vm.name)
                if domain.isActive():
                    domain.destroy()
                domain.undefine()
            except libvirt.libvirtError:
                pass

            # Release IP if allocated
            if vm.network_info and 'public' in vm.network_info:
                public_ip = vm.network_info['public']['ip']
                try:
                    self.ip_manager.detach_ip(public_ip)
                except Exception as e:
                    logger.error(f"Error detaching IP {public_ip} from VM {vm_id}: {e}")

            # Clean up VM directory
            vm_dir = self.vm_dir / vm_id
            if vm_dir.exists():
                shutil.rmtree(vm_dir)

            # Remove from database
            db.delete_vm(vm_id)
            del self.vms[vm_id]

        except Exception as e:
            logger.error(f"Error deleting VM {vm_id}: {e}")
            raise

    def list_disks(self) -> List[Dict]:
        """List all disks."""
        return self.disk_manager.list_disks()

    def create_disk(self, name: str, size_gb: int) -> Dict:
        """Create a new disk."""
        disk = self.disk_manager.create_disk(name, size_gb)
        return disk.to_dict()

    def delete_disk(self, disk_id: str) -> None:
        """Delete a disk."""
        self.disk_manager.delete_disk(disk_id)

    def attach_disk(self, disk_id: str, vm_name: str) -> None:
        """Attach a disk to a VM."""
        self.disk_manager.attach_disk(disk_id, vm_name)

    def detach_disk(self, disk_id: str) -> None:
        """Detach a disk from its VM."""
        self.disk_manager.detach_disk(disk_id)

    def get_disk(self, disk_id: str) -> Optional[Dict]:
        """Get disk details."""
        disk = self.disk_manager.get_disk(disk_id)
        return disk.to_dict() if disk else None

    def resize_disk(self, disk_id: str, new_size_gb: int) -> None:
        """Resize a disk."""
        self.disk_manager.resize_disk(disk_id, new_size_gb)

    def get_machine_disks(self, vm_name: str) -> List[Dict]:
        """Get all disks attached to a VM."""
        return self.disk_manager.get_machine_disks(vm_name)