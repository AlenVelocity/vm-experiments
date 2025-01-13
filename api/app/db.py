import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.db_dir = Path(__file__).parent.parent / "data"
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.vms_file = self.db_dir / "vms.json"
        self.disks_file = self.db_dir / "disks.json"
        self.ips_file = self.db_dir / "ips.json"
        self._ensure_files_exist()

    def _ensure_files_exist(self):
        """Ensure database files exist"""
        if not self.vms_file.exists():
            self.vms_file.write_text("{}")
        if not self.disks_file.exists():
            self.disks_file.write_text("{}")
        if not self.ips_file.exists():
            self.ips_file.write_text('{"ips": {}}')

    def _load_vms(self) -> Dict[str, Any]:
        """Load VMs from the database file"""
        try:
            return json.loads(self.vms_file.read_text())
        except Exception as e:
            logger.error(f"Error loading VMs: {str(e)}")
            return {}

    def _save_vms(self, vms: Dict[str, Any]):
        """Save VMs to the database file"""
        try:
            self.vms_file.write_text(json.dumps(vms, indent=2))
        except Exception as e:
            logger.error(f"Error saving VMs: {str(e)}")
            raise

    def list_vms(self) -> List[Dict[str, Any]]:
        """List all VMs"""
        vms = self._load_vms()
        return [{"id": vm_id, **vm_data} for vm_id, vm_data in vms.items()]

    def get_vm(self, vm_id: str) -> Dict[str, Any]:
        """Get a VM by ID"""
        vms = self._load_vms()
        vm_data = vms.get(vm_id)
        if vm_data:
            return {"id": vm_id, **vm_data}
        return None

    def create_vm(self, vm_id: str, data: Dict[str, Any]):
        """Create a new VM"""
        vms = self._load_vms()
        if vm_id in vms:
            raise Exception(f"VM with ID {vm_id} already exists")
        vms[vm_id] = data
        self._save_vms(vms)

    def update_vm(self, vm_id: str, data: Dict[str, Any]):
        """Update an existing VM"""
        vms = self._load_vms()
        if vm_id not in vms:
            raise Exception(f"VM with ID {vm_id} not found")
        vms[vm_id] = data
        self._save_vms(vms)

    def delete_vm(self, vm_id: str):
        """Delete a VM"""
        vms = self._load_vms()
        if vm_id not in vms:
            raise Exception(f"VM with ID {vm_id} not found")
        del vms[vm_id]
        self._save_vms(vms)

    def _load_disks(self) -> Dict[str, Any]:
        """Load disks from the database file"""
        try:
            return json.loads(self.disks_file.read_text())
        except Exception as e:
            logger.error(f"Error loading disks: {str(e)}")
            return {}

    def _save_disks(self, disks: Dict[str, Any]):
        """Save disks to the database file"""
        try:
            self.disks_file.write_text(json.dumps(disks, indent=2))
        except Exception as e:
            logger.error(f"Error saving disks: {str(e)}")
            raise

    def list_disks(self) -> List[Dict[str, Any]]:
        """List all disks"""
        disks = self._load_disks()
        return [{"id": disk_id, **disk_data} for disk_id, disk_data in disks.items()]

    def get_disk(self, disk_id: str) -> Dict[str, Any]:
        """Get a disk by ID"""
        disks = self._load_disks()
        disk_data = disks.get(disk_id)
        if disk_data:
            return {"id": disk_id, **disk_data}
        return None

    def create_disk(self, disk_id: str, data: Dict[str, Any]):
        """Create a new disk"""
        disks = self._load_disks()
        if disk_id in disks:
            raise Exception(f"Disk with ID {disk_id} already exists")
        disks[disk_id] = data
        self._save_disks(disks)

    def update_disk(self, disk_id: str, data: Dict[str, Any]):
        """Update an existing disk"""
        disks = self._load_disks()
        if disk_id not in disks:
            raise Exception(f"Disk with ID {disk_id} not found")
        disks[disk_id] = data
        self._save_disks(disks)

    def delete_disk(self, disk_id: str):
        """Delete a disk"""
        disks = self._load_disks()
        if disk_id not in disks:
            raise Exception(f"Disk with ID {disk_id} not found")
        del disks[disk_id]
        self._save_disks(disks)

    def _load_ips(self) -> Dict[str, Any]:
        """Load IPs from the database file"""
        try:
            return json.loads(self.ips_file.read_text())
        except Exception as e:
            logger.error(f"Error loading IPs: {str(e)}")
            return {"ips": {}}

    def _save_ips(self, data: Dict[str, Any]):
        """Save IPs to the database file"""
        try:
            self.ips_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Error saving IPs: {str(e)}")
            raise

    def list_ips(self) -> List[Dict[str, Any]]:
        """List all IPs"""
        data = self._load_ips()
        return [{"ip": ip, **ip_data} for ip, ip_data in data.get("ips", {}).items()]

    def get_ip(self, ip: str) -> Optional[Dict[str, Any]]:
        """Get IP data"""
        data = self._load_ips()
        ip_data = data.get("ips", {}).get(ip)
        if ip_data:
            return {"ip": ip, **ip_data}
        return None

    def create_ip(self, ip: str, data: Dict[str, Any]):
        """Create a new IP entry"""
        all_data = self._load_ips()
        if ip in all_data.get("ips", {}):
            raise Exception(f"IP {ip} already exists")
        all_data.setdefault("ips", {})[ip] = data
        self._save_ips(all_data)

    def update_ip(self, ip: str, data: Dict[str, Any]):
        """Update an existing IP entry"""
        all_data = self._load_ips()
        if ip not in all_data.get("ips", {}):
            raise Exception(f"IP {ip} not found")
        all_data["ips"][ip].update(data)
        self._save_ips(all_data)

    def delete_ip(self, ip: str):
        """Delete an IP entry"""
        all_data = self._load_ips()
        if ip not in all_data.get("ips", {}):
            raise Exception(f"IP {ip} not found")
        del all_data["ips"][ip]
        self._save_ips(all_data)

# Create a global database instance
db = Database() 