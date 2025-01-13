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
import random
import string

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
    status: Optional[str] = None

class LibvirtManager:
    def __init__(self):
        self.conn = libvirt.open('qemu:///system')
        if not self.conn:
            raise Exception('Failed to open connection to qemu:///system')
            
        try:
            self.vm_dir = Path("api/data/vms")
            self.vm_dir.mkdir(parents=True, exist_ok=True)
            
            self._init_storage_pool()
            
            self.network_manager = NetworkManager(self.conn)
            self.ip_manager = IPManager()
            self.disk_manager = DiskManager(self.conn)
            
            self.vms = self._load_vms()
            
            logger.info("LibvirtManager initialized successfully")
            self.ubuntu_daily_base_url = "https://cloud-images.ubuntu.com/focal/current/"
            
            # Configure requests session with timeouts and retries
            self.session = requests.Session()
            self.session.mount('https://', requests.adapters.HTTPAdapter(
                max_retries=3,
                pool_connections=10,
                pool_maxsize=10
            ))
            self.request_timeout = 10  # seconds
            
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
            # Generate a unique ID for the VM with hex digits at the end
            vm_id = str(uuid.uuid4())[:8]  # Use first 8 chars of a real UUID for the ID
            vm_uuid = str(uuid.uuid4())    # Generate a full UUID for libvirt
            
            # Create VM directory
            vm_dir = self.vm_dir / vm_id
            vm_dir.mkdir(parents=True, exist_ok=True)
            
            # Download or use cached Ubuntu image
            image_id = config.image_id or 'ubuntu-20.04'
            base_image = self._download_ubuntu_image(image_id)
            
            # Create VM disk using qcow2 format with absolute paths
            disk_path = vm_dir / "disk.qcow2"
            
            # Convert paths to absolute
            base_image_abs = str(base_image.absolute())
            disk_path_abs = str(disk_path.absolute())
            
            logger.info(f"Creating VM disk with base image: {base_image_abs}")
            logger.info(f"Target disk path: {disk_path_abs}")
            
            subprocess.run([
                'qemu-img', 'create',
                '-f', 'qcow2',
                '-F', 'qcow2',
                '-b', base_image_abs,
                disk_path_abs,
                f"{config.disk_size_gb}G"
            ], check=True)
            
            vm = VM(id=vm_id, name=config.name, config=config)
            vm.ssh_port = self._find_free_port()

            # Get network info
            public_ip = self.ip_manager.get_available_ip()
            if public_ip:
                self.ip_manager.attach_ip(public_ip, vm_id)

            # Calculate network details
            vm_number = int(vm_id[:4], 16) % 254
            subnet = (vm_number // 254) + 1
            host = (vm_number % 254) + 1

            vm.network_info = {
                'private': {
                    'ip': f"192.168.{subnet}.{host}",
                    'subnet_mask': "255.255.255.0",
                    'gateway': f"192.168.{subnet}.1",
                    'network_name': config.network_name or 'default'
                }
            }

            if public_ip:
                vm.network_info['public'] = {
                    'ip': public_ip,
                    'subnet_mask': "255.255.255.0",
                    'gateway': f"10.{subnet}.{host}.1",
                    'network_name': f"{config.network_name}-public" if config.network_name else "default-public"
                }

            # Create cloud-init config
            self._create_cloud_init_config(vm)

            # Generate and define domain
            domain_xml = self._generate_domain_xml(vm, disk_path, vm_uuid)
            logger.info(f"Defining domain with XML:\n{domain_xml}")
            
            domain = self.conn.defineXML(domain_xml)
            if not domain:
                raise Exception("Failed to define domain")
            
            # Start the domain
            if domain.create() < 0:
                raise Exception("Failed to start domain")

            # Save VM to database
            db.create_vm(vm_id, {
                'name': vm.name,
                'cpu_cores': config.cpu_cores,
                'memory_mb': config.memory_mb,
                'disk_size_gb': config.disk_size_gb,
                'network_name': config.network_name,
                'cloud_init': config.cloud_init,
                'image_id': config.image_id,
                'network_info': vm.network_info,
                'ssh_port': vm.ssh_port,
                'status': 'running',
                'uuid': vm_uuid
            })

            # Add to in-memory cache
            self.vms[vm_id] = vm

            return vm

        except Exception as e:
            logger.error(f"Error creating VM: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Clean up on failure
            if 'vm_id' in locals():
                self.delete_vm(vm_id)
            raise

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

    def _generate_domain_xml(self, vm: VM, disk_path: Path, vm_uuid: str) -> str:
        root = ET.Element('domain', type='kvm')
        
        # Basic VM metadata - use VM ID as the domain name
        ET.SubElement(root, 'name').text = vm.id
        ET.SubElement(root, 'uuid').text = vm_uuid  # Use the full UUID here
        ET.SubElement(root, 'memory', unit='MiB').text = str(vm.config.memory_mb)
        ET.SubElement(root, 'currentMemory', unit='MiB').text = str(vm.config.memory_mb)
        ET.SubElement(root, 'vcpu', placement='static').text = str(vm.config.cpu_cores)
        
        # OS configuration
        os = ET.SubElement(root, 'os')
        ET.SubElement(os, 'type', arch='x86_64', machine='pc-q35-6.2').text = 'hvm'
        ET.SubElement(os, 'boot', dev='hd')
        
        # Features
        features = ET.SubElement(root, 'features')
        ET.SubElement(features, 'acpi')
        ET.SubElement(features, 'apic')
        
        # CPU configuration
        cpu = ET.SubElement(root, 'cpu', mode='host-model')
        ET.SubElement(cpu, 'topology', sockets='1', cores=str(vm.config.cpu_cores), threads='1')
        
        # Devices
        devices = ET.SubElement(root, 'devices')
        
        # Disk
        disk = ET.SubElement(devices, 'disk', type='file', device='disk')
        ET.SubElement(disk, 'driver', name='qemu', type='qcow2')
        ET.SubElement(disk, 'source', file=str(disk_path))
        ET.SubElement(disk, 'target', dev='vda', bus='virtio')
        
        # Network interface
        interface = ET.SubElement(devices, 'interface', type='network')
        ET.SubElement(interface, 'source', network=vm.config.network_name or 'default')
        ET.SubElement(interface, 'model', type='virtio')
        
        # Add description with VM name for reference
        ET.SubElement(root, 'description').text = f"VM Name: {vm.name}"
        
        # Convert to string without pretty_print
        return ET.tostring(root, encoding='unicode')

    def _merge_cloud_init(self, base: dict, custom: dict) -> None:
        """Recursively merge custom cloud-init config into base config."""
        for key, value in custom.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_cloud_init(base[key], value)
            elif key in base and isinstance(base[key], list) and isinstance(value, list):
                base[key].extend(value)
            else:
                base[key] = value

    def _download_ubuntu_image(self, image_id: str) -> Path:
        try:
            # Ensure image_id is properly formatted
            if not image_id.startswith('ubuntu-'):
                image_id = f"ubuntu-{image_id}"
            
            cached_image = self.vm_dir / f"{image_id}.img"
            if cached_image.exists():
                logger.info(f"Using cached Ubuntu image: {cached_image}")
                return cached_image
            
            # Use AMD64 Focal image URL
            image_url = f"{self.ubuntu_daily_base_url}focal-server-cloudimg-amd64.img"
            logger.info(f"Downloading Ubuntu image from {image_url}")
            
            # Create temporary file for download
            temp_path = cached_image.with_suffix('.tmp')
            
            # Download with progress tracking
            response = requests.get(image_url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192
            downloaded = 0
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            percent = int(100 * downloaded / total_size)
                            if percent % 10 == 0:  # Log every 10%
                                logger.info(f"Download progress: {percent}%")
            
            # Move temporary file to final location
            temp_path.rename(cached_image)
            logger.info(f"Successfully downloaded image to {cached_image}")
            
            return cached_image
            
        except Exception as e:
            logger.error(f"Error downloading Ubuntu image: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Clean up temporary file if it exists
            if 'temp_path' in locals() and temp_path.exists():
                temp_path.unlink()
            raise

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

class VMManager:
    def __init__(self):
        self.libvirt_manager = LibvirtManager()
        self.active_consoles = {}

    def create_vm(self, config: VMConfig) -> VM:
        return self.libvirt_manager.create_vm(config)

    def get_vm_status(self, vm_id: str) -> Dict[str, Any]:
        return self.libvirt_manager.get_vm_status(vm_id)

    def list_vms(self) -> List[VM]:
        return self.libvirt_manager.list_vms()

    def get_vm(self, vm_id: str) -> Optional[VM]:
        return self.libvirt_manager.get_vm(vm_id)

    def delete_vm(self, vm_id: str) -> None:
        return self.libvirt_manager.delete_vm(vm_id)

    def start_vm(self, vm_id: str) -> None:
        return self.libvirt_manager.start_vm(vm_id)

    def stop_vm(self, vm_id: str) -> None:
        return self.libvirt_manager.stop_vm(vm_id)

    def restart_vm(self, vm_id: str) -> None:
        return self.libvirt_manager.restart_vm(vm_id)

    def resize_cpu(self, vm: VM, cpu_cores: int) -> None:
        return self.libvirt_manager.resize_cpu(vm, cpu_cores)

    def resize_memory(self, vm: VM, memory_mb: int) -> None:
        return self.libvirt_manager.resize_memory(vm, memory_mb)

    def get_metrics(self, vm: VM) -> Dict[str, Any]:
        return self.libvirt_manager.get_metrics(vm)

    def create_disk(self, name: str, size_gb: int) -> Dict[str, Any]:
        return self.libvirt_manager.create_disk(name, size_gb)

    def attach_disk(self, disk_id: str, vm_name: str) -> None:
        return self.libvirt_manager.attach_disk(disk_id, vm_name)

    def detach_disk(self, disk_id: str) -> None:
        return self.libvirt_manager.detach_disk(disk_id)

    def get_console(self, vm: VM) -> 'VMConsole':
        if vm.id not in self.active_consoles:
            self.active_consoles[vm.id] = VMConsole(vm, self.libvirt_manager)
        return self.active_consoles[vm.id]

    def send_console_input(self, text: str) -> None:
        for console in self.active_consoles.values():
            if console.is_active:
                console.send_input(text)

    def list_disks(self) -> List[Dict]:
        """List all disks"""
        return self.libvirt_manager.list_disks()

    def create_disk(self, name: str, size_gb: int) -> Dict:
        """Create a new disk"""
        return self.libvirt_manager.create_disk(name, size_gb)

    def delete_disk(self, disk_id: str) -> None:
        """Delete a disk"""
        return self.libvirt_manager.delete_disk(disk_id)

    def attach_disk(self, disk_id: str, vm_name: str) -> None:
        """Attach a disk to a VM"""
        return self.libvirt_manager.attach_disk(disk_id, vm_name)

    def detach_disk(self, disk_id: str) -> None:
        """Detach a disk from its VM"""
        return self.libvirt_manager.detach_disk(disk_id)

    def get_disk(self, disk_id: str) -> Optional[Dict]:
        """Get disk details"""
        return self.libvirt_manager.get_disk(disk_id)

    def resize_disk(self, disk_id: str, new_size_gb: int) -> None:
        """Resize a disk"""
        return self.libvirt_manager.resize_disk(disk_id, new_size_gb)

    def get_machine_disks(self, vm_name: str) -> List[Dict]:
        """Get all disks attached to a VM"""
        return self.libvirt_manager.get_machine_disks(vm_name)

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