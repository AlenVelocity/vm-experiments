from typing import Dict, List, Optional
import json
from pathlib import Path
import random
import subprocess
import logging
import ipaddress

logger = logging.getLogger(__name__)

class IPManager:
    def __init__(self):
        self.ips_dir = Path("ips")
        self.ips_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.ips_dir / "ips.json"
        self.ip_pool: Dict[str, dict] = self._load_ips()

    def _load_ips(self) -> Dict[str, dict]:
        if self.config_file.exists():
            with open(self.config_file) as f:
                return json.load(f)
        return {}

    def _save_ips(self) -> None:
        with open(self.config_file, "w") as f:
            json.dump(self.ip_pool, f, indent=2)

    def _configure_interface(self, ip: str, interface: str) -> None:
        try:
            subprocess.run(['sudo', 'ip', 'link', 'set', interface, 'down'], check=True)
            
            network = ipaddress.ip_network(f"{ip}/24", strict=False)
            gateway = str(next(network.hosts()))
            
            subprocess.run(['sudo', 'ip', 'addr', 'add', f"{ip}/24", 'dev', interface], check=True)
            subprocess.run(['sudo', 'ip', 'route', 'add', 'default', 'via', gateway, 'dev', interface], check=True)
            subprocess.run(['sudo', 'ip', 'link', 'set', interface, 'up'], check=True)
            subprocess.run(['sudo', 'sysctl', '-w', 'net.ipv4.ip_forward=1'], check=True)
            subprocess.run(['sudo', 'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', f"{ip}/24", '-j', 'MASQUERADE'], check=True)
            
            logger.info(f"Configured interface {interface} with IP {ip}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to configure interface: {e}")
            raise

    def _deconfigure_interface(self, ip: str, interface: str) -> None:
        try:
            subprocess.run(['sudo', 'iptables', '-t', 'nat', '-D', 'POSTROUTING', '-s', f"{ip}/24", '-j', 'MASQUERADE'], check=True)
            subprocess.run(['sudo', 'ip', 'addr', 'del', f"{ip}/24", 'dev', interface], check=True)
            logger.info(f"Deconfigured interface {interface}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to deconfigure interface: {e}")
            raise

    def add_ip(self, ip: str) -> None:
        if ip in self.ip_pool:
            raise ValueError(f"IP {ip} already exists in the pool")
        
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise ValueError(f"Invalid IP address: {ip}")
        
        self.ip_pool[ip] = {
            "status": "available",
            "attached_to": None,
            "is_elastic": False,
            "interface": None
        }
        self._save_ips()

    def remove_ip(self, ip: str) -> None:
        if ip not in self.ip_pool:
            raise ValueError(f"IP {ip} not found in the pool")
        
        if self.ip_pool[ip]["attached_to"]:
            raise ValueError(f"IP {ip} is still attached to a machine")
        
        if self.ip_pool[ip]["interface"]:
            self._deconfigure_interface(ip, self.ip_pool[ip]["interface"])
        
        del self.ip_pool[ip]
        self._save_ips()

    def list_ips(self) -> List[dict]:
        return [{"ip": ip, **info} for ip, info in self.ip_pool.items()]

    def get_available_ip(self) -> Optional[str]:
        available_ips = [ip for ip, info in self.ip_pool.items() 
                        if info["status"] == "available" and not info["is_elastic"]]
        return random.choice(available_ips) if available_ips else None

    def attach_ip(self, ip: str, machine_id: str, is_elastic: bool = False) -> None:
        if ip not in self.ip_pool:
            raise ValueError(f"IP {ip} not found in the pool")
        
        if self.ip_pool[ip]["attached_to"]:
            raise ValueError(f"IP {ip} is already attached to a machine")
        
        interface = f"eth{abs(hash(machine_id)) % 100}"
        self._configure_interface(ip, interface)
        
        self.ip_pool[ip].update({
            "status": "in_use",
            "attached_to": machine_id,
            "is_elastic": is_elastic,
            "interface": interface
        })
        self._save_ips()

    def detach_ip(self, ip: str) -> None:
        if ip not in self.ip_pool:
            raise ValueError(f"IP {ip} not found in the pool")
        
        if not self.ip_pool[ip]["attached_to"]:
            raise ValueError(f"IP {ip} is not attached to any machine")
        
        if self.ip_pool[ip]["interface"]:
            self._deconfigure_interface(ip, self.ip_pool[ip]["interface"])
        
        self.ip_pool[ip].update({
            "status": "available",
            "attached_to": None,
            "interface": None
        })
        self._save_ips()

    def get_machine_ips(self, machine_id: str) -> List[dict]:
        return [{"ip": ip, **info} for ip, info in self.ip_pool.items() 
                if info["attached_to"] == machine_id] 