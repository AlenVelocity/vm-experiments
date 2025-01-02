import json
import logging
import ipaddress
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class VPC:
    name: str
    cidr: str
    subnets: Dict[str, str] = None
    
    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'cidr': self.cidr,
            'subnets': self.subnets or {}
        }

class VPCManager:
    def __init__(self):
        self.vpc_dir = Path(__file__).parent.parent / "vpcs"
        self.vpc_dir.mkdir(parents=True, exist_ok=True)
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
                        subnets=data.get('subnets', {})
                    )
                    vpcs[vpc.name] = vpc
            except Exception as e:
                logger.error(f"Error loading VPC from {vpc_file}: {e}")
        return vpcs
    
    def create_vpc(self, name: str, cidr: str = "192.168.0.0/16") -> VPC:
        """Create a new VPC."""
        if name in self.vpcs:
            raise ValueError(f"VPC {name} already exists")
            
        # Validate CIDR
        try:
            ipaddress.ip_network(cidr)
        except ValueError as e:
            raise ValueError(f"Invalid CIDR block: {e}")
            
        vpc = VPC(name=name, cidr=cidr)
        
        # Save to disk
        with open(self.vpc_dir / f"{name}.json", 'w') as f:
            json.dump(vpc.to_dict(), f, indent=2)
            
        self.vpcs[name] = vpc
        logger.info(f"Created VPC {name} with CIDR {cidr}")
        return vpc
    
    def delete_vpc(self, name: str) -> bool:
        """Delete a VPC."""
        if name not in self.vpcs:
            return False
            
        vpc_file = self.vpc_dir / f"{name}.json"
        try:
            vpc_file.unlink()
            del self.vpcs[name]
            logger.info(f"Deleted VPC {name}")
            return True
        except Exception as e:
            logger.error(f"Error deleting VPC {name}: {e}")
            return False
    
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
            return False
            
        # Validate CIDR
        try:
            subnet = ipaddress.ip_network(cidr)
            vpc_network = ipaddress.ip_network(vpc.cidr)
            if not subnet.subnet_of(vpc_network):
                raise ValueError(f"Subnet CIDR {cidr} is not within VPC CIDR {vpc.cidr}")
        except ValueError as e:
            raise ValueError(f"Invalid subnet CIDR: {e}")
            
        if not vpc.subnets:
            vpc.subnets = {}
            
        vpc.subnets[subnet_name] = cidr
        
        # Save to disk
        with open(self.vpc_dir / f"{vpc_name}.json", 'w') as f:
            json.dump(vpc.to_dict(), f, indent=2)
            
        logger.info(f"Added subnet {subnet_name} with CIDR {cidr} to VPC {vpc_name}")
        return True
    
    def remove_subnet(self, vpc_name: str, subnet_name: str) -> bool:
        """Remove a subnet from a VPC."""
        vpc = self.get_vpc(vpc_name)
        if not vpc or not vpc.subnets or subnet_name not in vpc.subnets:
            return False
            
        del vpc.subnets[subnet_name]
        
        # Save to disk
        with open(self.vpc_dir / f"{vpc_name}.json", 'w') as f:
            json.dump(vpc.to_dict(), f, indent=2)
            
        logger.info(f"Removed subnet {subnet_name} from VPC {vpc_name}")
        return True 