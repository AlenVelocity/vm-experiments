from typing import Dict, List, Optional
import json
from pathlib import Path
import random
import subprocess
import logging
import ipaddress
from .db import db
import threading
import time

logger = logging.getLogger(__name__)

class NetworkError(Exception):
    """Custom exception for network operations"""
    pass

class IPLease:
    def __init__(self, ip: str, mac: str, hostname: str, lease_time: int = 3600):
        self.ip = ip
        self.mac = mac
        self.hostname = hostname
        self.lease_time = lease_time
        self.start_time = time.time()
        self.renewed_time = self.start_time

    def is_expired(self) -> bool:
        return (time.time() - self.renewed_time) > self.lease_time

    def renew(self) -> None:
        self.renewed_time = time.time()

class DHCPServer:
    def __init__(self, network: str, interface: str):
        self.network = ipaddress.IPv4Network(network)
        self.interface = interface
        self.leases: Dict[str, IPLease] = {}  # MAC -> Lease
        self.available_ips = set(self.network.hosts())
        self._load_leases()
        self._start_lease_cleanup()

    def _load_leases(self) -> None:
        try:
            lease_file = Path("/var/lib/misc/dnsmasq.leases")
            if lease_file.exists():
                for line in lease_file.read_text().splitlines():
                    parts = line.split()
                    if len(parts) >= 5:
                        timestamp, mac, ip, hostname, _ = parts
                        if ipaddress.IPv4Address(ip) in self.network:
                            self.leases[mac] = IPLease(ip, mac, hostname)
                            self.available_ips.discard(ipaddress.IPv4Address(ip))
        except Exception as e:
            logger.error(f"Error loading DHCP leases: {e}")

    def _start_lease_cleanup(self) -> None:
        def cleanup_expired_leases():
            while True:
                try:
                    current_time = time.time()
                    expired_macs = [
                        mac for mac, lease in self.leases.items()
                        if lease.is_expired()
                    ]
                    for mac in expired_macs:
                        lease = self.leases.pop(mac)
                        self.available_ips.add(ipaddress.IPv4Address(lease.ip))
                        logger.info(f"Expired lease for MAC {mac}, IP {lease.ip}")
                except Exception as e:
                    logger.error(f"Error in lease cleanup: {e}")
                time.sleep(60)

        thread = threading.Thread(target=cleanup_expired_leases, daemon=True)
        thread.start()

    def allocate_ip(self, mac: str, hostname: str) -> str:
        if mac in self.leases:
            lease = self.leases[mac]
            if not lease.is_expired():
                lease.renew()
                return lease.ip

        if not self.available_ips:
            raise NetworkError("No available IPs in the pool")

        ip = str(random.choice(list(self.available_ips)))
        self.available_ips.discard(ipaddress.IPv4Address(ip))
        self.leases[mac] = IPLease(ip, mac, hostname)
        return ip

    def release_ip(self, mac: str) -> None:
        if mac in self.leases:
            lease = self.leases.pop(mac)
            self.available_ips.add(ipaddress.IPv4Address(lease.ip))

class IPManager:
    def __init__(self):
        self.ip_range = ipaddress.IPv4Network('10.0.0.0/24')
        self.dhcp_servers: Dict[str, DHCPServer] = {}
        self._ensure_ip_pool()
        self._setup_networking()

    def _setup_networking(self) -> None:
        try:
            # Enable IP forwarding
            subprocess.run(['sudo', 'sysctl', '-w', 'net.ipv4.ip_forward=1'], check=True)
            
            # Ensure iptables FORWARD chain accepts traffic
            subprocess.run(['sudo', 'iptables', '-P', 'FORWARD', 'ACCEPT'], check=True)
            
            # Set up NAT
            subprocess.run([
                'sudo', 'iptables', '-t', 'nat', '-A', 'POSTROUTING',
                '-s', str(self.ip_range), '-j', 'MASQUERADE'
            ], check=True)
            
            logger.info("Network setup completed successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to set up networking: {e}")
            raise NetworkError(f"Failed to set up networking: {e}")

    def create_network(self, name: str, cidr: str) -> None:
        try:
            network = ipaddress.IPv4Network(cidr)
            bridge_name = f"br-{name[:12]}"  # Limit bridge name length
            
            # Create bridge interface
            subprocess.run(['sudo', 'ip', 'link', 'add', 'name', bridge_name, 'type', 'bridge'], check=True)
            subprocess.run(['sudo', 'ip', 'addr', 'add', f"{next(network.hosts())}/24", 'dev', bridge_name], check=True)
            subprocess.run(['sudo', 'ip', 'link', 'set', bridge_name, 'up'], check=True)
            
            # Set up DHCP server for this network
            self.dhcp_servers[name] = DHCPServer(cidr, bridge_name)
            
            # Save network configuration
            self._save_network_config(name, {
                'name': name,
                'cidr': cidr,
                'bridge': bridge_name,
                'gateway': str(next(network.hosts()))
            })
            
            logger.info(f"Created network {name} with CIDR {cidr}")
        except Exception as e:
            logger.error(f"Failed to create network: {e}")
            raise NetworkError(f"Failed to create network: {e}")

    def delete_network(self, name: str) -> None:
        try:
            config = self._load_network_config(name)
            if not config:
                raise NetworkError(f"Network {name} not found")
            
            bridge_name = config['bridge']
            
            # Remove DHCP server
            self.dhcp_servers.pop(name, None)
            
            # Delete bridge interface
            subprocess.run(['sudo', 'ip', 'link', 'set', bridge_name, 'down'], check=True)
            subprocess.run(['sudo', 'ip', 'link', 'delete', bridge_name, 'type', 'bridge'], check=True)
            
            # Remove network configuration
            self._delete_network_config(name)
            
            logger.info(f"Deleted network {name}")
        except Exception as e:
            logger.error(f"Failed to delete network: {e}")
            raise NetworkError(f"Failed to delete network: {e}")

    def _save_network_config(self, name: str, config: Dict) -> None:
        db.save_network(name, config)

    def _load_network_config(self, name: str) -> Optional[Dict]:
        return db.get_network(name)

    def _delete_network_config(self, name: str) -> None:
        db.delete_network(name)

    def list_networks(self) -> List[Dict]:
        return db.list_networks()

    def get_network(self, name: str) -> Optional[Dict]:
        return self._load_network_config(name)

    def allocate_ip(self, network_name: str, mac: str, hostname: str) -> str:
        dhcp_server = self.dhcp_servers.get(network_name)
        if not dhcp_server:
            raise NetworkError(f"Network {network_name} not found")
        return dhcp_server.allocate_ip(mac, hostname)

    def release_ip(self, network_name: str, mac: str) -> None:
        dhcp_server = self.dhcp_servers.get(network_name)
        if dhcp_server:
            dhcp_server.release_ip(mac)

    def get_network_info(self, network_name: str) -> Dict:
        config = self._load_network_config(network_name)
        if not config:
            raise NetworkError(f"Network {network_name} not found")
        
        dhcp_server = self.dhcp_servers.get(network_name)
        active_leases = len(dhcp_server.leases) if dhcp_server else 0
        available_ips = len(dhcp_server.available_ips) if dhcp_server else 0
        
        return {
            **config,
            'active_leases': active_leases,
            'available_ips': available_ips,
            'leases': [
                {
                    'ip': lease.ip,
                    'mac': lease.mac,
                    'hostname': lease.hostname,
                    'expires_in': int(lease.lease_time - (time.time() - lease.renewed_time))
                }
                for lease in (dhcp_server.leases.values() if dhcp_server else [])
            ]
        }

    def _ensure_ip_pool(self):
        """Ensure IP pool exists and is populated"""
        ips = db.list_ips()
        if not ips:
            # Initialize IP pool
            for ip in list(self.ip_range.hosts())[1:]:  # Skip network address
                self.add_ip(str(ip))

    def add_ip(self, ip: str) -> None:
        """Add an IP to the pool"""
        db.create_ip(ip, {
            'state': 'available',
            'machine_id': None,
            'is_elastic': False
        })

    def remove_ip(self, ip: str) -> None:
        """Remove an IP from the pool"""
        db.delete_ip(ip)

    def list_ips(self) -> List[dict]:
        """List all IPs"""
        return db.list_ips()

    def get_available_ip(self) -> Optional[str]:
        """Get an available IP"""
        ips = db.list_ips()
        available = [ip for ip in ips if ip.get('state') == 'available']
        return random.choice(available)['ip'] if available else None

    def attach_ip(self, ip: str, machine_id: str, is_elastic: bool = False) -> None:
        """Attach an IP to a machine"""
        ip_data = db.get_ip(ip)
        if not ip_data:
            raise ValueError(f"IP {ip} not found")
        if ip_data.get('state') != 'available':
            raise ValueError(f"IP {ip} is not available")

        db.update_ip(ip, {
            'state': 'attached',
            'machine_id': machine_id,
            'is_elastic': is_elastic
        })

    def detach_ip(self, ip: str) -> None:
        """Detach an IP from a machine"""
        ip_data = db.get_ip(ip)
        if not ip_data:
            raise ValueError(f"IP {ip} not found")
        if ip_data.get('state') != 'attached':
            raise ValueError(f"IP {ip} is not attached to any machine")

        db.update_ip(ip, {
            'state': 'available',
            'machine_id': None,
            'is_elastic': False
        })

    def get_machine_ips(self, machine_id: str) -> List[dict]:
        """Get all IPs attached to a machine"""
        ips = db.list_ips()
        return [ip for ip in ips if ip.get('machine_id') == machine_id] 