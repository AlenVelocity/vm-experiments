import subprocess
import json
from pathlib import Path
from typing import Optional, List, Dict
import shutil
from dataclasses import dataclass
import uuid
import os
import socket
import time
import libvirt
import xml.etree.ElementTree as ET
from .networking import NetworkManager, NetworkType

@dataclass
class VMConfig:
    name: str
    cpu_cores: int = 2
    memory_mb: int = 2048
    disk_size_gb: int = 20
    network_name: Optional[str] = None

@dataclass
class VM:
    id: str
    name: str
    config: VMConfig
    network_info: Optional[Dict] = None
    ssh_port: Optional[int] = None

class LibvirtManager:
    def __init__(self):
        self.vm_dir = Path("vms")
        self.vm_dir.mkdir(parents=True, exist_ok=True)
        self.network_manager = NetworkManager()
        self.conn = libvirt.open('qemu:///session')
        if not self.conn:
            raise Exception('Failed to connect to QEMU/KVM')
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
        ET.SubElement(features, 'hvf')
        cpu = ET.SubElement(domain, 'cpu', mode='host-passthrough')
        devices = ET.SubElement(domain, 'devices')
        ET.SubElement(devices, 'emulator').text = '/opt/homebrew/bin/qemu-system-aarch64'
        disk = ET.SubElement(devices, 'disk', type='file', device='disk')
        ET.SubElement(disk, 'driver', name='qemu', type='qcow2')
        ET.SubElement(disk, 'source', file=str(disk_path))
        ET.SubElement(disk, 'target', dev='vda', bus='virtio')
        interface = ET.SubElement(devices, 'interface', type='user')
        ET.SubElement(interface, 'model', type='virtio')
        ET.SubElement(interface, 'hostfwd', protocol='tcp', port=str(vm.ssh_port), to='22')
        console = ET.SubElement(devices, 'console', type='pty')
        ET.SubElement(console, 'target', type='serial', port='0')
        serial = ET.SubElement(devices, 'serial', type='pty')
        ET.SubElement(serial, 'target', port='0')
        return ET.tostring(domain, encoding='unicode')

    def create_vm(self, name: str, vpc_name: str) -> VM:
        vm_id = str(uuid.uuid4())
        config = VMConfig(name=name, network_name=vpc_name)
        vm = VM(id=vm_id, name=name, config=config)
        vm_path = self.vm_dir / vm_id
        vm_path.mkdir(parents=True, exist_ok=True)
        vm.ssh_port = self._find_free_port()

        with open(vm_path / "config.json", "w") as f:
            config_dict = {
                "name": config.name,
                "cpu_cores": config.cpu_cores,
                "memory_mb": config.memory_mb,
                "disk_size_gb": config.disk_size_gb,
                "network_name": config.network_name,
                "ssh_port": vm.ssh_port
            }
            json.dump(config_dict, f)

        try:
            pool = self.conn.storagePoolLookupByName('default')
            vol_xml = f"""
            <volume type='file'>
                <name>{name}.qcow2</name>
                <capacity unit='G'>{config.disk_size_gb}</capacity>
                <target>
                    <format type='qcow2'/>
                </target>
            </volume>
            """
            volume = pool.createXML(vol_xml, 0)
            if not volume:
                raise Exception("Failed to create storage volume")

            domain_xml = self._generate_domain_xml(vm, Path(volume.path()))
            domain = self.conn.defineXML(domain_xml)
            if not domain:
                raise Exception("Failed to define VM domain")

            self.vms[vm_id] = vm
            return vm
            
        except libvirt.libvirtError as e:
            if vm_path.exists():
                shutil.rmtree(vm_path)
            raise Exception(f"Failed to create VM: {str(e)}")

    def start_vm(self, vm_id: str) -> bool:
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            if domain.isActive():
                return True
            domain.create()
            return True
        except libvirt.libvirtError as e:
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
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        try:
            domain = self.conn.lookupByName(vm.name)
            if domain.isActive():
                domain.destroy()
            
            pool = self.conn.storagePoolLookupByName('default')
            try:
                volume = pool.storageVolLookupByName(f"{vm.name}.qcow2")
                volume.delete(0)
            except libvirt.libvirtError:
                pass

            domain.undefineFlags(
                libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE |
                libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
            )
            
            vm_path = self.vm_dir / vm_id
            if vm_path.exists():
                shutil.rmtree(vm_path)

            del self.vms[vm_id]
            return True
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to delete VM: {str(e)}")

    def get_vm_status(self, vm_id: str) -> Dict:
        vm = self.vms.get(vm_id)
        if not vm:
            raise Exception("VM not found")

        try:
            domain = self.conn.lookupByName(vm.name)
            state, reason = domain.state()
            info = domain.info()
            memory_kb = info[2]
            
            status = {
                "name": vm.name,
                "state": self._get_state_name(state),
                "memory_mb": memory_kb // 1024,
                "cpu_cores": info[3],
                "network": vm.config.network_name,
                "ssh_port": vm.ssh_port
            }
            
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