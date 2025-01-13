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
        self.ip_range = ipaddress.IPv4Network('10.0.0.0/24')
        self._ensure_ip_pool()

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