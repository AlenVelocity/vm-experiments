import os
import time
import uuid
import json
import logging
import ipaddress
import subprocess
from typing import Dict, List, Optional, Any
from pathlib import Path

from app.ip_manager import IPManager, NetworkError
from app.server_manager import ServerManager, Server, ServerError

logger = logging.getLogger(__name__)

class ClusterNetworkError(Exception):
    """Error related to cluster network operations."""
    pass

class ElasticIP:
    """Represents an elastic IP that can be attached to VMs."""
    def __init__(self, ip: str, attached_to: Optional[str] = None, server_id: Optional[str] = None):
        self.ip = ip
        self.attached_to = attached_to
        self.server_id = server_id
        self.created_at = time.time()
        self.updated_at = time.time()
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "ip": self.ip,
            "attached_to": self.attached_to,
            "server_id": self.server_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ElasticIP':
        """Create from dictionary."""
        elastic_ip = cls(data["ip"], data.get("attached_to"), data.get("server_id"))
        elastic_ip.created_at = data.get("created_at", time.time())
        elastic_ip.updated_at = data.get("updated_at", time.time())
        return elastic_ip

class ClusterNetworkManager:
    """
    Manager for handling networking across multiple servers in a cluster.
    This includes:
    - VPC management across servers
    - Elastic IP allocation and management
    - Network connectivity between servers
    """
    
    def __init__(self, server_manager: ServerManager, ip_manager: IPManager):
        """Initialize the cluster network manager."""
        self.server_manager = server_manager
        self.ip_manager = ip_manager
        self.elastic_ips: Dict[str, ElasticIP] = {}
        self.overlay_networks: Dict[str, Dict] = {}
        self._load_elastic_ips()
        self._load_overlay_networks()
    
    def _load_elastic_ips(self) -> None:
        """Load elastic IPs from config file."""
        config_path = Path("data/elastic_ips.json")
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    ip_data = json.load(f)
                
                for ip, data in ip_data.items():
                    self.elastic_ips[ip] = ElasticIP.from_dict(data)
            else:
                logger.info("No elastic IP config found, creating new one")
                self.elastic_ips = {}
        except Exception as e:
            logger.error(f"Error loading elastic IPs: {e}")
            self.elastic_ips = {}
    
    def _save_elastic_ips(self) -> None:
        """Save elastic IPs to config file."""
        config_path = Path("data/elastic_ips.json")
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            
            ip_data = {}
            for ip, elastic_ip in self.elastic_ips.items():
                ip_data[ip] = elastic_ip.to_dict()
            
            with open(config_path, "w") as f:
                json.dump(ip_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving elastic IPs: {e}")
    
    def _load_overlay_networks(self) -> None:
        """Load overlay networks from config file."""
        config_path = Path("data/overlay_networks.json")
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    self.overlay_networks = json.load(f)
            else:
                logger.info("No overlay network config found, creating new one")
                self.overlay_networks = {}
        except Exception as e:
            logger.error(f"Error loading overlay networks: {e}")
            self.overlay_networks = {}
    
    def _save_overlay_networks(self) -> None:
        """Save overlay networks to config file."""
        config_path = Path("data/overlay_networks.json")
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump(self.overlay_networks, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving overlay networks: {e}")
    
    def allocate_elastic_ip(self) -> str:
        """Allocate a new elastic IP."""
        available_ips = []
        for ip, eip in self.elastic_ips.items():
            if not eip.attached_to:
                available_ips.append(ip)
        
        if available_ips:
            return available_ips[0]
        
        base_prefix = "10.100"
        existing_ips = set(self.elastic_ips.keys())
        
        for third_octet in range(1, 255):
            for fourth_octet in range(1, 255):
                candidate_ip = f"{base_prefix}.{third_octet}.{fourth_octet}"
                if candidate_ip not in existing_ips:
                    self.elastic_ips[candidate_ip] = ElasticIP(candidate_ip)
                    self._save_elastic_ips()
                    return candidate_ip
        
        raise ClusterNetworkError("No more elastic IPs available")
    
    def attach_elastic_ip(self, ip: str, vm_id: str, server_id: str) -> None:
        """Attach an elastic IP to a VM."""
        if ip not in self.elastic_ips:
            raise ClusterNetworkError(f"Elastic IP {ip} not found")
        
        eip = self.elastic_ips[ip]
        if eip.attached_to:
            raise ClusterNetworkError(f"Elastic IP {ip} is already attached to VM {eip.attached_to}")
        
        try:
            server = self.server_manager.get_server(server_id)
        except ServerError as e:
            raise ClusterNetworkError(f"Server {server_id} not found: {str(e)}")
        
        eip.attached_to = vm_id
        eip.server_id = server_id
        eip.updated_at = time.time()
        
        self._save_elastic_ips()
        
        try:
            setup_cmd = f"sudo iptables -t nat -A PREROUTING -d {ip} -j DNAT --to-destination <vm_ip>"
            self.server_manager.execute_command(server_id, setup_cmd)
            logger.info(f"Successfully attached elastic IP {ip} to VM {vm_id} on server {server.name}")
        except Exception as e:
            eip.attached_to = None
            eip.server_id = None
            self._save_elastic_ips()
            logger.error(f"Error configuring elastic IP {ip} for VM {vm_id}: {e}")
            raise ClusterNetworkError(f"Failed to configure elastic IP: {str(e)}")
    
    def detach_elastic_ip(self, ip: str) -> None:
        """Detach an elastic IP from a VM."""
        if ip not in self.elastic_ips:
            raise ClusterNetworkError(f"Elastic IP {ip} not found")
        
        eip = self.elastic_ips[ip]
        if not eip.attached_to or not eip.server_id:
            logger.warning(f"Elastic IP {ip} is not attached to any VM")
            return
        
        vm_id = eip.attached_to
        server_id = eip.server_id
        
        try:
            server = self.server_manager.get_server(server_id)
            
            cleanup_cmd = f"sudo iptables -t nat -D PREROUTING -d {ip} -j DNAT --to-destination <vm_ip>"
            self.server_manager.execute_command(server_id, cleanup_cmd)
            
            eip.attached_to = None
            eip.server_id = None
            eip.updated_at = time.time()
            
            self._save_elastic_ips()
            
            logger.info(f"Successfully detached elastic IP {ip} from VM {vm_id}")
        except Exception as e:
            logger.error(f"Error detaching elastic IP {ip} from VM {vm_id}: {e}")
            raise ClusterNetworkError(f"Failed to detach elastic IP: {str(e)}")
    
    def list_elastic_ips(self) -> List[Dict]:
        """List all elastic IPs."""
        return [eip.to_dict() for eip in self.elastic_ips.values()]
    
    def create_overlay_network(self, name: str, cidr: str) -> Dict:
        """Create a new overlay network spanning all servers."""
        if name in self.overlay_networks:
            raise ClusterNetworkError(f"Overlay network {name} already exists")
        
        try:
            network = ipaddress.IPv4Network(cidr)
        except ValueError as e:
            raise ClusterNetworkError(f"Invalid CIDR: {str(e)}")
        
        overlay_network = {
            "name": name,
            "cidr": cidr,
            "created_at": time.time(),
            "updated_at": time.time(),
            "servers": []
        }
        
        online_servers = [s for s in self.server_manager.list_servers() if s.status == "online"]
        if not online_servers:
            raise ClusterNetworkError("No online servers available to create overlay network")
        
        for server in online_servers:
            overlay_network["servers"].append({
                "server_id": server.id,
                "server_name": server.name,
                "status": "pending"
            })
        
        self.overlay_networks[name] = overlay_network
        self._save_overlay_networks()
        
        for server_info in overlay_network["servers"]:
            server_id = server_info["server_id"]
            try:
                setup_cmd = f"echo 'Setting up overlay network {name} with CIDR {cidr}'"
                self.server_manager.execute_command(server_id, setup_cmd)
                server_info["status"] = "configured"
            except Exception as e:
                logger.error(f"Error configuring overlay network on server {server_id}: {e}")
                server_info["status"] = "failed"
        
        overlay_network["updated_at"] = time.time()
        self._save_overlay_networks()
        
        return overlay_network
    
    def delete_overlay_network(self, name: str) -> None:
        """Delete an overlay network."""
        if name not in self.overlay_networks:
            raise ClusterNetworkError(f"Overlay network {name} not found")
        
        overlay_network = self.overlay_networks[name]
        
        for server_info in overlay_network["servers"]:
            server_id = server_info["server_id"]
            try:
                cleanup_cmd = f"echo 'Cleaning up overlay network {name}'"
                self.server_manager.execute_command(server_id, cleanup_cmd)
            except Exception as e:
                logger.error(f"Error cleaning up overlay network on server {server_id}: {e}")
        
        del self.overlay_networks[name]
        self._save_overlay_networks()
    
    def list_overlay_networks(self) -> List[Dict]:
        """List all overlay networks."""
        return list(self.overlay_networks.values())
    
    def get_overlay_network(self, name: str) -> Dict:
        """Get an overlay network by name."""
        if name not in self.overlay_networks:
            raise ClusterNetworkError(f"Overlay network {name} not found")
        
        return self.overlay_networks[name]
    
    def setup_vpc_on_all_servers(self, vpc_name: str, cidr: str) -> None:
        """Set up a VPC on all servers."""
        try:
            self.ip_manager.get_network(vpc_name)
            raise ClusterNetworkError(f"VPC {vpc_name} already exists")
        except NetworkError:
            pass
        
        self.ip_manager.create_network(vpc_name, cidr)
        
        vpc_config = self.ip_manager.get_network(vpc_name)
        
        for server in self.server_manager.list_servers():
            if server.status != "online":
                continue
            
            try:
                setup_cmd = f"echo 'Setting up VPC {vpc_name} with CIDR {cidr}'"
                self.server_manager.execute_command(server.id, setup_cmd)
            except Exception as e:
                logger.error(f"Error setting up VPC on server {server.id}: {e}")
    
    def delete_vpc_from_all_servers(self, vpc_name: str) -> None:
        """Delete a VPC from all servers."""
        try:
            self.ip_manager.delete_network(vpc_name)
        except NetworkError as e:
            raise ClusterNetworkError(f"Error deleting VPC {vpc_name}: {str(e)}")
        
        for server in self.server_manager.list_servers():
            if server.status != "online":
                continue
            
            try:
                cleanup_cmd = f"echo 'Cleaning up VPC {vpc_name}'"
                self.server_manager.execute_command(server.id, cleanup_cmd)
            except Exception as e:
                logger.error(f"Error cleaning up VPC on server {server.id}: {e}")
    
    def setup_cross_server_networking(self) -> None:
        """Set up networking between servers to allow VMs to communicate."""
        online_servers = [s for s in self.server_manager.list_servers() if s.status == "online"]
        if len(online_servers) < 2:
            logger.info("Not enough online servers to set up cross-server networking")
            return
        
        for i, server1 in enumerate(online_servers):
            for server2 in online_servers[i+1:]:
                try:
                    setup_cmd = f"echo 'Setting up networking between {server1.name} and {server2.name}'"
                    self.server_manager.execute_command(server1.id, setup_cmd)
                    self.server_manager.execute_command(server2.id, setup_cmd)
                except Exception as e:
                    logger.error(f"Error setting up networking between servers: {e}")
    
    def configure_nat_for_outbound(self, server_id: str) -> None:
        """Configure NAT for outbound connections on a server."""
        try:
            server = self.server_manager.get_server(server_id)
            
            nat_cmd = "sudo iptables -t nat -A POSTROUTING -s 10.0.0.0/8 -o eth0 -j MASQUERADE"
            self.server_manager.execute_command(server_id, nat_cmd)
            
            forward_cmd = "sudo sysctl -w net.ipv4.ip_forward=1"
            self.server_manager.execute_command(server_id, forward_cmd)
            
            persist_cmd = "echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.conf"
            self.server_manager.execute_command(server_id, persist_cmd)
            
            logger.info(f"Successfully configured NAT for outbound connections on server {server.name}")
        except Exception as e:
            logger.error(f"Error configuring NAT on server {server_id}: {e}")
            raise ClusterNetworkError(f"Failed to configure NAT: {str(e)}")
    
    def configure_nat_for_all_servers(self) -> None:
        """Configure NAT for outbound connections on all servers."""
        for server in self.server_manager.list_servers():
            if server.status != "online":
                continue
            
            try:
                self.configure_nat_for_outbound(server.id)
            except Exception as e:
                logger.error(f"Error configuring NAT on server {server.id}: {e}")
                
    def get_network_metrics(self) -> Dict:
        """Get network metrics across all servers."""
        metrics = {
            "total_elastic_ips": len(self.elastic_ips),
            "allocated_elastic_ips": sum(1 for eip in self.elastic_ips.values() if eip.attached_to),
            "total_overlay_networks": len(self.overlay_networks),
            "server_network_metrics": []
        }
        
        for server in self.server_manager.list_servers():
            if server.status != "online" or not server.metrics_history:
                continue
            
            latest_metrics = server.metrics_history[-1]
            metrics["server_network_metrics"].append({
                "server_id": server.id,
                "server_name": server.name,
                "network_rx": latest_metrics.network_rx,
                "network_tx": latest_metrics.network_tx,
                "timestamp": latest_metrics.timestamp
            })
        
        return metrics 