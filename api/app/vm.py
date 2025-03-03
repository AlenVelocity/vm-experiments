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
from dataclasses import dataclass, asdict, field
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
import random
import string
import platform
import threading
from .libvirt_utils import get_libvirt_connection
import psutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class VMConfig:
    name: str
    network_name: str
    cpu_cores: int
    memory_mb: int
    disk_size_gb: int
    image_id: str
    cloud_init: Optional[dict] = None
    arch: Optional[str] = None

class VMStatus:
    CREATING = 'creating'
    RUNNING = 'running'
    STOPPED = 'stopped'
    ERROR = 'error'
    DELETING = 'deleting'
    NOT_FOUND = 'not_found'

class VMError(Exception):
    """Custom exception for VM operations"""
    pass

@dataclass
class VMMetrics:
    cpu_usage: float
    memory_usage: float
    disk_usage: Dict[str, float]
    network_usage: Dict[str, Dict[str, int]]
    timestamp: float

@dataclass
class VM:
    id: str
    name: str
    config: VMConfig
    network_info: Optional[Dict] = None
    ssh_port: Optional[int] = None
    status: str = VMStatus.CREATING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metrics_history: List[VMMetrics] = field(default_factory=list)
    error_message: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'name': self.name,
            'config': asdict(self.config),
            'network_info': self.network_info,
            'ssh_port': self.ssh_port,
            'status': self.status,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'error_message': self.error_message
        }

    def update_status(self, status: str, error_message: Optional[str] = None) -> None:
        self.status = status
        self.error_message = error_message
        self.updated_at = time.time()

    def add_metrics(self, metrics: VMMetrics) -> None:
        self.metrics_history.append(metrics)
        # Keep only last 24 hours of metrics
        cutoff_time = time.time() - 86400
        self.metrics_history = [m for m in self.metrics_history if m.timestamp > cutoff_time]

class LibvirtManager:
    def __init__(self, ip_manager: Optional[IPManager] = None):
        """Initialize LibvirtManager with libvirt connection."""
        try:
            self.conn = get_libvirt_connection()
            # Use absolute path to avoid the double 'api' prefix issue
            current_dir = Path.cwd()
            if current_dir.name == 'api':
                # We're in the api directory
                self.vm_dir = current_dir / "data/vms"
            else:
                # Assume we're in the project root
                self.vm_dir = current_dir / "api/data/vms"
            
            self.vm_dir.mkdir(parents=True, exist_ok=True)
            self.ip_manager = ip_manager
            self.vms = self._load_vms()
            
            # Initialize disk manager
            self.disk_manager = DiskManager(self.conn)
            
            # Set up storage pool
            try:
                self._init_storage_pool()
            except Exception as e:
                logger.warning(f"Could not initialize storage pool: {e}")
                
            # Detect system architecture
            self.arch = platform.machine()
            self.is_arm = 'arm' in self.arch.lower() or 'aarch64' in self.arch.lower()
            
            # Session for image downloads
            self.session = requests.Session()
            self.request_timeout = 300  # 5 minutes timeout for large downloads
            
            # Ubuntu image repository
            self.ubuntu_daily_base_url = "https://cloud-images.ubuntu.com/releases/focal/release/"
            
            logger.info("LibvirtManager initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing LibvirtManager: {e}")
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
        """Create cloud-init configuration for the VM."""
        try:
            # Get VM directory
            vm_dir = self.vm_dir / vm.id
            if not vm_dir.exists():
                vm_dir.mkdir(parents=True, exist_ok=True)

            # Create default cloud-init configuration
            default_cloud_init = {
                'hostname': vm.name,
                'users': [{
                    'name': 'ubuntu',
                    'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                    'shell': '/bin/bash',
                    'ssh_authorized_keys': []
                }],
                'packages': [
                    'qemu-guest-agent',
                    'python3',
                    'python3-pip',
                    'python3-venv',
                    'build-essential',
                    'pkg-config',
                    'libvirt-dev'
                ],
                'package_update': True,
                'package_upgrade': True,
                'runcmd': [
                    'systemctl daemon-reload',
                    'systemctl enable qemu-guest-agent',
                    'systemctl start qemu-guest-agent',
                    'systemctl enable ssh',
                    'systemctl start ssh',
                    'netplan apply',
                    'echo "ubuntu:ubuntu" | chpasswd',
                    'apt-get update',
                    'apt-get install -y python3-pip python3-venv libvirt-dev pkg-config',
                    'mkdir -p /opt/api',
                    'chown -R ubuntu:ubuntu /opt/api'
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

            # Create meta-data
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

            # Create user-data
            user_data = "#cloud-config\n" + json.dumps(default_cloud_init, indent=2)
            (vm_dir / 'user-data').write_text(user_data)

            # Create network-config
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
                '-output', str(vm_dir / "cloud-init.iso"),
                '-volid', 'cidata',
                '-joliet',
                '-rock',
                str(vm_dir / 'user-data'),
                str(vm_dir / 'meta-data'),
                str(vm_dir / 'network-config')
            ], check=True)

            logger.info(f"Created cloud-init configuration for VM {vm.id}")

        except Exception as e:
            logger.error(f"Error creating cloud-init config: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    def create_vm(self, config: VMConfig) -> VM:
        """Create a new VM."""
        try:
            # Check if VM with the same name already exists in libvirt
            try:
                existing_domain = self.conn.lookupByName(config.name)
                if existing_domain:
                    logger.warning(f"Found existing domain with name {config.name}, undefining it...")
                    try:
                        # Try to shutdown forcefully if running
                        if existing_domain.isActive():
                            existing_domain.destroy()
                        # Undefine the domain
                        existing_domain.undefine()
                        logger.info(f"Successfully undefined existing domain {config.name}")
                    except Exception as undefine_error:
                        logger.error(f"Error undefining existing domain: {undefine_error}")
                        raise VMError(f"Failed to undefine existing domain: {undefine_error}")
            except libvirt.libvirtError as lookup_error:
                # VM doesn't exist, which is fine
                if "Domain not found" not in str(lookup_error):
                    logger.warning(f"Unexpected libvirt error when checking for domain: {lookup_error}")

            # Create VM instance
            vm_id = str(uuid.uuid4())[:8]
            vm = VM(
                id=vm_id,
                name=config.name,
                config=config,
                status=VMStatus.CREATING
            )
            
            # Log VM creation
            logger.info(f"Creating VM {vm.name} with ID {vm.id}")
            
            # Create VM directory with proper permissions
            vm_dir = self._get_absolute_path(self.vm_dir / vm_id)
            vm_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                # Make sure directory has proper permissions
                # Use chmod to set permissions
                logger.info(f"Setting permissions on VM directory: {vm_dir}")
                os.system(f"chmod -R 777 {vm_dir}")
                
                # Also ensure parent directories have suitable permissions
                current_dir = vm_dir.parent
                while str(current_dir) != '/' and str(current_dir).find('/home/ubuntu/vm-experiments') != -1:
                    try:
                        os.chmod(current_dir, 0o777)
                        current_dir = current_dir.parent
                    except Exception:
                        break
            except Exception as e:
                logger.warning(f"Could not set directory permissions: {e}")
            
            # Configure networking
            vm.network_info = self._configure_networking(vm)

            # Prepare cloud image
            cloud_image = self._prepare_cloud_image(config.image_id)
            
            # Create VM disk - use absolute paths
            vm_disk_name = f"{vm.name}-{vm.id}.raw"
            vm_disk = vm_dir / vm_disk_name
            
            logger.info(f"Creating VM disk at {vm_disk}")
            self._create_vm_disk(cloud_image, vm_disk, config.disk_size_gb)
            
            # Generate VM UUID
            vm_uuid = str(uuid.uuid4())
            
            # Create cloud-init configuration if provided
            cloud_init_iso = None
            if config.cloud_init:
                cloud_init_iso = self._prepare_cloud_init_config(config)
                if cloud_init_iso:
                    logger.info(f"Created cloud-init config for VM {vm.name}")
            
            # Generate domain XML - make sure to use absolute path for disk
            domain_xml = self._generate_domain_xml(vm, vm_disk)
            
            # Define the domain
            domain = self.conn.defineXML(domain_xml)
            if not domain:
                raise VMError(f"Failed to define domain for VM {vm.name}")
            
            # If cloud-init ISO was created, attach it
            if cloud_init_iso:
                self._attach_cloud_init_iso(vm.name, cloud_init_iso)
            
            # Start the VM
            domain.create()
            
            # Update VM status
            vm.status = VMStatus.RUNNING
            vm.updated_at = time.time()
            
            # Assign a port for SSH forwarding
            vm.ssh_port = self._find_free_port()
            logger.info(f"Assigned SSH port {vm.ssh_port} for VM {vm.name}")

            # Save VM to database
            self._save_vm(vm)

            # Start metrics collection
            self._start_metrics_collection(vm)

            return vm

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error creating VM: {error_msg}")
            
            # Attempt cleanup of failed VM
            if 'vm' in locals():
                self._cleanup_failed_vm(vm.name)
                
            raise VMError(f"Failed to create VM: {error_msg}")

    def _init_storage_pool(self):
        """Initialize the default storage pool for QEMU/KVM"""
        try:
            # Try to find existing pool
            try:
                pool = self.conn.storagePoolLookupByName('default')
                if pool:
                    if not pool.isActive():
                        pool.create()
                    return pool
            except libvirt.libvirtError:
                pass  # Pool doesn't exist, create it
            
            # Create pool directory
            pool_path = Path('/var/lib/libvirt/images')
            os.makedirs(str(pool_path), mode=0o755, exist_ok=True)
            
            # Ensure proper permissions
            subprocess.run(['sudo', 'chown', '-R', 'libvirt-qemu:libvirt-qemu', str(pool_path)], check=True)
            
            pool_xml = f"""<pool type='dir'>
  <name>default</name>
  <target>
    <path>{str(pool_path)}</path>
    <permissions>
      <mode>0755</mode>
      <owner>64055</owner>
      <group>64055</group>
    </permissions>
  </target>
</pool>"""
            
            pool = self.conn.storagePoolDefineXML(pool_xml)
            if not pool:
                raise Exception("Failed to define storage pool")
            
            pool.setAutostart(True)
            pool.create()
            
            logger.info("Successfully initialized storage pool")
            return pool
            
        except Exception as e:
            logger.error(f"Error initializing storage pool: {str(e)}")
            raise

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
        """Generate libvirt domain XML for VM."""
        try:
            # Generate a UUID for the VM if it doesn't have one
            vm_uuid = str(uuid.uuid4())
            
            # Define VM specifications
            cpu_cores = vm.config.cpu_cores
            memory_mb = vm.config.memory_mb
            
            network_info = vm.network_info or {}
            bridge_name = network_info.get('bridge_name', 'virbr0')
            mac_address = network_info.get('mac_address')
            
            # Ensure disk_path is absolute
            absolute_disk_path = self._get_absolute_path(disk_path)
            
            # Make sure the disk file exists
            if not absolute_disk_path.exists():
                raise VMError(f"Disk image does not exist: {absolute_disk_path}")
                
            logger.info(f"Using disk path for domain XML: {absolute_disk_path}")
            
            # Create the XML template
            root = ET.Element('domain', type='kvm')
            ET.SubElement(root, 'name').text = vm.name
            ET.SubElement(root, 'uuid').text = vm_uuid
            ET.SubElement(root, 'memory', unit='MiB').text = str(memory_mb)
            ET.SubElement(root, 'currentMemory', unit='MiB').text = str(memory_mb)
            ET.SubElement(root, 'vcpu').text = str(cpu_cores)
            
            # OS settings
            os_element = ET.SubElement(root, 'os')
            ET.SubElement(os_element, 'type', arch='x86_64', machine='q35').text = 'hvm'
            ET.SubElement(os_element, 'boot', dev='hd')
            
            # Features
            features = ET.SubElement(root, 'features')
            ET.SubElement(features, 'acpi')
            ET.SubElement(features, 'apic')
            
            # CPU mode
            cpu = ET.SubElement(root, 'cpu', mode='host-model')
            
            # Add security model with none driver - this disables the security checks
            # and should resolve the permission issues
            security = ET.SubElement(root, 'seclabel', type='none')
            
            # Devices
            devices = ET.SubElement(root, 'devices')
            
            # Disk
            disk = ET.SubElement(devices, 'disk', type='file', device='disk')
            ET.SubElement(disk, 'driver', name='qemu', type='raw')
            ET.SubElement(disk, 'source', file=str(absolute_disk_path))
            ET.SubElement(disk, 'target', dev='vda', bus='virtio')
            
            # Network interface
            interface = ET.SubElement(devices, 'interface', type='bridge')
            ET.SubElement(interface, 'source', bridge=bridge_name)
            if mac_address:
                ET.SubElement(interface, 'mac', address=mac_address)
            ET.SubElement(interface, 'model', type='virtio')
            
            # Console
            console = ET.SubElement(devices, 'console', type='pty')
            ET.SubElement(console, 'target', type='serial', port='0')
            
            # VNC graphics
            graphics = ET.SubElement(devices, 'graphics', type='vnc', port='-1', autoport='yes', listen='0.0.0.0')
            ET.SubElement(graphics, 'listen', type='address', address='0.0.0.0')
            
            # Add a channel for qemu-guest-agent if it's installed
            channel = ET.SubElement(devices, 'channel', type='unix')
            ET.SubElement(channel, 'source', mode='bind')
            ET.SubElement(channel, 'target', type='virtio', name='org.qemu.guest_agent.0')
            
            # Video
            video = ET.SubElement(devices, 'video')
            ET.SubElement(video, 'model', type='cirrus')
            
            # Create the XML string
            xml_str = ET.tostring(root).decode()
            logger.debug(f"Generated domain XML for VM {vm.name}")
            
            return xml_str
        except Exception as e:
            logger.error(f"Error generating domain XML: {e}")
            raise VMError(f"Failed to generate domain XML: {e}")

    def _merge_cloud_init(self, base: dict, custom: dict) -> None:
        """Recursively merge custom cloud-init config into base config."""
        for key, value in custom.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_cloud_init(base[key], value)
            elif key in base and isinstance(base[key], list) and isinstance(value, list):
                base[key].extend(value)
            else:
                base[key] = value

    def _prepare_cloud_image(self, image_id: str) -> Path:
        """Download and prepare a cloud image if not already present."""
        images_dir = Path("api/data/vms")
        images_dir.mkdir(parents=True, exist_ok=True)
        
        cloud_image = images_dir / f"{image_id}.img"
        
        # Images mapping - add more as needed
        image_urls = {
            'ubuntu-20.04': 'https://cloud-images.ubuntu.com/releases/focal/release/ubuntu-20.04-server-cloudimg-amd64.img',
            'ubuntu-22.04': 'https://cloud-images.ubuntu.com/releases/jammy/release/ubuntu-22.04-server-cloudimg-amd64.img',
            'debian-11': 'https://cloud.debian.org/images/cloud/bullseye/latest/debian-11-generic-amd64.qcow2',
            'debian-12': 'https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2',
            'centos-9-stream': 'https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2',
            'alpine-3.17': 'https://dl-cdn.alpinelinux.org/alpine/v3.17/releases/x86_64/alpine-virt-3.17.0-x86_64.iso',
        }
        
        if not cloud_image.exists():
            # If image doesn't exist, download it
            if image_id not in image_urls:
                raise VMError(f"Unknown image ID: {image_id}. Available images: {', '.join(image_urls.keys())}")
            
            logger.info(f"Downloading cloud image {image_id}...")
            url = image_urls[image_id]
            
            # Create a temporary directory for downloads
            tmp_dir = Path("api/data/tmp")
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_file = tmp_dir / f"{image_id}-download.img"
            
            try:
                # Download the image with progress
                logger.info(f"Downloading {url} to {tmp_file}")
                
                # Use wget with progress
                result = subprocess.run(
                    ["wget", "-O", str(tmp_file), url], 
                    check=True,
                    capture_output=True,
                    text=True
                )
                
                # Check if download succeeded
                if result.returncode != 0:
                    raise VMError(f"Failed to download cloud image: {result.stderr}")
                
                # Make sure the image is valid
                validate_cmd = ["qemu-img", "info", str(tmp_file)]
                result = subprocess.run(validate_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise VMError(f"Downloaded image is not valid: {result.stderr}")
                
                # Move to final location
                shutil.move(str(tmp_file), str(cloud_image))
                
                # Set proper permissions on the cloud image
                logger.info(f"Setting permissions on cloud image: {cloud_image}")
                os.system(f"chmod 666 {cloud_image}")
                
                logger.info(f"Successfully downloaded and installed {image_id} image")
                
            except subprocess.CalledProcessError as e:
                logger.error(f"Error downloading cloud image: {e}")
                if tmp_file.exists():
                    tmp_file.unlink()
                raise VMError(f"Failed to download cloud image: {e}")
                
            except Exception as e:
                logger.error(f"Error preparing cloud image: {e}")
                if tmp_file.exists():
                    tmp_file.unlink()
                raise VMError(f"Failed to prepare cloud image: {e}")
                
        return cloud_image

    def _get_absolute_path(self, path: Path) -> Path:
        """Ensure we're using absolute paths for qemu commands."""
        if path.is_absolute():
            return path
        # If we're running from the project root or api directory,
        # convert to absolute path
        current_dir = Path.cwd()
        if current_dir.name == 'api':
            # We're in the api directory
            return current_dir / path
        else:
            # Assume we're in the project root
            return current_dir / path

    def _create_vm_disk(self, cloud_image: Path, vm_disk: Path, size_gb: int) -> None:
        """Create a VM disk based on a cloud image."""
        try:
            # Make sure we're using absolute paths
            abs_cloud_image = self._get_absolute_path(cloud_image)
            abs_vm_disk = self._get_absolute_path(vm_disk)
            
            # Make sure the base image exists
            if not abs_cloud_image.exists():
                raise VMError(f"Base image does not exist: {abs_cloud_image}")
                
            # Make sure the parent directory exists
            abs_vm_disk.parent.mkdir(parents=True, exist_ok=True)
            
            # Set directory permissions to allow libvirt to access
            try:
                os.chmod(abs_vm_disk.parent, 0o755)
            except Exception as perm_error:
                logger.warning(f"Failed to set permissions on VM directory: {perm_error}")
            
            logger.info(f"Creating VM disk {abs_vm_disk} based on {abs_cloud_image}")
            
            # Detect the format of the source image
            format_cmd = ['qemu-img', 'info', '--output=json', str(abs_cloud_image)]
            format_result = subprocess.run(
                format_cmd,
                check=True,
                capture_output=True,
                text=True
            )
            
            # Parse the format from json output
            img_info = json.loads(format_result.stdout)
            source_format = img_info.get('format', 'qcow2')  # Default to qcow2 if not detected
            
            logger.info(f"Detected source image format: {source_format}")
            
            # Create a raw image with the base cloud image
            cmd = [
                'qemu-img', 'convert',
                '-f', source_format,
                '-O', 'raw',
                str(abs_cloud_image),
                str(abs_vm_disk)
            ]
            
            logger.info(f"Running command: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True
            )
            
            # Resize the disk to the requested size
            resize_cmd = ['qemu-img', 'resize', str(abs_vm_disk), f"{size_gb}G"]
            logger.info(f"Resizing disk: {' '.join(resize_cmd)}")
            
            resize_result = subprocess.run(
                resize_cmd,
                check=True,
                capture_output=True,
                text=True
            )
            
            # Set permissions on the disk file to make it accessible to libvirt
            logger.info(f"Setting permissions on VM disk file: {abs_vm_disk}")
            try:
                # Make the disk file readable and writable by everyone
                os.system(f"chmod 666 {abs_vm_disk}")
                
                # Try to make parent directories accessible as well
                current_dir = abs_vm_disk.parent
                while str(current_dir) != '/' and str(current_dir).find('/home/ubuntu/vm-experiments') != -1:
                    try:
                        os.chmod(current_dir, 0o777)
                        current_dir = current_dir.parent
                    except Exception:
                        break
            except Exception as perm_error:
                logger.warning(f"Failed to set permissions on VM disk file: {perm_error}")
            
            logger.info(f"Successfully created VM disk at {abs_vm_disk}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Command '{e.cmd}' returned non-zero exit status {e.returncode}")
            logger.error(f"STDOUT: {e.stdout}")
            logger.error(f"STDERR: {e.stderr}")
            raise VMError(f"Failed to create VM disk: {e}")
        except Exception as e:
            logger.error(f"Error creating VM disk: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise VMError(f"Failed to create VM disk: {e}")

    def _configure_networking(self, vm: VM) -> Dict:
        """Configure networking for a VM."""
        try:
            logger.info(f"Configuring networking for VM {vm.name}")
            
            # Get network info
            network_name = vm.config.network_name
            
            # Find network by name
            try:
                network = self.conn.networkLookupByName(network_name)
            except libvirt.libvirtError:
                logger.warning(f"Network {network_name} not found, falling back to default")
                try:
                    network = self.conn.networkLookupByName('default')
                    network_name = 'default'
                except libvirt.libvirtError:
                    logger.warning("Default network not found, will use first available network")
                    networks = self.conn.listAllNetworks()
                    if not networks:
                        raise VMError("No networks available")
                    network = networks[0]
                    network_name = network.name()
            
            # Get network details
            net_xml = network.XMLDesc()
            net_root = ET.fromstring(net_xml)
            
            # Get bridge name
            bridge_elem = net_root.find('bridge')
            bridge_name = bridge_elem.get('name') if bridge_elem is not None else 'virbr0'
            
            # Get IP information
            ip_elem = net_root.find('ip')
            network_address = None
            netmask = None
            
            if ip_elem is not None:
                network_address = ip_elem.get('address')
                netmask = ip_elem.get('netmask')
            
            # Generate a MAC address if not already set
            if not hasattr(vm, 'mac_address'):
                # Generate a random MAC address
                mac = [0x52, 0x54, 0x00,
                      random.randint(0x00, 0xff),
                      random.randint(0x00, 0xff),
                      random.randint(0x00, 0xff)]
                vm.mac_address = ':'.join([f'{x:02x}' for x in mac])
            
            # Allocate an IP from the IP manager if available
            ip_address = None
            if hasattr(self, 'ip_manager') and self.ip_manager:
                try:
                    # Get an available IP address
                    ip_address = self.ip_manager.get_available_ip()
                    if ip_address:
                        self.ip_manager.attach_ip(ip_address, vm.id)
                        logger.info(f"Allocated IP {ip_address} for VM {vm.name}")
                except Exception as e:
                    logger.warning(f"Failed to allocate IP from IP manager: {e}")
            
            # Build network info dictionary
            network_info = {
                'network_name': network_name,
                'bridge_name': bridge_name,
                'network_address': network_address,
                'netmask': netmask,
                'mac_address': getattr(vm, 'mac_address', None),
                'ip_address': ip_address
            }
            
            logger.info(f"Network configuration for VM {vm.name}: {network_info}")
            return network_info
            
        except Exception as e:
            logger.error(f"Error configuring networking: {e}")
            raise VMError(f"Failed to configure networking: {e}")

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

    def list_images(self) -> List[Dict[str, str]]:
        try:
            # First try to get from cache
            cache_file = self.vm_dir / "image_cache.json"
            if cache_file.exists():
                cache_age = time.time() - cache_file.stat().st_mtime
                if cache_age < 3600:  # Cache valid for 1 hour
                    with open(cache_file) as f:
                        cached_images = json.load(f)
                        if cached_images:  # Only return cache if it's not empty
                            return cached_images

            # If cache miss or expired, fetch from Ubuntu cloud images
            response = self.session.get(
                self.ubuntu_daily_base_url,
                timeout=self.request_timeout,
                headers={'User-Agent': 'VM-Manager/1.0'}
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            images = []
            
            # Look for .img files
            for link in soup.find_all('a'):
                href = link.get('href', '')
                if href.endswith('.img'):
                    match = re.search(r'ubuntu-(\d+\.\d+).*?\.img', href)
                    if match:
                        version = match.group(1)
                        image_id = f"ubuntu-{version}"
                        images.append({
                            "id": image_id,
                            "name": f"Ubuntu {version}",
                            "version": version,
                            "url": self.ubuntu_daily_base_url + href
                        })
            
            if images:  # Only cache if we found images
                # Cache the results
                with open(cache_file, 'w') as f:
                    json.dump(images, f)
                return images
            
            # If no images found or error occurred, return default image
            return [
                {
                    "id": "ubuntu-20.04",
                    "name": "Ubuntu 20.04 LTS",
                    "version": "20.04",
                    "url": "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img"
                }
            ]
            
        except Exception as e:
            logger.error(f"Error listing images: {str(e)}")
            logger.error(traceback.format_exc())
            # Always return at least the default image
            return [
                {
                    "id": "ubuntu-20.04",
                    "name": "Ubuntu 20.04 LTS",
                    "version": "20.04",
                    "url": "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img"
                }
            ]

    def delete_vm(self, vm_id: str) -> None:
        """Delete a VM with proper cleanup."""
        try:
            vm = self.vms.get(vm_id)
            if not vm:
                logger.warning(f"VM {vm_id} not found")
                return

            # Stop VM if running
            try:
                domain = self.conn.lookupByName(vm.name)
                if domain.isActive():
                    domain.destroy()
                domain.undefine()
            except libvirt.libvirtError as e:
                logger.warning(f"Error stopping VM domain: {e}")

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
                try:
                    shutil.rmtree(vm_dir)
                except Exception as e:
                    logger.error(f"Error removing VM directory: {e}")

            # Remove from database
            try:
                db.delete_vm(vm_id)
            except Exception as e:
                logger.error(f"Error removing VM from database: {e}")

            # Remove from memory
            self.vms.pop(vm_id, None)

            logger.info(f"Successfully deleted VM {vm_id}")

        except Exception as e:
            logger.error(f"Error deleting VM {vm_id}: {e}")
            raise

    def list_disks(self) -> List[Dict]:
        """List all disks"""
        try:
            return self.disk_manager.list_disks()
        except Exception as e:
            logger.error(f"Error listing disks: {str(e)}")
            raise

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

    def list_vms(self) -> List[VM]:
        """List all VMs with their current status"""
        vms = []
        for vm_id, vm in self.vms.items():
            vm.status = self.get_vm_status(vm_id)
            vms.append(vm)
        return vms

    def get_vm(self, vm_id: str) -> Optional[VM]:
        """Get a VM by its ID"""
        return self.vms.get(vm_id)

    def get_vm_status(self, vm_id: str) -> str:
        """Get the current status of a VM"""
        try:
            vm = self.vms.get(vm_id)
            if not vm:
                return 'not_found'

            domain = self.conn.lookupByName(vm.name)
            if not domain:
                return 'not_found'

            state, reason = domain.state()
            states = {
                libvirt.VIR_DOMAIN_NOSTATE: 'no_state',
                libvirt.VIR_DOMAIN_RUNNING: 'running',
                libvirt.VIR_DOMAIN_BLOCKED: 'blocked',
                libvirt.VIR_DOMAIN_PAUSED: 'paused',
                libvirt.VIR_DOMAIN_SHUTDOWN: 'shutdown',
                libvirt.VIR_DOMAIN_SHUTOFF: 'shutoff',
                libvirt.VIR_DOMAIN_CRASHED: 'crashed',
                libvirt.VIR_DOMAIN_PMSUSPENDED: 'suspended'
            }
            return states.get(state, 'unknown')
        except libvirt.libvirtError:
            return 'not_found'
        except Exception as e:
            logger.error(f"Error getting VM status: {str(e)}")
            return 'error'

    def get_metrics(self, vm: VM) -> Dict[str, Any]:
        """Get current metrics for a VM"""
        try:
            domain = self.conn.lookupByName(vm.name)
            if not domain:
                raise Exception("VM domain not found")

            # Get CPU stats
            cpu_stats = domain.getCPUStats(True)[0]
            cpu_time = cpu_stats.get('cpu_time', 0)
            system_time = cpu_stats.get('system_time', 0)
            user_time = cpu_stats.get('user_time', 0)

            # Get memory stats
            memory_stats = domain.memoryStats()
            actual = memory_stats.get('actual', 0)
            available = memory_stats.get('available', 0)
            unused = memory_stats.get('unused', 0)

            # Get disk stats
            disk_stats = {}
            for disk in domain.disks:
                stats = domain.blockStats(disk)
                disk_stats[disk] = {
                    'read_bytes': stats[0],
                    'read_requests': stats[1],
                    'write_bytes': stats[2],
                    'write_requests': stats[3]
                }

            # Get network stats
            net_stats = {}
            for interface in domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT).keys():
                stats = domain.interfaceStats(interface)
                net_stats[interface] = {
                    'rx_bytes': stats[0],
                    'rx_packets': stats[1],
                    'rx_errors': stats[2],
                    'rx_drops': stats[3],
                    'tx_bytes': stats[4],
                    'tx_packets': stats[5],
                    'tx_errors': stats[6],
                    'tx_drops': stats[7]
                }

            return {
                'cpu': {
                    'total_time': cpu_time,
                    'system_time': system_time,
                    'user_time': user_time
                },
                'memory': {
                    'actual': actual,
                    'available': available,
                    'unused': unused,
                    'used': actual - unused if unused else 0
                },
                'disk': disk_stats,
                'network': net_stats
            }
        except Exception as e:
            logger.error(f"Error getting VM metrics: {str(e)}")
            return {}

    def resize_cpu(self, vm: VM, cpu_cores: int) -> None:
        """Resize the number of CPU cores for a VM"""
        try:
            domain = self.conn.lookupByName(vm.name)
            if not domain:
                raise Exception("VM domain not found")

            # Update XML configuration
            xml = domain.XMLDesc()
            tree = ET.ElementTree(ET.fromstring(xml))
            vcpu = tree.find('.//vcpu')
            if vcpu is not None:
                vcpu.text = str(cpu_cores)
                new_xml = ET.tostring(tree.getroot(), encoding='unicode')
                
                # Apply new configuration
                if domain.isActive():
                    domain.setVcpus(cpu_cores)
                domain.updateDeviceFlags(new_xml, libvirt.VIR_DOMAIN_AFFECT_CONFIG)

            # Update VM config
            vm.config.cpu_cores = cpu_cores
            self._save_vm(vm)
        except Exception as e:
            logger.error(f"Error resizing CPU: {str(e)}")
            raise

    def resize_memory(self, vm: VM, memory_mb: int) -> None:
        """Resize the memory for a VM"""
        try:
            domain = self.conn.lookupByName(vm.name)
            if not domain:
                raise Exception("VM domain not found")

            # Update XML configuration
            xml = domain.XMLDesc()
            tree = ET.ElementTree(ET.fromstring(xml))
            memory = tree.find('.//memory')
            currentMemory = tree.find('.//currentMemory')
            if memory is not None and currentMemory is not None:
                memory_kb = memory_mb * 1024
                memory.text = str(memory_kb)
                currentMemory.text = str(memory_kb)
                new_xml = ET.tostring(tree.getroot(), encoding='unicode')
                
                # Apply new configuration
                if domain.isActive():
                    domain.setMemory(memory_kb)
                domain.updateDeviceFlags(new_xml, libvirt.VIR_DOMAIN_AFFECT_CONFIG)

            # Update VM config
            vm.config.memory_mb = memory_mb
            self._save_vm(vm)
        except Exception as e:
            logger.error(f"Error resizing memory: {str(e)}")
            raise

    def _save_vm(self, vm: VM) -> None:
        """Save VM configuration to the database"""
        try:
            db.update_vm(vm.id, {
                'name': vm.name,
                'cpu_cores': vm.config.cpu_cores,
                'memory_mb': vm.config.memory_mb,
                'disk_size_gb': vm.config.disk_size_gb,
                'network_name': vm.config.network_name,
                'cloud_init': vm.config.cloud_init,
                'image_id': vm.config.image_id,
                'network_info': vm.network_info,
                'ssh_port': vm.ssh_port
            })
        except Exception as e:
            logger.error(f"Error saving VM configuration: {str(e)}")
            raise

    def _prepare_cloud_init_config(self, config: VMConfig) -> Optional[str]:
        """Prepare cloud-init configuration for VM. Returns path to cloud-init ISO."""
        try:
            if not config.cloud_init:
                logger.info("No cloud-init config provided, using defaults")
                return None
            
            # Create temp directory for cloud-init files
            cloud_init_dir = Path(f"api/data/tmp/cloud-init-{config.name}")
            cloud_init_dir.mkdir(parents=True, exist_ok=True)
            
            # Default cloud-init configuration
            default_config = {
                'hostname': config.name,
                'users': [{
                    'name': 'ubuntu',
                    'shell': '/bin/bash',
                    'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                    'ssh_authorized_keys': []
                }],
                'packages': ['qemu-guest-agent', 'cloud-init'],
                'package_update': True,
                'package_upgrade': True,
                'runcmd': ['systemctl enable qemu-guest-agent', 'systemctl start qemu-guest-agent'],
                'write_files': []
            }
            
            # Merge custom config with defaults
            merged_config = default_config.copy()
            self._merge_cloud_init(merged_config, config.cloud_init)
            
            # Generate cloud-init files
            user_data = f"""#cloud-config
{json.dumps(merged_config, indent=2)}
"""
            
            meta_data = f"""instance-id: {config.name}
local-hostname: {merged_config['hostname']}
"""
            
            network_config = """version: 2
ethernets:
  ens3:
    dhcp4: true
"""
            
            # Write cloud-init files
            with open(cloud_init_dir / "user-data", "w") as f:
                f.write(user_data)
                
            with open(cloud_init_dir / "meta-data", "w") as f:
                f.write(meta_data)
                
            with open(cloud_init_dir / "network-config", "w") as f:
                f.write(network_config)
                
            # Generate ISO file
            iso_path = cloud_init_dir / "cloud-init.iso"
            
            # Check if mkisofs or genisoimage is available
            iso_cmd = None
            for cmd in ["mkisofs", "genisoimage"]:
                try:
                    subprocess.run(["which", cmd], check=True, capture_output=True)
                    iso_cmd = cmd
                    break
                except subprocess.CalledProcessError:
                    continue
                    
            if not iso_cmd:
                logger.warning("Neither mkisofs nor genisoimage found, cannot create cloud-init ISO")
                return None
                
            # Create cloud-init ISO
            cmd = [
                iso_cmd,
                "-output", str(iso_path),
                "-volid", "cidata",
                "-joliet",
                "-rock",
                str(cloud_init_dir / "user-data"),
                str(cloud_init_dir / "meta-data"),
                str(cloud_init_dir / "network-config")
            ]
            
            subprocess.run(cmd, check=True, capture_output=True)
            
            logger.info(f"Created cloud-init ISO at {iso_path}")
            return str(iso_path)
            
        except Exception as e:
            logger.error(f"Error creating cloud-init config: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
            
    def _attach_cloud_init_iso(self, vm_name: str, iso_path: str):
        """Attach cloud-init ISO to the VM."""
        try:
            domain = self.conn.lookupByName(vm_name)
            
            # Generate disk XML
            disk_xml = f"""
            <disk type='file' device='cdrom'>
                <driver name='qemu' type='raw'/>
                <source file='{iso_path}'/>
                <target dev='hdc' bus='ide'/>
                <readonly/>
            </disk>
            """
            
            domain.attachDevice(disk_xml)
            
        except Exception as e:
            logger.error(f"Failed to attach cloud-init ISO: {e}")
            raise VMError(f"Failed to attach cloud-init ISO: {e}")
            
    def _cleanup_failed_vm(self, vm_name: str):
        """Cleanup resources after failed VM creation."""
        try:
            # Remove cloud-init ISO if it exists
            iso_path = f"/var/lib/libvirt/images/cloud-init-{vm_name}.iso"
            if os.path.exists(iso_path):
                os.remove(iso_path)
                
            # Existing cleanup logic...
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def _start_metrics_collection(self, vm: VM) -> None:
        """Start collecting metrics for the VM in a background thread."""
        def collect_metrics():
            while True:
                try:
                    if vm.status != VMStatus.RUNNING:
                        break

                    metrics = self.get_metrics(vm)
                    if metrics:
                        vm.add_metrics(VMMetrics(
                            cpu_usage=metrics['cpu']['usage_percent'],
                            memory_usage=metrics['memory']['used_percent'],
                            disk_usage=metrics['disk'],
                            network_usage=metrics['network'],
                            timestamp=time.time()
                        ))
                        self._save_vm(vm)

                    time.sleep(60)  # Collect metrics every minute
                except Exception as e:
                    logger.error(f"Error collecting metrics for VM {vm.id}: {e}")
                    time.sleep(60)  # Wait before retrying

        thread = threading.Thread(target=collect_metrics, daemon=True)
        thread.start()

    def get_vm_logs(self, vm_id: str, lines: int = 100) -> List[str]:
        """Get recent logs for a VM."""
        try:
            vm = self.get_vm(vm_id)
            if not vm:
                raise VMError(f"VM {vm_id} not found")

            domain = self.conn.lookupByName(vm.name)
            if not domain:
                raise VMError(f"VM domain {vm.name} not found")

            # Get console output
            stream = self.conn.newStream()
            domain.openConsole(None, stream, 0)
            
            output = []
            while len(output) < lines:
                try:
                    data = stream.recv(1024).decode()
                    if not data:
                        break
                    output.extend(data.splitlines())
                except:
                    break

            return output[-lines:] if output else []

        except Exception as e:
            logger.error(f"Error getting VM logs: {e}")
            raise VMError(f"Failed to get VM logs: {e}")

    def get_vm_statistics(self, vm_id: str) -> Dict:
        """Get detailed statistics for a VM."""
        try:
            vm = self.get_vm(vm_id)
            if not vm:
                raise VMError(f"VM {vm_id} not found")

            # Get current metrics
            current_metrics = self.get_metrics(vm)

            # Calculate historical statistics
            if vm.metrics_history:
                avg_cpu = sum(m.cpu_usage for m in vm.metrics_history) / len(vm.metrics_history)
                avg_memory = sum(m.memory_usage for m in vm.metrics_history) / len(vm.metrics_history)
                
                # Calculate network statistics
                network_stats = {
                    'total_rx_bytes': 0,
                    'total_tx_bytes': 0,
                    'avg_rx_bytes_per_second': 0,
                    'avg_tx_bytes_per_second': 0
                }
                
                for metrics in vm.metrics_history:
                    for interface in metrics.network_usage.values():
                        network_stats['total_rx_bytes'] += interface.get('rx_bytes', 0)
                        network_stats['total_tx_bytes'] += interface.get('tx_bytes', 0)
                
                time_period = vm.metrics_history[-1].timestamp - vm.metrics_history[0].timestamp
                if time_period > 0:
                    network_stats['avg_rx_bytes_per_second'] = network_stats['total_rx_bytes'] / time_period
                    network_stats['avg_tx_bytes_per_second'] = network_stats['total_tx_bytes'] / time_period
            else:
                avg_cpu = 0
                avg_memory = 0
                network_stats = {
                    'total_rx_bytes': 0,
                    'total_tx_bytes': 0,
                    'avg_rx_bytes_per_second': 0,
                    'avg_tx_bytes_per_second': 0
                }

            return {
                'current': current_metrics,
                'historical': {
                    'avg_cpu_usage': avg_cpu,
                    'avg_memory_usage': avg_memory,
                    'network': network_stats,
                    'uptime': time.time() - vm.created_at,
                    'total_metrics_collected': len(vm.metrics_history)
                }
            }

        except Exception as e:
            logger.error(f"Error getting VM statistics: {e}")
            raise VMError(f"Failed to get VM statistics: {e}")


class VMManager:
    """Manages VM operations through libvirt."""
    
    def __init__(self, network_manager: NetworkManager, ip_manager: IPManager):
        """Initialize the VM manager."""
        self.network_manager = network_manager
        self.ip_manager = ip_manager
        self.libvirt_manager = LibvirtManager(ip_manager=ip_manager)
        
        # Database connection is already initialized
        # This is just to make sure VMs are loaded on startup
        self._load_vms()
        
        logger.info("VMManager initialized successfully")

    def _load_vms(self):
        self.libvirt_manager._load_vms()
            
    def create_vm(self, config: VMConfig) -> VM:
        return self.libvirt_manager.create_vm(config)
    
    def get_vm(self, vm_id: str) -> Optional[VM]:
        return self.libvirt_manager.get_vm(vm_id)
    
    def delete_vm(self, vm_id: str) -> None:
        return self.libvirt_manager.delete_vm(vm_id)
    
    def list_vms(self) -> List[VM]:
        return self.libvirt_manager.list_vms()
    
    def get_vm_status(self, vm_id: str) -> str:
        return self.libvirt_manager.get_vm_status(vm_id)
    
    def get_metrics(self, vm: VM) -> Dict[str, Any]:
        return self.libvirt_manager.get_metrics(vm)
    
    def list_disks(self) -> List[Dict]:
        return self.libvirt_manager.list_disks()
    
    def create_disk(self, name: str, size_gb: int) -> Dict:
        return self.libvirt_manager.create_disk(name, size_gb)
    
    def resize_cpu(self, vm: VM, cpu_cores: int) -> None:
        return self.libvirt_manager.resize_cpu(vm, cpu_cores)
    
    def resize_memory(self, vm: VM, memory_mb: int) -> None:
        return self.libvirt_manager.resize_memory(vm, memory_mb)


class VMConsole:
    def __init__(self, vm: VM, libvirt_manager: LibvirtManager):
        self.vm = vm
        self.libvirt_manager = libvirt_manager
        self.is_active = False
        self.on_output = None

    def connect(self):
        try:
            domain = self.libvirt_manager.conn.lookupByName(self.vm.name)
            if not domain:
                raise Exception("VM domain not found")

            # Get console stream
            stream = self.libvirt_manager.conn.newStream()
            domain.openConsole(None, stream, 0)

            self.is_active = True
            self._handle_stream(stream)
        except Exception as e:
            logger.error(f"Error connecting to console: {str(e)}")
            raise

    def send_input(self, text: str):
        if not self.is_active:
            raise Exception("Console not connected")
        
        try:
            domain = self.libvirt_manager.conn.lookupByName(self.vm.name)
            if not domain:
                raise Exception("VM domain not found")

            # Send input to console
            stream = self.libvirt_manager.conn.newStream()
            domain.openConsole(None, stream, 0)
            stream.send(text.encode())
        except Exception as e:
            logger.error(f"Error sending console input: {str(e)}")
            raise

    def _handle_stream(self, stream):
        def stream_handler(stream, events, _):
            try:
                if events & libvirt.VIR_STREAM_EVENT_READABLE:
                    data = stream.recv(1024).decode()
                    if self.on_output:
                        self.on_output(data)
            except Exception as e:
                logger.error(f"Error handling console stream: {str(e)}")
                self.is_active = False

        stream.eventAddCallback(
            libvirt.VIR_STREAM_EVENT_READABLE,
            stream_handler
        )