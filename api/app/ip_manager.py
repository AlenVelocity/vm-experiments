from typing import Dict, List, Optional
import json
from pathlib import Path
import random
import subprocess
import logging
import ipaddress
from .db import db

logger = logging.getLogger(__name__)

class IPManager:
    def __init__(self):
        pass

    def _configure_interface(self, ip: str, interface: str) -> None:
        try:
            network = ipaddress.ip_network(f"{ip}/24", strict=False)
            gateway = str(next(network.hosts()))
            
            # Configure interface with IP
            subprocess.run(['sudo', 'ip', 'addr', 'add', f"{ip}/24", 'dev', interface], check=True)
            subprocess.run(['sudo', 'ip', 'link', 'set', interface, 'up'], check=True)
            
            # Enable IP forwarding
            subprocess.run(['sudo', 'sysctl', '-w', 'net.ipv4.ip_forward=1'], check=True)
            
            # Set up NAT
            subprocess.run(['sudo', 'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', f"{ip}/24", '-j', 'MASQUERADE'], check=True)
            subprocess.run(['sudo', 'iptables', '-A', 'FORWARD', '-i', interface, '-j', 'ACCEPT'], check=True)
            subprocess.run(['sudo', 'iptables', '-A', 'FORWARD', '-o', interface, '-j', 'ACCEPT'], check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to configure interface: {e}")
            raise

    def _deconfigure_interface(self, ip: str, interface: str) -> None:
        try:
            # Remove NAT rules
            subprocess.run(['sudo', 'iptables', '-t', 'nat', '-D', 'POSTROUTING', '-s', f"{ip}/24", '-j', 'MASQUERADE'], check=True)
            subprocess.run(['sudo', 'iptables', '-D', 'FORWARD', '-i', interface, '-j', 'ACCEPT'], check=True)
            subprocess.run(['sudo', 'iptables', '-D', 'FORWARD', '-o', interface, '-j', 'ACCEPT'], check=True)
            
            # Remove IP from interface
            subprocess.run(['sudo', 'ip', 'addr', 'del', f"{ip}/24", 'dev', interface], check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to deconfigure interface: {e}")
            raise

    def create_bridge(self, bridge_name: str, ip: str) -> None:
        """Create a bridge interface and assign an IP address."""
        try:
            # Create and configure bridge interface
            subprocess.run(['sudo', 'ip', 'link', 'add', 'name', bridge_name, 'type', 'bridge'], check=True)
            subprocess.run(['sudo', 'ip', 'addr', 'add', f"{ip}/24", 'dev', bridge_name], check=True)
            subprocess.run(['sudo', 'ip', 'link', 'set', bridge_name, 'up'], check=True)
            
            # Enable IP forwarding
            subprocess.run(['sudo', 'sysctl', '-w', 'net.ipv4.ip_forward=1'], check=True)
            
            # Set up NAT and forwarding rules
            subprocess.run(['sudo', 'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', f"{ip}/24", '-j', 'MASQUERADE'], check=True)
            subprocess.run(['sudo', 'iptables', '-A', 'FORWARD', '-i', bridge_name, '-j', 'ACCEPT'], check=True)
            subprocess.run(['sudo', 'iptables', '-A', 'FORWARD', '-o', bridge_name, '-j', 'ACCEPT'], check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create bridge: {e}")
            raise

    def delete_bridge(self, bridge_name: str, ip: str) -> None:
        """Delete the bridge interface and clean up NAT rules."""
        try:
            # Remove NAT and forwarding rules
            subprocess.run(['sudo', 'iptables', '-t', 'nat', '-D', 'POSTROUTING', '-s', f"{ip}/24", '-j', 'MASQUERADE'], check=True)
            subprocess.run(['sudo', 'iptables', '-D', 'FORWARD', '-i', bridge_name, '-j', 'ACCEPT'], check=True)
            subprocess.run(['sudo', 'iptables', '-D', 'FORWARD', '-o', bridge_name, '-j', 'ACCEPT'], check=True)
            
            # Delete bridge interface
            subprocess.run(['sudo', 'ip', 'link', 'set', bridge_name, 'down'], check=True)
            subprocess.run(['sudo', 'ip', 'link', 'delete', bridge_name, 'type', 'bridge'], check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to delete bridge: {e}")
            raise

    # Database operations remain unchanged
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