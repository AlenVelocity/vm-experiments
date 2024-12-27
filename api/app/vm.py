import subprocess
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
import shutil
from dataclasses import dataclass
import uuid
import os
import socket
import time
import libvirt
import xml.etree.ElementTree as ET
from .networking import NetworkManager, NetworkType
from .ip_manager import IPManager
from .disk_manager import DiskManager

@dataclass
class VMConfig:
    name: str
    cpu_cores: int = 2
    memory_mb: int = 2048
    disk_size_gb: int = 20
    network_name: Optional[str] = None
    cloud_init: Optional[Dict[str, Any]] = None

@dataclass
class VM:
    id: str
    name: str
    config: VMConfig
    network_info: Optional[Dict] = None
    ssh_port: Optional[int] = None

class LibvirtManager:
    def __init__(self):
        # Use absolute path for VM directory
        self.vm_dir = Path.home() / "vm-experiments" / "vms"
        self.vm_dir.mkdir(parents=True, exist_ok=True)
        self.network_manager = NetworkManager()
        self.ip_manager = IPManager()
        self.disk_manager = DiskManager(self.conn)
        
        # Try different connection methods
        connection_uris = [
            'qemu+unix:///system?socket=/opt/homebrew/var/run/libvirt/libvirt-sock',
            'qemu:///system',
            'qemu:///session'
        ]
        
        last_error = None
        for uri in connection_uris:
            try:
                self.conn = libvirt.open(uri)
                if self.conn:
                    print(f"Successfully connected to libvirt using {uri}")
                    break
            except libvirt.libvirtError as e:
                last_error = e
                print(f"Failed to connect using {uri}: {str(e)}")
                continue
        
        if not self.conn:
            raise Exception(f'Failed to connect to libvirt: {str(last_error)}')
            
        self._init_storage_pool()
        self.vms: Dict[str, VM] = self._load_vms()

    def __del__(self):
        if hasattr(self, 'conn'):
            try:
                self.conn.close()
            except:
                pass

    def _init_storage_pool(self):
        try:
            pool = self.conn.storagePoolLookupByName('default')
        except libvirt.libvirtError:
            pool_path = Path.home() / '.local/share/libvirt/images'
            pool_path.mkdir(parents=True, exist_ok=True)
            
            pool_xml = f"""
            <pool type='dir'>
                <name>default</name>
                <target>
                    <path>{pool_path}</path>
                </target>
            </pool>
            """
            pool = self.conn.storagePoolDefineXML(pool_xml)
            if not pool:
                raise Exception("Failed to create storage pool")
            
            pool.setAutostart(True)
            if not pool.isActive():
                pool.create()

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
        domain = ET.Element('domain', type='qemu')
        ET.SubElement(domain, 'name').text = vm.name
        ET.SubElement(domain, 'uuid').text = vm.id
        ET.SubElement(domain, 'memory', unit='MiB').text = str(vm.config.memory_mb)
        ET.SubElement(domain, 'currentMemory', unit='MiB').text = str(vm.config.memory_mb)
        vcpu = ET.SubElement(domain, 'vcpu', placement='static')
        vcpu.text = str(vm.config.cpu_cores)
        
        os = ET.SubElement(domain, 'os')
        ET.SubElement(os, 'type', arch='aarch64', machine='virt').text = 'hvm'
        ET.SubElement(os, 'boot', dev='hd')
        
        features = ET.SubElement(domain, 'features')
        ET.SubElement(features, 'gic', version='3')
        ET.SubElement(features, 'acpi')
        
        # Use custom CPU mode with cortex-a72 model for ARM64
        cpu = ET.SubElement(domain, 'cpu', mode='custom')
        ET.SubElement(cpu, 'model', fallback='allow').text = 'cortex-a72'
        topology = ET.SubElement(cpu, 'topology', sockets='1', cores=str(vm.config.cpu_cores), threads='1')
        
        devices = ET.SubElement(domain, 'devices')
        
        # Add emulator
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
        ET.SubElement(cloud_init_disk, 'target', dev='sda', bus='sata')
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
        hostfwd = ET.SubElement(interface, 'hostfwd', protocol='tcp', port=str(vm.ssh_port), to='22')
        
        # Add QEMU guest agent channel
        channel = ET.SubElement(devices, 'channel', type='unix')
        ET.SubElement(channel, 'target', type='virtio', name='org.qemu.guest_agent.0')
        
        return ET.tostring(domain, encoding='unicode')

    def _create_cloud_init_config(self, vm: VM) -> None:
        cloud_init_dir = self.vm_dir / "cloud-init"
        cloud_init_dir.mkdir(parents=True, exist_ok=True)

        # Start with default cloud-init config
        user_data = {
            "users": [{
                "name": "ubuntu",
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "shell": "/bin/bash",
                "ssh_authorized_keys": [
                    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC0WGP1EZykEtv5YGC9nMiPFW3U3DmZNzKFO5nEu6uozEHh4jLZzPNHSrw1BEQzzeQqwDPDHSFWqk0wF7HSLch+VHtcDyCqJpwRzxqz+YkHk7jm8HRJ4zN0o5W4jF+5lxMmBOWkqVv2U2K8DcwzqX5Qed3u7GZDM7kEKVeJtqk7/ayGRHvHmW2yCxKIyZQQErUkKmXCRvUG6zrHGGsxqQJlICh9UJyxkZM8JkYu8C4RjX9JGo+xKlxNCvGnLGz+LlPMY6qqhV+bDnCP+1v2IkCSxKRFGGW5EQlP4i8l77h/jL4CEoatP3XTLWNae+v9LvrFzUVz3JoFVvmmdFql"
                ]
            }],
            "write_files": [{
                "path": "/etc/netplan/50-cloud-init.yaml",
                "content": {
                    "network": {
                        "version": 2,
                        "ethernets": {
                            "eth0": {
                                "dhcp4": False,
                                "addresses": [f"{vm.network_info['private']['ip']}/24"],
                                "gateway4": vm.network_info['private']['gateway'],
                                "nameservers": {"addresses": ["8.8.8.8", "8.8.4.4"]}
                            }
                        }
                    }
                }
            }]
        }

        # Add public network configuration if available
        if 'public' in vm.network_info:
            user_data["write_files"][0]["content"]["network"]["ethernets"]["eth1"] = {
                "dhcp4": False,
                "addresses": [f"{vm.network_info['public']['ip']}/24"],
                "gateway4": vm.network_info['public']['gateway'],
                "nameservers": {"addresses": ["8.8.8.8", "8.8.4.4"]}
            }

        # Merge with custom cloud-init config if provided
        if vm.config.cloud_init:
            self._merge_cloud_init(user_data, vm.config.cloud_init)

        # Write cloud-init files
        (cloud_init_dir / "user-data").write_text("#cloud-config\n" + json.dumps(user_data, indent=2))
        (cloud_init_dir / "meta-data").write_text(f"""instance-id: {vm.id}
local-hostname: {vm.name}""")

        # Create network-config
        network_config = """version: 2
ethernets:
  eth0:
    dhcp4: false"""
        (cloud_init_dir / "network-config").write_text(network_config)

        # Create the cloud-init ISO
        subprocess.run([
            'mkisofs',
            '-output', str(self.vm_dir / f"{vm.name}-cloud-init.iso"),
            '-volid', 'cidata',
            '-joliet',
            '-rock',
            '-input-charset', 'utf-8',
            str(cloud_init_dir / "user-data"),
            str(cloud_init_dir / "meta-data"),
            str(cloud_init_dir / "network-config")
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

    def create_vm(self, name: str, vpc_name: str) -> VM:
        vm_id = str(uuid.uuid4())
        config = VMConfig(name=name, network_name=vpc_name)
        vm = VM(id=vm_id, name=name, config=config)
        vm_path = self.vm_dir / vm_id
        vm_path.mkdir(parents=True, exist_ok=True)
        vm.ssh_port = self._find_free_port()

        # Get an available public IP
        public_ip = self.ip_manager.get_available_ip()
        if public_ip:
            self.ip_manager.attach_ip(public_ip, vm_id)
        else:
            logger.warning(f"No available public IPs for VM {name}")
            public_ip = None

        # Generate unique network information
        vm_number = int(vm_id[-4:], 16) % 254  # Use last 4 chars of UUID as hex number
        subnet = (vm_number // 254) + 1  # Increment subnet for each 254 VMs
        host = (vm_number % 254) + 1     # Host number 1-254 within subnet

        # Create network information
        vm.network_info = {
            'private': {
                'ip': f"192.168.{subnet}.{host}",
                'subnet_mask': "255.255.255.0",
                'gateway': f"192.168.{subnet}.1",
                'network_name': f"{vpc_name}-private"
            }
        }

        # Add public network info if IP is available
        if public_ip:
            vm.network_info['public'] = {
                'ip': public_ip,
                'subnet_mask': "255.255.255.0",
                'gateway': f"10.{subnet}.{host}.1",
                'network_name': f"{vpc_name}-public"
            }

        # Save VM configuration
        with open(vm_path / "config.json", "w") as f:
            config_dict = {
                "name": config.name,
                "cpu_cores": config.cpu_cores,
                "memory_mb": config.memory_mb,
                "disk_size_gb": config.disk_size_gb,
                "network_name": config.network_name,
                "ssh_port": vm.ssh_port,
                "network_info": vm.network_info
            }
            json.dump(config_dict, f)

        # Create the VM
        self._create_vm_disk(vm)
        self._create_cloud_init_config(vm)
        self._start_vm(vm)

        return vm

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
            snapshot_xml = f"""
            <domainsnapshot>
                <name>{name}</name>
                <description>{description}</description>
            </domainsnapshot>
            """
            snapshot = domain.snapshotCreateXML(snapshot_xml)
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