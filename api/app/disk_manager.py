from typing import Dict, List, Optional
import json
from pathlib import Path
import subprocess
import logging
import uuid
import libvirt
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

class Disk:
    def __init__(self, disk_id: str, name: str, size_gb: int, 
                 attached_to: Optional[str] = None, device: Optional[str] = None):
        self.disk_id = disk_id
        self.name = name
        self.size_gb = size_gb
        self.attached_to = attached_to
        self.device = device  # e.g., vdb, vdc, etc.

    def to_dict(self) -> dict:
        return {
            "disk_id": self.disk_id,
            "name": self.name,
            "size_gb": self.size_gb,
            "attached_to": self.attached_to,
            "device": self.device
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Disk':
        return cls(
            disk_id=data["disk_id"],
            name=data["name"],
            size_gb=data["size_gb"],
            attached_to=data.get("attached_to"),
            device=data.get("device")
        )

class DiskManager:
    def __init__(self, conn: libvirt.virConnect):
        self.disks_dir = Path("disks")
        self.disks_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.disks_dir / "disks.json"
        self.disks: Dict[str, Disk] = self._load_disks()
        self.conn = conn

    def _load_disks(self) -> Dict[str, Disk]:
        if self.config_file.exists():
            with open(self.config_file) as f:
                data = json.load(f)
                return {disk_id: Disk.from_dict(disk_data) 
                        for disk_id, disk_data in data.items()}
        return {}

    def _save_disks(self) -> None:
        with open(self.config_file, "w") as f:
            json.dump({disk_id: disk.to_dict() for disk_id, disk in self.disks.items()}, 
                     f, indent=2)

    def _get_next_device(self, domain: libvirt.virDomain) -> str:
        xml = domain.XMLDesc()
        root = ET.fromstring(xml)
        used_devices = {disk.get('dev') for disk in root.findall('.//disk/target')}
        
        # Start from vdb (vda is usually the boot disk)
        for c in 'bcdefghijklmnopqrstuvwxyz':
            device = f'vd{c}'
            if device not in used_devices:
                return device
        raise ValueError("No available device names")

    def create_disk(self, name: str, size_gb: int) -> Disk:
        disk_id = str(uuid.uuid4())[:8]
        disk = Disk(disk_id, name, size_gb)
        
        # Create the disk file
        pool = self.conn.storagePoolLookupByName('default')
        vol_xml = f"""
        <volume type='file'>
            <name>{disk_id}.qcow2</name>
            <capacity unit='G'>{size_gb}</capacity>
            <target>
                <format type='qcow2'/>
            </target>
        </volume>
        """
        volume = pool.createXML(vol_xml, 0)
        if not volume:
            raise Exception("Failed to create disk volume")
        
        self.disks[disk_id] = disk
        self._save_disks()
        return disk

    def delete_disk(self, disk_id: str) -> None:
        if disk_id not in self.disks:
            raise ValueError(f"Disk {disk_id} not found")
        
        disk = self.disks[disk_id]
        if disk.attached_to:
            raise ValueError(f"Disk {disk_id} is still attached to a machine")
        
        # Delete the disk file
        try:
            pool = self.conn.storagePoolLookupByName('default')
            volume = pool.storageVolLookupByName(f"{disk_id}.qcow2")
            volume.delete(0)
        except libvirt.libvirtError as e:
            logger.error(f"Failed to delete disk volume: {e}")
        
        del self.disks[disk_id]
        self._save_disks()

    def attach_disk(self, disk_id: str, domain_name: str) -> None:
        if disk_id not in self.disks:
            raise ValueError(f"Disk {disk_id} not found")
        
        disk = self.disks[disk_id]
        if disk.attached_to:
            raise ValueError(f"Disk {disk_id} is already attached to a machine")
        
        try:
            domain = self.conn.lookupByName(domain_name)
            device = self._get_next_device(domain)
            
            pool = self.conn.storagePoolLookupByName('default')
            volume = pool.storageVolLookupByName(f"{disk_id}.qcow2")
            
            disk_xml = f"""
            <disk type='file' device='disk'>
                <driver name='qemu' type='qcow2'/>
                <source file='{volume.path()}'/>
                <target dev='{device}' bus='virtio'/>
            </disk>
            """
            
            flags = libvirt.VIR_DOMAIN_DEVICE_MODIFY_CONFIG
            if domain.isActive():
                flags |= libvirt.VIR_DOMAIN_DEVICE_MODIFY_LIVE
            
            domain.attachDeviceFlags(disk_xml, flags)
            
            disk.attached_to = domain_name
            disk.device = device
            self._save_disks()
            
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to attach disk: {str(e)}")

    def detach_disk(self, disk_id: str) -> None:
        if disk_id not in self.disks:
            raise ValueError(f"Disk {disk_id} not found")
        
        disk = self.disks[disk_id]
        if not disk.attached_to:
            raise ValueError(f"Disk {disk_id} is not attached to any machine")
        
        try:
            domain = self.conn.lookupByName(disk.attached_to)
            
            disk_xml = f"""
            <disk type='file' device='disk'>
                <target dev='{disk.device}'/>
            </disk>
            """
            
            flags = libvirt.VIR_DOMAIN_DEVICE_MODIFY_CONFIG
            if domain.isActive():
                flags |= libvirt.VIR_DOMAIN_DEVICE_MODIFY_LIVE
            
            domain.detachDeviceFlags(disk_xml, flags)
            
            disk.attached_to = None
            disk.device = None
            self._save_disks()
            
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to detach disk: {str(e)}")

    def list_disks(self) -> List[dict]:
        return [disk.to_dict() for disk in self.disks.values()]

    def get_disk(self, disk_id: str) -> Optional[Disk]:
        return self.disks.get(disk_id)

    def resize_disk(self, disk_id: str, new_size_gb: int) -> None:
        if disk_id not in self.disks:
            raise ValueError(f"Disk {disk_id} not found")
        
        disk = self.disks[disk_id]
        if disk.attached_to:
            raise ValueError(f"Disk {disk_id} must be detached before resizing")
        
        try:
            pool = self.conn.storagePoolLookupByName('default')
            volume = pool.storageVolLookupByName(f"{disk_id}.qcow2")
            volume.resize(new_size_gb * 1024 * 1024 * 1024)
            
            disk.size_gb = new_size_gb
            self._save_disks()
            
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to resize disk: {str(e)}")

    def get_machine_disks(self, domain_name: str) -> List[dict]:
        return [disk.to_dict() for disk in self.disks.values() 
                if disk.attached_to == domain_name] 