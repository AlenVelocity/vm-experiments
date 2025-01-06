from typing import Dict, List, Optional
import json
from pathlib import Path
import random
import subprocess
import logging
import ipaddress
import platform
from .db import db

logger = logging.getLogger(__name__)

class IPManager:
    def __init__(self):
        self.is_macos = platform.system().lower() == 'darwin'

    def _configure_interface(self, ip: str, interface: str) -> None:
        try:
            network = ipaddress.ip_network(f"{ip}/24", strict=False)
            gateway = str(next(network.hosts()))
            
            if self.is_macos:
                # On macOS, we use bridge interfaces
                bridge_name = f"bridge{abs(hash(interface)) % 100}"
                
                # Create bridge interface if it doesn't exist
                subprocess.run(['sudo', 'ifconfig', bridge_name, 'create'], check=True)
                
                # Configure IP address
                subprocess.run(['sudo', 'ifconfig', bridge_name, f"{ip}/24"], check=True)
                
                # Enable IP forwarding
                subprocess.run(['sudo', 'sysctl', '-w', 'net.inet.ip.forwarding=1'], check=True)
                
                # Add firewall rules using pfctl
                pf_rules = f"""
                nat on en0 from {ip}/24 to any -> (en0)
                pass in on {bridge_name} from any to any
                pass out on {bridge_name} from any to any
                """
                
                with open('/tmp/pf.rules', 'w') as f:
                    f.write(pf_rules)
                
                subprocess.run(['sudo', 'pfctl', '-f', '/tmp/pf.rules'], check=True)
                subprocess.run(['sudo', 'pfctl', '-e'], check=True)
            else:
                # For Linux systems
                subprocess.run(['sudo', 'ip', 'addr', 'add', f"{ip}/24", 'dev', interface], check=True)
                subprocess.run(['sudo', 'ip', 'link', 'set', interface, 'up'], check=True)
                subprocess.run(['sudo', 'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', f"{ip}/24", '-j', 'MASQUERADE'], check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to configure interface: {e}")
            raise

    def _deconfigure_interface(self, ip: str, interface: str) -> None:
        try:
            if self.is_macos:
                bridge_name = f"bridge{abs(hash(interface)) % 100}"
                
                # Remove bridge interface
                subprocess.run(['sudo', 'ifconfig', bridge_name, 'destroy'], check=True)
                
                # Remove firewall rules
                subprocess.run(['sudo', 'pfctl', '-F', 'nat'], check=True)
            else:
                # For Linux systems
                subprocess.run(['sudo', 'ip', 'addr', 'del', f"{ip}/24", 'dev', interface], check=True)
                subprocess.run(['sudo', 'iptables', '-t', 'nat', '-D', 'POSTROUTING', '-s', f"{ip}/24", '-j', 'MASQUERADE'], check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to deconfigure interface: {e}")
            raise

    def add_ip(self, ip: str) -> None:
        db.save_ip({
            'ip': ip,
            'state': 'available'
        })

    def remove_ip(self, ip: str) -> None:
        db.delete_ip(ip)

    def list_ips(self) -> List[dict]:
        return db.list_ips()

    def get_available_ip(self) -> Optional[str]:
        ips = db.list_ips()
        available_ips = [ip['ip'] for ip in ips if ip['state'] == 'available']
        return random.choice(available_ips) if available_ips else None

    def attach_ip(self, ip: str, machine_id: str, is_elastic: bool = False) -> None:
        ip_data = db.get_ip(ip)
        if not ip_data:
            raise ValueError(f"IP {ip} not found")
        if ip_data['state'] != 'available':
            raise ValueError(f"IP {ip} is not available")

        db.save_ip({
            'ip': ip,
            'machine_id': machine_id,
            'is_elastic': is_elastic,
            'state': 'attached'
        })

    def detach_ip(self, ip: str) -> None:
        ip_data = db.get_ip(ip)
        if not ip_data:
            raise ValueError(f"IP {ip} not found")
        if ip_data['state'] != 'attached':
            raise ValueError(f"IP {ip} is not attached to any machine")

        db.save_ip({
            'ip': ip,
            'machine_id': None,
            'is_elastic': False,
            'state': 'available'
        })

    def get_machine_ips(self, machine_id: str) -> List[dict]:
        ips = db.list_ips()
        return [ip for ip in ips if ip.get('machine_id') == machine_id] 