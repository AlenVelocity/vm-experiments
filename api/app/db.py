import sqlite3
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from contextlib import contextmanager
import time

logger = logging.getLogger(__name__)

class DatabaseError(Exception):
    """Custom exception for database operations"""
    pass

class Database:
    def __init__(self):
        self.db_path = Path("api/data/vm.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise DatabaseError(f"Database error: {e}")
        finally:
            conn.close()

    def _init_db(self):
        with self.get_connection() as conn:
            # Create VMs table with enhanced fields
            conn.execute("""
            CREATE TABLE IF NOT EXISTS vms (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                config TEXT NOT NULL,
                network_info TEXT,
                ssh_port INTEGER,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                error_message TEXT
            )
            """)

            # Create VM metrics table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_metrics (
                vm_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                cpu_usage REAL NOT NULL,
                memory_usage REAL NOT NULL,
                disk_usage TEXT NOT NULL,
                network_usage TEXT NOT NULL,
                FOREIGN KEY (vm_id) REFERENCES vms(id) ON DELETE CASCADE,
                PRIMARY KEY (vm_id, timestamp)
            )
            """)

            # Create networks table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS networks (
                name TEXT PRIMARY KEY,
                cidr TEXT NOT NULL,
                bridge TEXT NOT NULL,
                gateway TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """)

            # Create DHCP leases table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS dhcp_leases (
                network_name TEXT NOT NULL,
                mac TEXT NOT NULL,
                ip TEXT NOT NULL,
                hostname TEXT NOT NULL,
                lease_time INTEGER NOT NULL,
                start_time REAL NOT NULL,
                renewed_time REAL NOT NULL,
                FOREIGN KEY (network_name) REFERENCES networks(name) ON DELETE CASCADE,
                PRIMARY KEY (network_name, mac)
            )
            """)

            # Create firewall rules table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS firewall_rules (
                id TEXT PRIMARY KEY,
                network_name TEXT NOT NULL,
                direction TEXT NOT NULL,
                protocol TEXT NOT NULL,
                port_range TEXT NOT NULL,
                source TEXT NOT NULL,
                description TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (network_name) REFERENCES networks(name) ON DELETE CASCADE
            )
            """)

            # Create storage volumes table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS storage_volumes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                size_gb INTEGER NOT NULL,
                vm_id TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (vm_id) REFERENCES vms(id) ON DELETE SET NULL
            )
            """)

            # Create IP addresses table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS ip_addresses (
                ip TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                machine_id TEXT,
                is_elastic BOOLEAN DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (machine_id) REFERENCES vms(id) ON DELETE SET NULL
            )
            """)

            # Create disks table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS disks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                size_gb INTEGER NOT NULL,
                state TEXT NOT NULL,
                attached_to TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (attached_to) REFERENCES vms(id) ON DELETE SET NULL
            )
            """)

    def save_vm(self, vm_id: str, data: Dict[str, Any]) -> None:
        """Create or update a VM in the database."""
        with self.get_connection() as conn:
            now = time.time()
            conn.execute("""
            INSERT OR REPLACE INTO vms (
                id, name, config, network_info, ssh_port, status,
                created_at, updated_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                vm_id,
                data['name'],
                json.dumps({
                    'cpu_cores': data['cpu_cores'],
                    'memory_mb': data['memory_mb'],
                    'disk_size_gb': data['disk_size_gb'],
                    'network_name': data['network_name'],
                    'cloud_init': data.get('cloud_init'),
                    'image_id': data.get('image_id')
                }),
                json.dumps(data.get('network_info')),
                data.get('ssh_port'),
                data.get('status', 'creating'),
                data.get('created_at', now),
                now,
                data.get('error_message')
            ))

    def save_vm_metrics(self, vm_id: str, metrics: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
            INSERT INTO vm_metrics (
                vm_id, timestamp, cpu_usage, memory_usage,
                disk_usage, network_usage
            ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                vm_id,
                metrics['timestamp'],
                metrics['cpu_usage'],
                metrics['memory_usage'],
                json.dumps(metrics['disk_usage']),
                json.dumps(metrics['network_usage'])
            ))

    def get_vm_metrics(self, vm_id: str, start_time: float, end_time: float) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("""
            SELECT * FROM vm_metrics
            WHERE vm_id = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
            """, (vm_id, start_time, end_time))
            return [
                {
                    'timestamp': row['timestamp'],
                    'cpu_usage': row['cpu_usage'],
                    'memory_usage': row['memory_usage'],
                    'disk_usage': json.loads(row['disk_usage']),
                    'network_usage': json.loads(row['network_usage'])
                }
                for row in cursor.fetchall()
            ]

    def save_network(self, name: str, data: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO networks (
                name, cidr, bridge, gateway, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """, (
                name,
                data['cidr'],
                data['bridge'],
                data['gateway'],
                time.time()
            ))

    def save_dhcp_lease(self, network_name: str, lease: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO dhcp_leases (
                network_name, mac, ip, hostname, lease_time,
                start_time, renewed_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                network_name,
                lease['mac'],
                lease['ip'],
                lease['hostname'],
                lease['lease_time'],
                lease['start_time'],
                lease['renewed_time']
            ))

    def get_network_leases(self, network_name: str) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("""
            SELECT * FROM dhcp_leases WHERE network_name = ?
            """, (network_name,))
            return [dict(row) for row in cursor.fetchall()]

    def save_firewall_rule(self, rule_id: str, data: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO firewall_rules (
                id, network_name, direction, protocol, port_range,
                source, description, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rule_id,
                data['network_name'],
                data['direction'],
                data['protocol'],
                data['port_range'],
                data['source'],
                data.get('description'),
                time.time()
            ))

    def get_firewall_rules(self, network_name: str) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("""
            SELECT * FROM firewall_rules WHERE network_name = ?
            """, (network_name,))
            return [dict(row) for row in cursor.fetchall()]

    def save_storage_volume(self, volume_id: str, data: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO storage_volumes (
                id, name, size_gb, vm_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """, (
                volume_id,
                data['name'],
                data['size_gb'],
                data.get('vm_id'),
                time.time()
            ))

    def get_storage_volumes(self, vm_id: Optional[str] = None) -> List[Dict]:
        with self.get_connection() as conn:
            if vm_id:
                cursor = conn.execute("""
                SELECT * FROM storage_volumes WHERE vm_id = ?
                """, (vm_id,))
            else:
                cursor = conn.execute("SELECT * FROM storage_volumes")
            return [dict(row) for row in cursor.fetchall()]

    def get_vm(self, vm_id: str) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM vms WHERE id = ?", (vm_id,))
            row = cursor.fetchone()
            if row:
                return {
                    **dict(row),
                    'config': json.loads(row['config']),
                    'network_info': json.loads(row['network_info']) if row['network_info'] else None
                }
        return None

    def list_vms(self) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM vms")
            return [
                {
                    **dict(row),
                    'config': json.loads(row['config']),
                    'network_info': json.loads(row['network_info']) if row['network_info'] else None
                }
                for row in cursor.fetchall()
            ]

    def delete_vm(self, vm_id: str) -> None:
        with self.get_connection() as conn:
            conn.execute("DELETE FROM vms WHERE id = ?", (vm_id,))
            conn.execute("DELETE FROM vm_metrics WHERE vm_id = ?", (vm_id,))

    def cleanup_old_metrics(self, max_age_seconds: int = 86400 * 7) -> None:
        """Clean up metrics older than the specified age (default 7 days)"""
        with self.get_connection() as conn:
            cutoff_time = time.time() - max_age_seconds
            conn.execute("DELETE FROM vm_metrics WHERE timestamp < ?", (cutoff_time,))

    def list_ips(self) -> List[Dict]:
        """List all IP addresses"""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM ip_addresses")
            return [dict(row) for row in cursor.fetchall()]

    def get_ip(self, ip: str) -> Optional[Dict]:
        """Get IP address details"""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM ip_addresses WHERE ip = ?", (ip,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_ip(self, ip: str, data: Dict) -> None:
        """Create a new IP address entry"""
        with self.get_connection() as conn:
            now = time.time()
            conn.execute("""
            INSERT INTO ip_addresses (ip, state, machine_id, is_elastic, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                ip,
                data['state'],
                data.get('machine_id'),
                data.get('is_elastic', False),
                now,
                now
            ))

    def update_ip(self, ip: str, data: Dict) -> None:
        """Update an IP address entry"""
        with self.get_connection() as conn:
            now = time.time()
            conn.execute("""
            UPDATE ip_addresses
            SET state = ?, machine_id = ?, is_elastic = ?, updated_at = ?
            WHERE ip = ?
            """, (
                data['state'],
                data.get('machine_id'),
                data.get('is_elastic', False),
                now,
                ip
            ))

    def delete_ip(self, ip: str) -> None:
        """Delete an IP address entry"""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM ip_addresses WHERE ip = ?", (ip,))

    def update_vm(self, vm_id: str, data: Dict[str, Any]) -> None:
        """Update a VM's configuration in the database."""
        with self.get_connection() as conn:
            # Prepare the data for update
            update_fields = []
            params = []
            
            if 'name' in data:
                update_fields.append("name = ?")
                params.append(data['name'])
            
            if 'config' in data:
                config = {
                    'cpu_cores': data['config'].get('cpu_cores'),
                    'memory_mb': data['config'].get('memory_mb'),
                    'disk_size_gb': data['config'].get('disk_size_gb'),
                    'network_name': data['config'].get('network_name'),
                    'cloud_init': data['config'].get('cloud_init'),
                    'image_id': data['config'].get('image_id')
                }
                update_fields.append("config = ?")
                params.append(json.dumps(config))
            
            if 'network_info' in data:
                update_fields.append("network_info = ?")
                params.append(json.dumps(data['network_info']))
            
            if 'ssh_port' in data:
                update_fields.append("ssh_port = ?")
                params.append(data['ssh_port'])
            
            if 'status' in data:
                update_fields.append("status = ?")
                params.append(data['status'])
            
            update_fields.append("updated_at = ?")
            params.append(time.time())
            
            if 'error_message' in data:
                update_fields.append("error_message = ?")
                params.append(data['error_message'])
            
            # Add VM ID to params
            params.append(vm_id)
            
            # Execute the update
            query = f"UPDATE vms SET {', '.join(update_fields)} WHERE id = ?"
            conn.execute(query, params)

    # Disk management methods
    def create_disk(self, disk_id: str, data: Dict) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO disks (id, name, size_gb, state, attached_to, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (disk_id, data['name'], data['size_gb'], data.get('state', 'available'), 
                 data.get('attached_to'), time.time(), time.time())
            )
    
    def update_disk(self, disk_id: str, data: Dict) -> None:
        with self.get_connection() as conn:
            update_fields = []
            params = []
            
            if 'name' in data:
                update_fields.append("name = ?")
                params.append(data['name'])
            
            if 'size_gb' in data:
                update_fields.append("size_gb = ?")
                params.append(data['size_gb'])
            
            if 'state' in data:
                update_fields.append("state = ?")
                params.append(data['state'])
            
            if 'attached_to' in data:
                update_fields.append("attached_to = ?")
                params.append(data['attached_to'])
            
            update_fields.append("updated_at = ?")
            params.append(time.time())
            
            params.append(disk_id)
            
            query = f"UPDATE disks SET {', '.join(update_fields)} WHERE id = ?"
            conn.execute(query, params)
    
    def delete_disk(self, disk_id: str) -> None:
        with self.get_connection() as conn:
            conn.execute("DELETE FROM disks WHERE id = ?", (disk_id,))
    
    def get_disk(self, disk_id: str) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM disks WHERE id = ?", (disk_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    
    def list_disks(self) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM disks ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

# Create a global database instance
db = Database() 