import json
import logging
import ipaddress
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass
from .networking import NetworkManager, NetworkType, NetworkError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VPCError(Exception):
    """Base exception for VPC-related errors"""
    pass

@dataclass
class VPC:
    name: str
    cidr: str
    subnets: Dict[str, str] = None
    used_private_ips: Dict[str, str] = None
    used_public_ips: Dict[str, str] = None
    
    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'cidr': self.cidr,
            'subnets': self.subnets or {},
            'used_private_ips': self.used_private_ips or {},
            'used_public_ips': self.used_public_ips or {}
        }

class VPCManager:
    def __init__(self, network_manager: NetworkManager):
        self.vpc_dir = Path(__file__).parent.parent / "vpcs"
        self.vpc_dir.mkdir(parents=True, exist_ok=True)
        self.network_manager = network_manager
        self.vpcs: Dict[str, VPC] = self._load_vpcs()
        
    def _load_vpcs(self) -> Dict[str, VPC]:
        """Load VPCs from disk."""
        vpcs = {}
        for vpc_file in self.vpc_dir.glob("*.json"):
            try:
                with open(vpc_file) as f:
                    data = json.load(f)
                    vpc = VPC(
                        name=data['name'],
                        cidr=data['cidr'],
                        subnets=data.get('subnets', {}),
                        used_private_ips=data.get('used_private_ips', {}),
                        used_public_ips=data.get('used_public_ips', {})
                    )
                    vpcs[vpc.name] = vpc
            except Exception as e:
                logger.error(f"Error loading VPC from {vpc_file}: {e}")
        return vpcs
    
    def create_vpc(self, name: str, cidr: str = "192.168.0.0/16") -> VPC:
        """Create a new VPC with corresponding network."""
        if name in self.vpcs:
            raise VPCError(f"VPC {name} already exists")
            
        # Validate CIDR
        try:
            ipaddress.ip_network(cidr)
        except ValueError as e:
            raise VPCError(f"Invalid CIDR block: {e}")
            
        vpc = VPC(
            name=name, 
            cidr=cidr, 
            subnets={},
            used_private_ips={},
            used_public_ips={}
        )
        
        try:
            # Create corresponding network in libvirt
            self.network_manager.create_network(
                name=name,
                subnet=cidr,
                network_type=NetworkType.NAT
            )
            
            # Save VPC to disk
            with open(self.vpc_dir / f"{name}.json", 'w') as f:
                json.dump(vpc.to_dict(), f, indent=2)
                
            self.vpcs[name] = vpc
            logger.info(f"Created VPC {name} with CIDR {cidr}")
            return vpc
            
        except (NetworkError, Exception) as e:
            # Cleanup if network creation failed
            self._cleanup_failed_vpc(name)
            raise VPCError(f"Failed to create VPC {name}: {e}")
    
    def _cleanup_failed_vpc(self, name: str):
        """Cleanup resources after failed VPC creation."""
        try:
            # Remove network if it was created
            self.network_manager.delete_network(name)
        except NetworkError:
            pass
            
        # Remove VPC file if it was created
        vpc_file = self.vpc_dir / f"{name}.json"
        if vpc_file.exists():
            vpc_file.unlink()
            
        # Remove from memory if added
        if name in self.vpcs:
            del self.vpcs[name]
    
    def delete_vpc(self, name: str) -> bool:
        """Delete a VPC and its corresponding network."""
        if name not in self.vpcs:
            return False
            
        try:
            # Delete the network first
            self.network_manager.delete_network(name)
            
            # Delete VPC file
            vpc_file = self.vpc_dir / f"{name}.json"
            vpc_file.unlink()
            
            # Remove from memory
            del self.vpcs[name]
            
            logger.info(f"Deleted VPC {name}")
            return True
            
        except (NetworkError, Exception) as e:
            logger.error(f"Error deleting VPC {name}: {e}")
            raise VPCError(f"Failed to delete VPC {name}: {e}")
    
    def get_vpc(self, name: str) -> Optional[VPC]:
        """Get a VPC by name."""
        return self.vpcs.get(name)
    
    def list_vpcs(self) -> List[VPC]:
        """List all VPCs."""
        return list(self.vpcs.values())
    
    def add_subnet(self, vpc_name: str, subnet_name: str, cidr: str) -> bool:
        """Add a subnet to a VPC."""
        vpc = self.get_vpc(vpc_name)
        if not vpc:
            raise VPCError(f"VPC {vpc_name} not found")
            
        # Validate CIDR
        try:
            subnet = ipaddress.ip_network(cidr)
            vpc_network = ipaddress.ip_network(vpc.cidr)
            if not subnet.subnet_of(vpc_network):
                raise VPCError(f"Subnet CIDR {cidr} is not within VPC CIDR {vpc.cidr}")
        except ValueError as e:
            raise VPCError(f"Invalid subnet CIDR: {e}")
            
        if not vpc.subnets:
            vpc.subnets = {}
            
        vpc.subnets[subnet_name] = cidr
        
        try:
            # Create corresponding network for subnet
            self.network_manager.create_network(
                name=f"{vpc_name}-{subnet_name}",
                subnet=cidr,
                network_type=NetworkType.NAT
            )
            
            # Save to disk
            with open(self.vpc_dir / f"{vpc_name}.json", 'w') as f:
                json.dump(vpc.to_dict(), f, indent=2)
                
            logger.info(f"Added subnet {subnet_name} with CIDR {cidr} to VPC {vpc_name}")
            return True
            
        except NetworkError as e:
            # Cleanup if network creation failed
            self._cleanup_failed_subnet(vpc_name, subnet_name)
            raise VPCError(f"Failed to create subnet network: {e}")
    
    def _cleanup_failed_subnet(self, vpc_name: str, subnet_name: str):
        """Cleanup resources after failed subnet creation."""
        try:
            # Remove network if it was created
            self.network_manager.delete_network(f"{vpc_name}-{subnet_name}")
        except NetworkError:
            pass
            
        # Remove subnet from VPC if it was added
        vpc = self.get_vpc(vpc_name)
        if vpc and vpc.subnets and subnet_name in vpc.subnets:
            del vpc.subnets[subnet_name]
            # Save VPC state
            with open(self.vpc_dir / f"{vpc_name}.json", 'w') as f:
                json.dump(vpc.to_dict(), f, indent=2)
    
    def remove_subnet(self, vpc_name: str, subnet_name: str) -> bool:
        """Remove a subnet from a VPC."""
        vpc = self.get_vpc(vpc_name)
        if not vpc or not vpc.subnets or subnet_name not in vpc.subnets:
            return False
            
        try:
            # Delete the subnet network first
            self.network_manager.delete_network(f"{vpc_name}-{subnet_name}")
            
            # Remove subnet from VPC
            del vpc.subnets[subnet_name]
            
            # Save to disk
            with open(self.vpc_dir / f"{vpc_name}.json", 'w') as f:
                json.dump(vpc.to_dict(), f, indent=2)
                
            logger.info(f"Removed subnet {subnet_name} from VPC {vpc_name}")
            return True
            
        except NetworkError as e:
            raise VPCError(f"Failed to remove subnet network: {e}") 