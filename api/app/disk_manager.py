import uuid
import json
from pathlib import Path
from typing import Dict, List, Optional
import libvirt
import logging
from dataclasses import dataclass, asdict
from .db import db

logger = logging.getLogger(__name__)

@dataclass
class Disk:
    id: str
    name: str
    size_gb: int
    attached_to: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)

class DiskManager:
    def __init__(self, conn: libvirt.virConnect):
        self.conn = conn

    def create_disk(self, name: str, size_gb: int) -> Disk:
        disk_id = str(uuid.uuid4())[:8]
        disk = Disk(disk_id, name, size_gb)
        
        # Create the disk file
        pool = self.conn.storagePoolLookupByName('default')
        vol_xml = f"""<volume type='file'>
            <name>{disk_id}.qcow2</name>
            <capacity unit='G'>{size_gb}</capacity>
            <target>
                <format type='qcow2'/>
            </target>
        </volume>"""
        
        try:
            volume = pool.createXML(vol_xml, 0)
            if not volume:
                raise Exception("Failed to create disk volume")
            
            # Save to database
            db.create_disk(disk_id, {
                'name': name,
                'size_gb': size_gb,
                'state': 'available'
            })
            
            return disk
            
        except libvirt.libvirtError as e:
            logger.error(f"Failed to create disk volume: {e}")
            raise Exception(f"Failed to create disk volume: {e}")

    def delete_disk(self, disk_id: str) -> None:
        disk_data = db.get_disk(disk_id)
        if not disk_data:
            raise ValueError(f"Disk {disk_id} not found")
        
        if disk_data['attached_to']:
            raise ValueError(f"Disk {disk_id} is still attached to a machine")
        
        # Delete the disk file
        try:
            pool = self.conn.storagePoolLookupByName('default')
            volume = pool.storageVolLookupByName(f"{disk_id}.qcow2")
            volume.delete(0)
        except libvirt.libvirtError as e:
            logger.error(f"Failed to delete disk volume: {e}")
        
        db.delete_disk(disk_id)

    def attach_disk(self, disk_id: str, vm_id: str) -> None:
        disk_data = db.get_disk(disk_id)
        if not disk_data:
            raise ValueError(f"Disk {disk_id} not found")
        
        if disk_data.get('attached_to'):
            raise ValueError(f"Disk {disk_id} is already attached to a machine")
        
        try:
            # Get VM domain using the VM ID
            domain = None
            try:
                domain = self.conn.lookupByName(vm_id)
            except libvirt.libvirtError:
                logger.error(f"Could not find VM domain for {vm_id}")
                raise ValueError(f"VM {vm_id} not found")

            pool = self.conn.storagePoolLookupByName('default')
            volume = pool.storageVolLookupByName(f"{disk_id}.qcow2")
            
            # Find next available device name
            xml = domain.XMLDesc()
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml)
            existing_disks = root.findall('.//disk[@device="disk"]/target')
            used_devs = {disk.get('dev') for disk in existing_disks}
            
            # Generate device name (vdb, vdc, etc.)
            for c in 'bcdefghijklmnopqrstuvwxyz':
                dev = f'vd{c}'
                if dev not in used_devs:
                    break
            else:
                raise Exception("No available device names")
            
            # Attach disk
            disk_xml = f"""
            <disk type='file' device='disk'>
                <driver name='qemu' type='qcow2'/>
                <source file='{volume.path()}'/>
                <target dev='{dev}' bus='virtio'/>
            </disk>
            """
            
            domain.attachDevice(disk_xml)
            
            # Update database
            db.update_disk(disk_id, {
                'attached_to': vm_id,
                'state': 'attached'
            })
            
        except libvirt.libvirtError as e:
            logger.error(f"Failed to attach disk: {e}")
            raise Exception(f"Failed to attach disk: {e}")
        except Exception as e:
            logger.error(f"Unexpected error attaching disk: {e}")
            raise

    def detach_disk(self, disk_id: str) -> None:
        disk_data = db.get_disk(disk_id)
        if not disk_data:
            raise ValueError(f"Disk {disk_id} not found")
        
        if not disk_data['attached_to']:
            raise ValueError(f"Disk {disk_id} is not attached to any machine")
        
        try:
            domain = self.conn.lookupByName(disk_data['attached_to'])
            pool = self.conn.storagePoolLookupByName('default')
            volume = pool.storageVolLookupByName(f"{disk_id}.qcow2")
            
            # Find the disk in domain XML
            xml = domain.XMLDesc()
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml)
            for disk in root.findall('.//disk[@device="disk"]'):
                source = disk.find('source')
                if source is not None and source.get('file') == volume.path():
                    disk_xml = ET.tostring(disk, encoding='unicode')
                    domain.detachDevice(disk_xml)
                    break
            
            # Update database
            db.update_disk(disk_id, {
                'attached_to': None,
                'state': 'available'
            })
            
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to detach disk: {e}")

    def list_disks(self) -> List[Dict]:
        return db.list_disks()

    def get_disk(self, disk_id: str) -> Optional[Disk]:
        disk_data = db.get_disk(disk_id)
        if disk_data:
            return Disk(
                id=disk_data['id'],
                name=disk_data['name'],
                size_gb=disk_data['size_gb'],
                attached_to=disk_data.get('attached_to')
            )
        return None

    def resize_disk(self, disk_id: str, new_size_gb: int) -> None:
        disk_data = db.get_disk(disk_id)
        if not disk_data:
            raise ValueError(f"Disk {disk_id} not found")
        
        if disk_data['attached_to']:
            raise ValueError(f"Cannot resize attached disk {disk_id}")
        
        try:
            pool = self.conn.storagePoolLookupByName('default')
            volume = pool.storageVolLookupByName(f"{disk_id}.qcow2")
            volume.resize(new_size_gb * 1024 * 1024 * 1024)
            
            # Update database
            db.update_disk(disk_id, {
                'size_gb': new_size_gb
            })
            
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to resize disk: {e}")

    def get_machine_disks(self, vm_name: str) -> List[Dict]:
        disks = db.list_disks()
        return [disk for disk in disks if disk.get('attached_to') == vm_name] 