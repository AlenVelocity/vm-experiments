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
from dataclasses import dataclass
import libvirt
import xml.etree.ElementTree as ET
from .networking import NetworkManager, NetworkType
from .ip_manager import IPManager
from .disk_manager import DiskManager
from datetime import datetime
from bs4 import BeautifulSoup
import re
import traceback

# Configure logging
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
    image_id: Optional[str] = None  # For selecting daily Ubuntu images

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
            # For M1 Macs, we need to use the session URI instead of system
            self.conn = libvirt.open('qemu:///session')
            if not self.conn:
                raise Exception("Failed to open connection to qemu:///session")
            
            self.vm_dir = Path(__file__).parent.parent / "vms"
            self.vm_dir.mkdir(parents=True, exist_ok=True)
            
            # Initialize storage pool first
            self._init_storage_pool()
            
            self.network_manager = NetworkManager(self.conn)
            self.ip_manager = IPManager()
            self.disk_manager = DiskManager(self.conn)
            
            self.vms = {}  # Dictionary to store VM instances
            
            logger.info("LibvirtManager initialized successfully")
            self.ubuntu_daily_base_url = "https://cloud-images.ubuntu.com/focal/current/"
        except Exception as e:
            logger.error(f"Failed to initialize LibvirtManager: {str(e)}")
            raise

    def __del__(self):
        if hasattr(self, 'conn'):
            try:
                self.conn.close()
            except:
                pass

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

    def _load_vms(self) -> Dict[str, VM]:
        vms = {}
        for vm_dir in self.vm_dir.glob("*"):
            if not vm_dir.is_dir():
                continue
            
            config_file = vm_dir / "config.json"
            if not config_file.exists():
                continue

            try:
                with open(config_file) as f:
                    config_data = json.load(f)
                    config = VMConfig(**config_data)
                    vm = VM(
                        id=vm_dir.name,
                        name=config.name,
                        config=config,
                        network_info=config_data.get("network_info"),
                        ssh_port=config_data.get("ssh_port")
                    )
                    vms[vm.id] = vm
            except Exception:
                continue
        return vms

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

    def _create_cloud_init_config(self, vm: VM) -> None:
        """Create cloud-init configuration files."""
        vm_dir = self.vm_dir / vm.name
        vm_dir.mkdir(parents=True, exist_ok=True)
        
        # Default cloud-init config if none provided
        if not vm.config.cloud_init:
            vm.config.cloud_init = {
                'hostname': vm.name,
                'users': [{
                    'name': 'ubuntu',
                    'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                    'shell': '/bin/bash',
                    'ssh_authorized_keys': []
                }]
            }
        
        # Write meta-data
        meta_data = f"""instance-id: {vm.id}
local-hostname: {vm.name}
"""
        (vm_dir / 'meta-data').write_text(meta_data)
        
        # Write user-data
        user_data = f"""#cloud-config
hostname: {vm.config.cloud_init['hostname']}
users:
  - name: {vm.config.cloud_init['users'][0]['name']}
    sudo: {vm.config.cloud_init['users'][0]['sudo']}
    shell: {vm.config.cloud_init['users'][0]['shell']}
    ssh_authorized_keys: {json.dumps(vm.config.cloud_init['users'][0]['ssh_authorized_keys'])}

# Configure for ARM64
apt:
  primary:
    - arches: [arm64, default]
      uri: http://ports.ubuntu.com/ubuntu-ports/

# Install required packages
packages:
  - qemu-guest-agent
  - cloud-init
  - openssh-server

# Enable services
runcmd:
  - systemctl enable qemu-guest-agent
  - systemctl start qemu-guest-agent
  - systemctl enable ssh
  - systemctl start ssh

# Configure networking
network:
  version: 2
  ethernets:
    enp0s1:
      dhcp4: true
      dhcp6: false
"""
        (vm_dir / 'user-data').write_text(user_data)
        
        # Create ISO file
        iso_path = self.vm_dir / f"{vm.name}-cloud-init.iso"
        subprocess.run([
            'mkisofs',
            '-output', str(iso_path),
            '-volid', 'cidata',
            '-joliet',
            '-rock',
            str(vm_dir / 'user-data'),
            str(vm_dir / 'meta-data')
        ], check=True)

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

    def create_vm(self, config: VMConfig) -> VM:
        """Create a new VM."""
        try:
            # Create images directory if it doesn't exist
            images_dir = self.vm_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            
            # Handle image selection and download
            if config.image_id and config.image_id != 'default':
                # It's a daily build
                image_url = f"https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-arm64.img"
                image_path = images_dir / f"ubuntu-{config.image_id}.img"
            else:
                # Use default image
                image_url = "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-arm64.img"
                image_path = images_dir / "ubuntu-focal-default.img"
            
            # Download image if not exists
            if not image_path.exists():
                logger.info(f"Downloading Ubuntu image from {image_url}")
                try:
                    response = requests.get(image_url, stream=True)
                    response.raise_for_status()
                    
                    with open(image_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    logger.info(f"Successfully downloaded image to {image_path}")
                except Exception as e:
                    logger.error(f"Failed to download image: {e}")
                    if image_path.exists():
                        image_path.unlink()
                    raise
            
            # Create VM directory
            vm_id = str(uuid.uuid4())[:8]
            vm_dir = self.vm_dir / vm_id
            vm_dir.mkdir(parents=True, exist_ok=True)
            
            # Create disk image
            disk_path = vm_dir / f"{config.name}.qcow2"
            logger.info(f"Creating disk image at {disk_path}")
            subprocess.run([
                'qemu-img', 'create',
                '-f', 'qcow2',
                '-F', 'qcow2',
                '-b', str(image_path),
                str(disk_path),
                f"{config.disk_size_gb}G"
            ], check=True)
            logger.info("Successfully created disk image")
            
            # Create VM instance
            vm = VM(id=vm_id, name=config.name, config=config)
            vm.ssh_port = self._find_free_port()
            
            # Get an available public IP
            public_ip = self.ip_manager.get_available_ip()
            if public_ip:
                self.ip_manager.attach_ip(public_ip, vm_id)
            else:
                logger.warning(f"No available public IPs for VM {config.name}")
                public_ip = None
            
            # Generate unique network information
            vm_number = int(vm_id[-4:], 16) % 254
            subnet = (vm_number // 254) + 1
            host = (vm_number % 254) + 1
            
            # Create network information
            vm.network_info = {
                'private': {
                    'ip': f"192.168.{subnet}.{host}",
                    'subnet_mask': "255.255.255.0",
                    'gateway': f"192.168.{subnet}.1",
                    'network_name': f"{config.network_name}-private" if config.network_name else "default-private"
                }
            }
            
            # Add public network info if IP is available
            if public_ip:
                vm.network_info['public'] = {
                    'ip': public_ip,
                    'subnet_mask': "255.255.255.0",
                    'gateway': f"10.{subnet}.{host}.1",
                    'network_name': f"{config.network_name}-public" if config.network_name else "default-public"
                }
            
            # Save VM configuration
            with open(vm_dir / "config.json", "w") as f:
                config_dict = {
                    "name": config.name,
                    "cpu_cores": config.cpu_cores,
                    "memory_mb": config.memory_mb,
                    "disk_size_gb": config.disk_size_gb,
                    "network_name": config.network_name,
                    "ssh_port": vm.ssh_port,
                    "network_info": vm.network_info,
                    "image_id": config.image_id
                }
                json.dump(config_dict, f)
            
            # Create cloud-init config and start VM
            self._create_cloud_init_config(vm)
            self._start_vm(vm)
            
            # Store VM in manager
            self.vms[vm_id] = vm
            
            return vm
            
        except Exception as e:
            logger.error(f"Error creating VM: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    def start_vm(self, vm_id: str) -> bool:
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            if domain.isActive():
                return True

            # Create and clean up the VM directory
            script_dir = self.vm_dir / vm_id
            script_dir.mkdir(parents=True, exist_ok=True)
            
            # Clean up any stale files
            for file in ["qemu.pid", "disk.qcow2", "qemu.log"]:
                file_path = script_dir / file
                try:
                    if file_path.exists():
                        file_path.unlink()
                except Exception as e:
                    print(f"Warning: Failed to remove {file}: {str(e)}")

            # Get the domain XML
            xml = domain.XMLDesc()
            root = ET.fromstring(xml)
            
            # Extract disk path
            disk_elem = root.find('.//disk[@device="disk"]/source')
            if disk_elem is None:
                raise Exception("Could not find disk path in domain XML")
            disk_path = disk_elem.get('file')
            
            if not Path(disk_path).exists():
                raise Exception(f"Source disk not found: {disk_path}")
            
            # Extract memory and CPU info
            memory_mb = int(root.find('.//memory').text) // 1024
            vcpus = root.find('.//vcpu').text
            
            # Find a free VNC port
            vnc_port = self._find_free_port(5900)
            vnc_display = vnc_port - 5900
            
            # Build QEMU command
            qemu_cmd = [
                '/opt/homebrew/bin/qemu-system-aarch64',
                '-M', 'virt',
                '-cpu', 'cortex-a72',
                '-accel', 'hvf',
                '-smp', vcpus,
                '-m', str(memory_mb),
                # Main disk
                '-drive', f'file={disk_path},if=virtio,format=qcow2',
                # Cloud-init disk
                '-drive', f'file={self.vm_dir / f"{vm.name}-cloud-init.iso"},if=virtio,format=raw,media=cdrom',
                # Network with SSH port forwarding
                '-device', 'virtio-net-pci,netdev=net0',
                '-netdev', f'user,id=net0,hostfwd=tcp::{vm.ssh_port}-:22',
                # VNC
                '-vnc', f':{vnc_display}',
                '-display', 'default,show-cursor=on',
                # Devices
                '-device', 'virtio-gpu-pci',
                '-device', 'qemu-xhci',
                '-device', 'usb-kbd',
                '-device', 'usb-tablet',
                # QEMU guest agent
                '-chardev', 'socket,path=/tmp/qga.sock,server=on,wait=off,id=qga0',
                '-device', 'virtio-serial',
                '-device', 'virtserialport,chardev=qga0,name=org.qemu.guest_agent.0',
                # Logging and monitoring
                '-pidfile', str(script_dir / "qemu.pid"),
                '-D', str(script_dir / "qemu.log"),
                '-d', 'guest_errors,unimp',
                '-serial', 'mon:stdio'
            ]
            
            # Create the start script
            with open(script_dir / "start_vm.sh", "w") as f:
                f.write(f"""#!/bin/bash
cd "{script_dir}"

# Verify disk exists
if [ ! -f "{disk_path}" ]; then
    echo "Error: Disk file not found at {disk_path}"
    exit 1
fi

# Clean up any stale files
rm -f qemu.pid

echo "Starting QEMU VM..."
echo "Log file: {script_dir}/qemu.log"
echo "PID file: {script_dir}/qemu.pid"
echo "VNC port: {vnc_port} (display :{vnc_display})"
echo "SSH port: {vm.ssh_port} (use: ssh -p {vm.ssh_port} ubuntu@localhost)"
echo "Default password: ubuntu"
echo "Network Information:"
echo "  Private IP: {vm.network_info['private']['ip']}"
echo "  Public IP:  {vm.network_info['public']['ip']}"
echo "----------------------------------------"

# Start QEMU and save its PID
{' '.join(qemu_cmd)} 2>&1 | tee -a qemu.log
""")
            
            # Create a monitor script
            with open(script_dir / "monitor_vm.sh", "w") as f:
                f.write(f"""#!/bin/bash
cd "{script_dir}"

# Wait for QEMU to start and create PID file
sleep 2
if [ -f "qemu.pid" ]; then
    pid=$(cat qemu.pid)
    echo "QEMU process started with PID: $pid"
    
    # Wait for SSH to become available
    echo "Waiting for SSH to become available..."
    for i in $(seq 1 30); do
        if nc -z localhost {vm.ssh_port} 2>/dev/null; then
            echo "SSH is now available on port {vm.ssh_port}"
            break
        fi
        sleep 2
    done
    
    # Monitor the QEMU process
    while kill -0 $pid 2>/dev/null; do
        sleep 5
    done
    echo "QEMU process terminated"
fi

# Clean up files
rm -f qemu.pid disk.qcow2 qemu.log
""")
            
            # Make scripts executable
            script_path = script_dir / "start_vm.sh"
            monitor_script_path = script_dir / "monitor_vm.sh"
            script_path.chmod(0o755)
            monitor_script_path.chmod(0o755)
            
            # Open a new terminal window and run the VM
            terminal_cmd = [
                'osascript',
                '-e', 'tell application "Terminal"',
                '-e', f'do script "clear; echo \\"Starting VM {vm.name}...\\"; {script_path}"',
                '-e', 'end tell'
            ]
            subprocess.Popen(terminal_cmd)
            
            # Run the monitor script in the background
            subprocess.Popen([str(monitor_script_path)], 
                           stdout=subprocess.DEVNULL, 
                           stderr=subprocess.DEVNULL)
            
            print(f"""
VM {vm.name} is starting...
SSH access will be available at:
  ssh -p {vm.ssh_port} ubuntu@localhost
Default password: ubuntu

VNC access available at:
  localhost:{vnc_port} (display :{vnc_display})
""")
            return True
            
        except Exception as e:
            # Clean up any temporary files if there was an error
            try:
                script_dir = self.vm_dir / vm_id
                for file in ["qemu.pid", "disk.qcow2", "qemu.log"]:
                    file_path = script_dir / file
                    if file_path.exists():
                        file_path.unlink()
            except:
                pass
            raise Exception(f"Failed to start VM: {str(e)}")

    def stop_vm(self, vm_id: str) -> bool:
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            if not domain.isActive():
                return True
            domain.shutdown()
            for _ in range(30):
                if not domain.isActive():
                    return True
                time.sleep(1)
            domain.destroy()
            return True
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to stop VM: {str(e)}")

    def delete_vm(self, vm_id: str) -> bool:
        """Delete a VM and release its IP if it has one."""
        try:
            # Get VM's IPs before deletion
            vm = self.get_vm(vm_id)
            if vm and vm.network_info and 'public' in vm.network_info:
                public_ip = vm.network_info['public']['ip']
                try:
                    self.ip_manager.detach_ip(public_ip)
                except Exception as e:
                    logger.error(f"Error detaching IP {public_ip} from VM {vm_id}: {e}")

            # Proceed with normal VM deletion
            super().delete_vm(vm_id)
        except Exception as e:
            logger.error(f"Error deleting VM {vm_id}: {e}")
            raise

    def get_vm_status(self, vm_id: str) -> Dict:
        vm = self.vms.get(vm_id)
        if not vm:
            raise Exception("VM not found")

        try:
            domain = self.conn.lookupByName(vm.name)
            state, reason = domain.state()
            
            # Check if QEMU is actually running
            pid_file = self.vm_dir / vm_id / "qemu.pid"
            if pid_file.exists():
                try:
                    with open(pid_file) as f:
                        pid = int(f.read().strip())
                    # Check if process is running
                    os.kill(pid, 0)
                    # If we get here, process is running
                    state = libvirt.VIR_DOMAIN_RUNNING
                except (OSError, ValueError):
                    # Process is not running
                    state = libvirt.VIR_DOMAIN_SHUTOFF
                    if pid_file.exists():
                        pid_file.unlink()
            
            info = domain.info()
            memory_kb = info[2]
            
            # Get VNC port if available
            vnc_port = None
            network_info = {}
            
            if state == libvirt.VIR_DOMAIN_RUNNING:
                xml = domain.XMLDesc()
                root = ET.fromstring(xml)
                
                # Get VNC port
                graphics = root.find('.//graphics[@type="vnc"]')
                if graphics is not None:
                    vnc_port = graphics.get('port')
                
                # Get network interfaces and use stored network info
                interfaces = root.findall('.//interface')
                for idx, iface in enumerate(interfaces):
                    net_info = {
                        'type': iface.get('type'),
                        'model': iface.find('model').get('type') if iface.find('model') is not None else None,
                    }
                    
                    # Get MAC address
                    mac = iface.find('mac')
                    if mac is not None:
                        net_info['mac'] = mac.get('address')
                    
                    # Get forwarded ports
                    forwards = iface.findall('hostfwd')
                    if forwards:
                        net_info['forwarded_ports'] = []
                        for fwd in forwards:
                            net_info['forwarded_ports'].append({
                                'protocol': fwd.get('protocol'),
                                'host_port': fwd.get('port'),
                                'guest_port': fwd.get('to')
                            })
                    
                    # Use stored network information
                    if idx == 0 and vm.network_info:  # Private network
                        private_info = vm.network_info['private']
                        net_info.update({
                            'private_ip': private_info['ip'],
                            'network_name': private_info['network_name'],
                            'subnet_mask': private_info['subnet_mask'],
                            'gateway': private_info['gateway']
                        })
                    elif idx == 1 and vm.network_info:  # Public network
                        public_info = vm.network_info['public']
                        net_info.update({
                            'public_ip': public_info['ip'],
                            'network_name': public_info['network_name'],
                            'subnet_mask': public_info['subnet_mask'],
                            'gateway': public_info['gateway']
                        })
                    
                    network_info[f'net{idx}'] = net_info
            
            status = {
                "name": vm.name,
                "state": self._get_state_name(state),
                "memory_mb": memory_kb // 1024,
                "cpu_cores": info[3],
                "network": vm.config.network_name,
                "ssh_port": vm.ssh_port,
                "vnc_port": vnc_port,
                "network_interfaces": network_info,
                "connection_info": {
                    "ssh": f"ssh -p {vm.ssh_port} ubuntu@localhost",
                    "vnc": f"localhost:{vnc_port}" if vnc_port else None
                }
            }
            
            # Add public IP to connection info when running
            if state == libvirt.VIR_DOMAIN_RUNNING and vm.network_info and 'public' in vm.network_info:
                status["connection_info"]["public_ssh"] = f"ssh ubuntu@{vm.network_info['public']['ip']}"
            
            if state == libvirt.VIR_DOMAIN_RUNNING:
                cpu_stats = domain.getCPUStats(True)
                status.update({
                    "cpu_time": cpu_stats[0].get("cpu_time", 0),
                    "system_time": cpu_stats[0].get("system_time", 0)
                })
                
                mem_stats = domain.memoryStats()
                if mem_stats:
                    status.update({
                        "actual_memory_mb": mem_stats.get("actual", 0) // 1024,
                        "available_memory_mb": mem_stats.get("available", 0) // 1024
                    })
            
            return status
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to get VM status: {str(e)}")

    def _get_state_name(self, state: int) -> str:
        states = {
            libvirt.VIR_DOMAIN_NOSTATE: "no state",
            libvirt.VIR_DOMAIN_RUNNING: "running",
            libvirt.VIR_DOMAIN_BLOCKED: "blocked",
            libvirt.VIR_DOMAIN_PAUSED: "paused",
            libvirt.VIR_DOMAIN_SHUTDOWN: "shutting down",
            libvirt.VIR_DOMAIN_SHUTOFF: "shutoff",
            libvirt.VIR_DOMAIN_CRASHED: "crashed",
            libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended"
        }
        return states.get(state, "unknown")

    def create_snapshot(self, vm_id: str, name: str, description: str = "") -> bool:
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            
            # Get all attached disks
            xml = domain.XMLDesc()
            root = ET.fromstring(xml)
            disks = root.findall('.//disk[@device="disk"]')
            
            # Prepare snapshot XML with all disks
            snapshot_xml = f"""
            <domainsnapshot>
                <name>{name}</name>
                <description>{description}</description>
                <disks>
            """
            
            # Add each disk to the snapshot
            for disk in disks:
                target = disk.find('target')
                if target is not None:
                    dev = target.get('dev')
                    snapshot_xml += f"""
                    <disk name='{dev}'>
                        <source/>
                    </disk>
                    """
            
            snapshot_xml += """
                </disks>
            </domainsnapshot>
            """
            
            # Create snapshot with flags for disk snapshot
            flags = (libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY |
                    libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_ATOMIC)
            snapshot = domain.snapshotCreateXML(snapshot_xml, flags)
            
            return bool(snapshot)
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to create snapshot: {str(e)}")

    def list_snapshots(self, vm_id: str) -> List[Dict]:
        vm = self.vms.get(vm_id)
        if not vm:
            return []

        try:
            domain = self.conn.lookupByName(vm.name)
            snapshots = []
            for snapshot in domain.listAllSnapshots():
                snapshot_time = snapshot.getParent().getTime()
                snapshot_info = {
                    "name": snapshot.getName(),
                    "description": snapshot.getXMLDesc(),
                    "creation_time": snapshot_time.tv_sec if snapshot_time else None,
                    "state": snapshot.getState()[0],
                    "parent": snapshot.getParent().getName() if snapshot.getParent() else None
                }
                snapshots.append(snapshot_info)
            return snapshots
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to list snapshots: {str(e)}")

    def revert_to_snapshot(self, vm_id: str, snapshot_name: str) -> bool:
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            snapshot = domain.snapshotLookupByName(snapshot_name)
            
            if domain.isActive():
                domain.destroy()
            
            result = domain.revertToSnapshot(snapshot)
            return result == 0
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to revert to snapshot: {str(e)}")

    def delete_snapshot(self, vm_id: str, snapshot_name: str) -> bool:
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            snapshot = domain.snapshotLookupByName(snapshot_name)
            result = snapshot.delete()
            return result == 0
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to delete snapshot: {str(e)}")

    def create_snapshot_and_export(self, vm_id: str, name: str, export_path: Path) -> bool:
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            
            if domain.isActive():
                domain.suspend()
            
            try:
                pool = self.conn.storagePoolLookupByName('default')
                volume = pool.storageVolLookupByName(f"{vm.name}.qcow2")
                
                snapshot_xml = f"""
                <domainsnapshot>
                    <name>{name}</name>
                    <disk name='vda' snapshot='external'>
                        <source file='{export_path}'/>
                    </disk>
                </domainsnapshot>
                """
                
                snapshot = domain.snapshotCreateXML(snapshot_xml, 
                    libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY)
                
                return bool(snapshot)
            finally:
                if domain.isActive():
                    domain.resume()
                
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to create and export snapshot: {str(e)}")

    def import_snapshot(self, vm_id: str, snapshot_path: Path) -> bool:
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            
            if domain.isActive():
                domain.destroy()
            
            pool = self.conn.storagePoolLookupByName('default')
            volume = pool.storageVolLookupByName(f"{vm.name}.qcow2")
            
            import_xml = f"""
            <disk type='file' device='disk'>
                <driver name='qemu' type='qcow2'/>
                <source file='{snapshot_path}'/>
                <target dev='vda' bus='virtio'/>
            </disk>
            """
            
            flags = (libvirt.VIR_DOMAIN_BLOCK_COPY_REUSE_EXT |
                    libvirt.VIR_DOMAIN_BLOCK_COPY_SHALLOW)
            
            domain.blockCopy('vda', import_xml, flags=flags)
            return True
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to import snapshot: {str(e)}")

    def create_disk(self, name: str, size_gb: int) -> dict:
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

    def list_disks(self) -> List[dict]:
        """List all disks."""
        return self.disk_manager.list_disks()

    def get_disk(self, disk_id: str) -> Optional[dict]:
        """Get disk details."""
        disk = self.disk_manager.get_disk(disk_id)
        return disk.to_dict() if disk else None

    def resize_disk(self, disk_id: str, new_size_gb: int) -> None:
        """Resize a disk."""
        self.disk_manager.resize_disk(disk_id, new_size_gb)

    def get_machine_disks(self, vm_name: str) -> List[dict]:
        """Get all disks attached to a VM."""
        return self.disk_manager.get_machine_disks(vm_name)

    def create_incremental_snapshot(self, vm_id: str, name: str, parent_snapshot: str = None, description: str = "") -> bool:
        """Create an incremental snapshot that only stores changes since the parent snapshot."""
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            
            # Get all attached disks
            xml = domain.XMLDesc()
            root = ET.fromstring(xml)
            disks = root.findall('.//disk[@device="disk"]')
            
            # Prepare snapshot XML with all disks
            snapshot_xml = f"""
            <domainsnapshot>
                <name>{name}</name>
                <description>{description}</description>
                <parent>
                    <name>{parent_snapshot}</name>
                </parent>
                <disks>
            """
            
            # Add each disk to the snapshot with incremental backup
            for disk in disks:
                target = disk.find('target')
                if target is not None:
                    dev = target.get('dev')
                    snapshot_xml += f"""
                    <disk name='{dev}' snapshot='external'>
                        <driver type='qcow2'/>
                        <source/>
                    </disk>
                    """
            
            snapshot_xml += """
                </disks>
            </domainsnapshot>
            """
            
            # Create snapshot with flags for incremental backup
            flags = (libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY |
                    libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_REUSE_EXT)
            
            if parent_snapshot:
                parent = domain.snapshotLookupByName(parent_snapshot)
                if not parent:
                    raise Exception(f"Parent snapshot {parent_snapshot} not found")
            
            snapshot = domain.snapshotCreateXML(snapshot_xml, flags)
            return bool(snapshot)
            
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to create incremental snapshot: {str(e)}")

    def _create_vm_disk(self, vm: VM) -> None:
        """Create a disk for the VM using qemu-img."""
        try:
            # Get the storage pool
            pool = self.conn.storagePoolLookupByName('default')
            if not pool.isActive():
                pool.create()

            # On macOS, we need to use getXMLDesc to get pool info
            pool_xml = pool.XMLDesc(0)
            import xml.etree.ElementTree as ET
            pool_root = ET.fromstring(pool_xml)
            pool_path = pool_root.find('.//path').text
            disk_path = Path(pool_path) / f"{vm.name}.qcow2"

            # Create a new disk using qemu-img
            subprocess.run([
                'qemu-img', 'create',
                '-f', 'qcow2',
                str(disk_path),
                f"{vm.config.disk_size_gb}G"
            ], check=True)

            # Download Ubuntu cloud image if not exists
            ubuntu_img = self.vm_dir / "ubuntu-focal-server-cloudimg-arm64.img"
            if not ubuntu_img.exists():
                logger.info("Downloading Ubuntu cloud image...")
                response = requests.get(
                    "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-arm64.img",
                    stream=True
                )
                response.raise_for_status()
                with open(ubuntu_img, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

            # Convert and resize the Ubuntu image to our disk
            subprocess.run([
                'qemu-img', 'convert',
                '-f', 'qcow2',
                '-O', 'qcow2',
                str(ubuntu_img),
                str(disk_path)
            ], check=True)

            # Resize the disk to the specified size
            subprocess.run([
                'qemu-img', 'resize',
                str(disk_path),
                f"{vm.config.disk_size_gb}G"
            ], check=True)

            logger.info(f"Created disk for VM {vm.name} at {disk_path}")
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to create VM disk: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to download Ubuntu image: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to create VM disk: {str(e)}")

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