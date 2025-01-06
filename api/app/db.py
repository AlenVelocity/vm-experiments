import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
import json
from dataclasses import dataclass, asdict
import uuid
import logging

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "vm.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def dict_factory(cursor, row):
    fields = [column[0] for column in cursor.description]
    return {key: value for key, value in zip(fields, row)}

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = dict_factory
        self.init_db()

    def init_db(self):
        with self.conn:
            # VMs table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS vms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cpu_cores INTEGER NOT NULL,
                    memory_mb INTEGER NOT NULL,
                    disk_size_gb INTEGER NOT NULL,
                    network_name TEXT,
                    ssh_port INTEGER,
                    network_info TEXT,
                    cloud_init TEXT,
                    image_id TEXT,
                    state TEXT DEFAULT 'stopped',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # IPs table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ips (
                    ip TEXT PRIMARY KEY,
                    machine_id TEXT,
                    is_elastic BOOLEAN DEFAULT 0,
                    state TEXT DEFAULT 'available',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(machine_id) REFERENCES vms(id)
                )
            """)

            # Disks table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS disks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    size_gb INTEGER NOT NULL,
                    attached_to TEXT,
                    state TEXT DEFAULT 'available',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(attached_to) REFERENCES vms(id)
                )
            """)

            # VPCs table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS vpcs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cidr TEXT NOT NULL,
                    state TEXT DEFAULT 'available',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Firewall rules table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS firewall_rules (
                    id TEXT PRIMARY KEY,
                    vpc_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    port_range TEXT,
                    source TEXT,
                    destination TEXT,
                    action TEXT NOT NULL,
                    priority INTEGER DEFAULT 1000,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(vpc_id) REFERENCES vpcs(id)
                )
            """)

    def save_vm(self, vm_data: Dict) -> None:
        with self.conn:
            if isinstance(vm_data.get('network_info'), dict):
                vm_data['network_info'] = json.dumps(vm_data['network_info'])
            if isinstance(vm_data.get('cloud_init'), dict):
                vm_data['cloud_init'] = json.dumps(vm_data['cloud_init'])
            
            fields = ', '.join(vm_data.keys())
            placeholders = ', '.join('?' * len(vm_data))
            sql = f'INSERT OR REPLACE INTO vms ({fields}) VALUES ({placeholders})'
            self.conn.execute(sql, list(vm_data.values()))

    def get_vm(self, vm_id: str) -> Optional[Dict]:
        with self.conn:
            vm = self.conn.execute('SELECT * FROM vms WHERE id = ?', (vm_id,)).fetchone()
            if vm:
                if vm.get('network_info'):
                    vm['network_info'] = json.loads(vm['network_info'])
                if vm.get('cloud_init'):
                    vm['cloud_init'] = json.loads(vm['cloud_init'])
            return vm

    def list_vms(self) -> List[Dict]:
        with self.conn:
            vms = self.conn.execute('SELECT * FROM vms').fetchall()
            for vm in vms:
                if vm.get('network_info'):
                    vm['network_info'] = json.loads(vm['network_info'])
                if vm.get('cloud_init'):
                    vm['cloud_init'] = json.loads(vm['cloud_init'])
            return vms

    def delete_vm(self, vm_id: str) -> None:
        with self.conn:
            self.conn.execute('DELETE FROM vms WHERE id = ?', (vm_id,))

    def save_ip(self, ip_data: Dict) -> None:
        with self.conn:
            fields = ', '.join(ip_data.keys())
            placeholders = ', '.join('?' * len(ip_data))
            sql = f'INSERT OR REPLACE INTO ips ({fields}) VALUES ({placeholders})'
            self.conn.execute(sql, list(ip_data.values()))

    def get_ip(self, ip: str) -> Optional[Dict]:
        with self.conn:
            return self.conn.execute('SELECT * FROM ips WHERE ip = ?', (ip,)).fetchone()

    def list_ips(self) -> List[Dict]:
        with self.conn:
            return self.conn.execute('SELECT * FROM ips').fetchall()

    def delete_ip(self, ip: str) -> None:
        with self.conn:
            self.conn.execute('DELETE FROM ips WHERE ip = ?', (ip,))

    def save_disk(self, disk_data: Dict) -> None:
        with self.conn:
            fields = ', '.join(disk_data.keys())
            placeholders = ', '.join('?' * len(disk_data))
            sql = f'INSERT OR REPLACE INTO disks ({fields}) VALUES ({placeholders})'
            self.conn.execute(sql, list(disk_data.values()))

    def get_disk(self, disk_id: str) -> Optional[Dict]:
        with self.conn:
            return self.conn.execute('SELECT * FROM disks WHERE id = ?', (disk_id,)).fetchone()

    def list_disks(self) -> List[Dict]:
        with self.conn:
            return self.conn.execute('SELECT * FROM disks').fetchall()

    def delete_disk(self, disk_id: str) -> None:
        with self.conn:
            self.conn.execute('DELETE FROM disks WHERE id = ?', (disk_id,))

    def save_vpc(self, vpc_data: Dict) -> None:
        with self.conn:
            fields = ', '.join(vpc_data.keys())
            placeholders = ', '.join('?' * len(vpc_data))
            sql = f'INSERT OR REPLACE INTO vpcs ({fields}) VALUES ({placeholders})'
            self.conn.execute(sql, list(vpc_data.values()))

    def get_vpc(self, vpc_id: str) -> Optional[Dict]:
        with self.conn:
            return self.conn.execute('SELECT * FROM vpcs WHERE id = ?', (vpc_id,)).fetchone()

    def list_vpcs(self) -> List[Dict]:
        with self.conn:
            return self.conn.execute('SELECT * FROM vpcs').fetchall()

    def delete_vpc(self, vpc_id: str) -> None:
        with self.conn:
            self.conn.execute('DELETE FROM vpcs WHERE id = ?', (vpc_id,))

    def save_firewall_rule(self, rule_data: Dict) -> None:
        with self.conn:
            fields = ', '.join(rule_data.keys())
            placeholders = ', '.join('?' * len(rule_data))
            sql = f'INSERT OR REPLACE INTO firewall_rules ({fields}) VALUES ({placeholders})'
            self.conn.execute(sql, list(rule_data.values()))

    def get_firewall_rule(self, rule_id: str) -> Optional[Dict]:
        with self.conn:
            return self.conn.execute('SELECT * FROM firewall_rules WHERE id = ?', (rule_id,)).fetchone()

    def list_firewall_rules(self, vpc_id: Optional[str] = None) -> List[Dict]:
        with self.conn:
            if vpc_id:
                return self.conn.execute('SELECT * FROM firewall_rules WHERE vpc_id = ?', (vpc_id,)).fetchall()
            return self.conn.execute('SELECT * FROM firewall_rules').fetchall()

    def delete_firewall_rule(self, rule_id: str) -> None:
        with self.conn:
            self.conn.execute('DELETE FROM firewall_rules WHERE id = ?', (rule_id,))

db = Database() 