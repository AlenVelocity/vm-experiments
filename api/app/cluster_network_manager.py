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

class NetworkConfig:
    """Network configuration for a server."""
    def __init__(self, name: str, cidr: str, gateway: Optional[str] = None):
        self.name = name
        self.cidr = cidr
        self.gateway = gateway
        self.network = ipaddress.ip_network(cidr)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "cidr": self.cidr,
            "gateway": self.gateway
        }

class ClusterNetworkManager:
    """
    Manager for handling networking on a single server.
    This class provides a stateless interface for network operations.
    """
    
    def __init__(self):
        """Initialize the network manager."""
        self.logger = logging.getLogger(__name__)

    def validate_network_config(self, config: NetworkConfig) -> bool:
        """Validate network configuration."""
        try:
            # Validate CIDR format
            network = ipaddress.ip_network(config.cidr)
            
            # Validate gateway if provided
            if config.gateway:
                gateway = ipaddress.ip_address(config.gateway)
                if gateway not in network:
                    return False
            
            return True
        except ValueError:
            return False

    def create_network(self, config: NetworkConfig, server: Server) -> Dict:
        """Create a new network on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterNetworkError("Server not accessible")
            
            # Validate network configuration
            if not self.validate_network_config(config):
                raise ClusterNetworkError("Invalid network configuration")
            
            # Create network on server
            commands = [
                f"ip link add {config.name} type bridge",
                f"ip addr add {config.cidr} dev {config.name}",
                f"ip link set {config.name} up"
            ]
            
            for cmd in commands:
                result = server_manager.execute_command(server, cmd)
                if result["exit_code"] != 0:
                    raise ClusterNetworkError(f"Failed to execute command: {cmd}\nError: {result['stderr']}")
            
            return config.to_dict()
            
        except Exception as e:
            raise ClusterNetworkError(f"Failed to create network: {str(e)}")

    def delete_network(self, network_name: str, server: Server) -> None:
        """Delete a network from the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterNetworkError("Server not accessible")
            
            # Delete network
            cmd = f"ip link delete {network_name}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise ClusterNetworkError(f"Failed to delete network: {result['stderr']}")
            
        except Exception as e:
            raise ClusterNetworkError(f"Failed to delete network: {str(e)}")

    def list_networks(self, server: Server) -> List[Dict]:
        """List all networks on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterNetworkError("Server not accessible")
            
            # Get networks
            cmd = "ip -j link show type bridge"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise ClusterNetworkError(f"Failed to list networks: {result['stderr']}")
            
            networks = json.loads(result["stdout"])
            network_list = []
            
            for net in networks:
                # Get network details
                cmd = f"ip -j addr show dev {net['ifname']}"
                addr_result = server_manager.execute_command(server, cmd)
                if addr_result["exit_code"] == 0:
                    addr_info = json.loads(addr_result["stdout"])[0]
                    if "addr_info" in addr_info:
                        for addr in addr_info["addr_info"]:
                            if addr["family"] == "inet":
                                network_list.append({
                                    "name": net["ifname"],
                                    "cidr": f"{addr['local']}/{addr['prefixlen']}",
                                    "state": net["operstate"]
                                })
                                break
            
            return network_list
            
        except Exception as e:
            raise ClusterNetworkError(f"Failed to list networks: {str(e)}")

    def get_network(self, network_name: str, server: Server) -> Dict:
        """Get details of a network on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterNetworkError("Server not accessible")
            
            # Get network details
            cmd = f"ip -j addr show dev {network_name}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise ClusterNetworkError(f"Failed to get network details: {result['stderr']}")
            
            net_info = json.loads(result["stdout"])[0]
            if "addr_info" in net_info:
                for addr in net_info["addr_info"]:
                    if addr["family"] == "inet":
                        return {
                            "name": network_name,
                            "cidr": f"{addr['local']}/{addr['prefixlen']}",
                            "state": net_info["operstate"]
                        }
            
            raise ClusterNetworkError(f"Network {network_name} not found")
            
        except Exception as e:
            raise ClusterNetworkError(f"Failed to get network details: {str(e)}")

    def configure_nat(self, network_name: str, server: Server) -> None:
        """Configure NAT for a network on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterNetworkError("Server not accessible")
            
            # Configure NAT
            commands = [
                "sysctl -w net.ipv4.ip_forward=1",
                f"iptables -t nat -A POSTROUTING -o eth0 -s $(ip -j addr show dev {network_name} | jq -r '.[0].addr_info[0].local')/$(ip -j addr show dev {network_name} | jq -r '.[0].addr_info[0].prefixlen') -j MASQUERADE"
            ]
            
            for cmd in commands:
                result = server_manager.execute_command(server, cmd)
                if result["exit_code"] != 0:
                    raise ClusterNetworkError(f"Failed to configure NAT: {result['stderr']}")
            
        except Exception as e:
            raise ClusterNetworkError(f"Failed to configure NAT: {str(e)}")

    def get_network_metrics(self, network_name: str, server: Server) -> Dict:
        """Get metrics for a network on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterNetworkError("Server not accessible")
            
            # Get network metrics
            cmd = f"ip -s -j link show dev {network_name}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise ClusterNetworkError(f"Failed to get network metrics: {result['stderr']}")
            
            metrics = json.loads(result["stdout"])[0]
            return {
                "name": network_name,
                "rx_bytes": metrics["stats64"]["rx"]["bytes"],
                "rx_packets": metrics["stats64"]["rx"]["packets"],
                "tx_bytes": metrics["stats64"]["tx"]["bytes"],
                "tx_packets": metrics["stats64"]["tx"]["packets"]
            }
            
        except Exception as e:
            raise ClusterNetworkError(f"Failed to get network metrics: {str(e)}") 