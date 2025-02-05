import ipaddress
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

class VPCError(Exception):
    """Base exception for VPC-related errors"""
    pass

class VPC:
    def __init__(self, name: str, cidr: str = "10.0.0.0/24"):
        if not name:
            raise VPCError("VPC name cannot be empty")
        
        try:
            self.name = name
            network = ipaddress.ip_network(cidr)
            if network.prefixlen > 28:  # Ensure subnet isn't too small
                raise VPCError("CIDR prefix length must be 28 or less")
            self.network = network
            self.cidr = str(self.network)  # Normalize CIDR notation
            self.used_private_ips: List[str] = []
            self.used_public_ips: List[str] = []
            self.public_network = ipaddress.ip_network("172.16.0.0/24")
            self.created_at = datetime.now().isoformat()
        except ValueError as e:
            raise VPCError(f"Invalid CIDR format: {str(e)}")
        
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "cidr": self.cidr,
            "used_private_ips": self.used_private_ips,
            "used_public_ips": self.used_public_ips,
            "created_at": getattr(self, 'created_at', datetime.now().isoformat())
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'VPC':
        if not isinstance(data, dict):
            raise VPCError("Invalid VPC data format")
        
        required_fields = ["name", "cidr"]
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            raise VPCError(f"Missing required fields: {', '.join(missing_fields)}")
        
        vpc = cls(data["name"], data["cidr"])
        vpc.used_private_ips = data.get("used_private_ips", [])
        vpc.used_public_ips = data.get("used_public_ips", [])
        vpc.created_at = data.get("created_at", datetime.now().isoformat())
        return vpc

    def _get_next_available_ip(self, network: ipaddress.IPv4Network, used_ips: List[str]) -> str:
        """Get next available IP from the network"""
        try:
            # Skip the first IP (network address) and last IP (broadcast)
            available_ips = [str(ip) for ip in list(network.hosts())[1:-1]]
            unused_ips = list(set(available_ips) - set(used_ips))
            
            if not unused_ips:
                raise VPCError(f"No available IPs in network {network}")
                
            # Return the first available IP
            ip = unused_ips[0]
            used_ips.append(ip)
            return ip
            
        except Exception as e:
            if isinstance(e, VPCError):
                raise
            raise VPCError(f"Error allocating IP from network {network}: {str(e)}")

    def allocate_ip(self) -> Dict[str, str]:
        """Allocate a new IP address pair (public and private) for a VM"""
        try:
            private_ip = self._get_next_available_ip(self.network, self.used_private_ips)
            public_ip = self._get_next_available_ip(self.public_network, self.used_public_ips)
            
            return {
                "private_ip": private_ip,
                "public_ip": public_ip,
                "allocated_at": datetime.now().isoformat()
            }
        except Exception as e:
            raise VPCError(f"Error allocating IP pair: {str(e)}")

    def release_ip(self, private_ip: str, public_ip: str) -> None:
        """Release allocated IP addresses back to the pool"""
        try:
            # Validate IPs belong to correct networks
            if private_ip and ipaddress.ip_address(private_ip) not in self.network:
                raise VPCError(f"Private IP {private_ip} does not belong to network {self.network}")
            if public_ip and ipaddress.ip_address(public_ip) not in self.public_network:
                raise VPCError(f"Public IP {public_ip} does not belong to network {self.public_network}")
                
            if private_ip in self.used_private_ips:
                self.used_private_ips.remove(private_ip)
            if public_ip in self.used_public_ips:
                self.used_public_ips.remove(public_ip)
        except Exception as e:
            if isinstance(e, VPCError):
                raise
            raise VPCError(f"Error releasing IPs: {str(e)}")

class VPCManager:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(VPCManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not VPCManager._initialized:
            self.vpc_dir = Path("vms/vpc")
            self.vpc_dir.mkdir(parents=True, exist_ok=True)
            self.vpc_file = self.vpc_dir / "vpcs.json"
            self.vpcs: Dict[str, VPC] = {}
            self.load_vpcs()
            VPCManager._initialized = True

    def load_vpcs(self):
        try:
            if self.vpc_file.exists():
                data = json.loads(self.vpc_file.read_text())
                if not isinstance(data, dict):
                    raise VPCError("Invalid VPC data format in file")
                self.vpcs = {
                    name: VPC.from_dict(vpc_data)
                    for name, vpc_data in data.items()
                }
        except Exception as e:
            raise VPCError(f"Error loading VPCs: {str(e)}")

    def save_vpcs(self):
        try:
            data = {
                name: vpc.to_dict()
                for name, vpc in self.vpcs.items()
            }
            self.vpc_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            raise VPCError(f"Error saving VPCs: {str(e)}")

    def create_vpc(self, name: str, cidr: str = "10.0.0.0/24") -> VPC:
        if not name:
            raise VPCError("VPC name cannot be empty")
        if name in self.vpcs:
            raise VPCError(f"VPC {name} already exists")
        
        vpc = VPC(name, cidr)
        self.vpcs[name] = vpc
        self.save_vpcs()
        return vpc

    def get_vpc(self, name: str) -> Optional[VPC]:
        return self.vpcs.get(name)

    def list_vpcs(self) -> List[str]:
        return list(self.vpcs.keys())

    def delete_vpc(self, name: str) -> None:
        if name not in self.vpcs:
            raise VPCError(f"VPC {name} does not exist")
        del self.vpcs[name]
        self.save_vpcs() 