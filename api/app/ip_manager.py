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
        self.scaling_threshold = 0.8  # Scale when 80% full
        self.max_pool_size = 24  # Maximum pool size /24
        self.min_pool_size = 28  # Minimum pool size /28
        self._ensure_ip_pool()
        try:
            self._setup_networking()
        except NetworkError as e:
            logger.warning(f"Network setup failed (this is expected if not running as root): {e}")

    def _setup_networking(self) -> None:
        try:
            # Try to enable IP forwarding without sudo first
            try:
                with open('/proc/sys/net/ipv4/ip_forward', 'r') as f:
                    if f.read().strip() != '1':
                        logger.warning("IP forwarding is not enabled. This may require root privileges to enable.")
            except Exception as e:
                logger.warning(f"Could not check IP forwarding status: {e}")
            
            # Check if iptables rules exist instead of trying to set them
            try:
                result = subprocess.run(
                    ['iptables', '-t', 'nat', '-L', 'POSTROUTING', '-n'],
                    capture_output=True,
                    text=True,
                    check=True
                )
                if str(self.ip_range) not in result.stdout:
                    logger.warning(f"NAT rule for {self.ip_range} not found in iptables. This may require root privileges to set up.")
            except subprocess.CalledProcessError as e:
                logger.warning(f"Could not check iptables rules: {e}")
            
            logger.info("Network setup checks completed")
        except Exception as e:
            logger.warning(f"Network setup checks failed: {e}")
            # Don't raise an error, just log the warning

    def create_network(self, name: str, cidr: str) -> None:
        try:
            network = ipaddress.IPv4Network(cidr)
            bridge_name = f"br-{name[:12]}"  # Limit bridge name length
            
            # Check if bridge already exists
            try:
                result = subprocess.run(
                    ['ip', 'link', 'show', bridge_name],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    logger.info(f"Bridge {bridge_name} already exists")
                else:
                    logger.warning(f"Bridge {bridge_name} does not exist. Root privileges may be required to create it.")
            except Exception as e:
                logger.warning(f"Could not check bridge status: {e}")
            
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
            
            # Check if bridge exists before trying to delete
            try:
                result = subprocess.run(
                    ['ip', 'link', 'show', bridge_name],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    logger.warning(f"Bridge {bridge_name} exists but may require root privileges to delete")
            except Exception as e:
                logger.warning(f"Could not check bridge status: {e}")
            
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

    def _check_pool_utilization(self) -> None:
        """Check IP pool utilization and scale if needed."""
        ips = self.list_ips()
        total_ips = len(ips)
        used_ips = len([ip for ip in ips if ip['state'] != 'available'])
        utilization = used_ips / total_ips if total_ips > 0 else 0

        if utilization >= self.scaling_threshold:
            current_prefix = self.ip_range.prefixlen
            if current_prefix > self.max_pool_size:
                # Calculate new range with one bit less prefix (doubles the size)
                new_prefix = current_prefix - 1
                new_range = ipaddress.IPv4Network(f"{self.ip_range.network_address}/{new_prefix}")
                
                # Add new IPs to the pool
                for ip in new_range.hosts():
                    if str(ip) not in [existing['ip'] for existing in ips]:
                        self.add_ip(str(ip))
                
                self.ip_range = new_range
                logger.info(f"IP pool expanded from /{current_prefix} to /{new_prefix}")

    def get_available_ip(self) -> Optional[str]:
        """Get an available IP and check if pool needs scaling."""
        self._check_pool_utilization()  # Check before getting IP
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

    def get_pool_metrics(self) -> Dict:
        """Get metrics about the IP pool."""
        ips = self.list_ips()
        total_ips = len(ips)
        
        # Count IPs in each state
        state_counts = {
            'available': 0,
            'allocated': 0,
            'attached': 0,
            'detached': 0
        }
        
        elastic_count = 0
        attached_vms = set()
        
        for ip in ips:
            state = ip.get('state', 'unknown')
            state_counts[state] = state_counts.get(state, 0) + 1
            
            if ip.get('is_elastic'):
                elastic_count += 1
            
            if ip.get('machine_id'):
                attached_vms.add(ip['machine_id'])
        
        utilization = (total_ips - state_counts['available']) / total_ips if total_ips > 0 else 0
        
        return {
            'total_ips': total_ips,
            'available_ips': state_counts['available'],
            'allocated_ips': state_counts['allocated'],
            'attached_ips': state_counts['attached'],
            'detached_ips': state_counts['detached'],
            'elastic_ips': elastic_count,
            'utilization_percentage': round(utilization * 100, 2),
            'unique_vms': len(attached_vms),
            'pool_size': f"/{self.ip_range.prefixlen}",
            'can_scale': self.ip_range.prefixlen > self.max_pool_size,
            'scaling_threshold_percentage': round(self.scaling_threshold * 100, 2)
        } 