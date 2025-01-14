import ipaddress
import subprocess
from typing import Optional, Tuple, Dict, List
import os
from pathlib import Path
import json
import random
from enum import Enum
import libvirt
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

logger = logging.getLogger(__name__)

class NetworkType(Enum):
    BRIDGE = "bridge"
    NAT = "nat"

class NetworkError(Exception):
    """Base exception for network-related errors"""
    pass

class NetworkCreationError(NetworkError):
    """Raised when network creation fails"""
    pass

class NetworkNotFoundError(NetworkError):
    """Raised when a network is not found"""
    pass

@dataclass
class NetworkConfig:
    name: str
    subnet: str
    network_type: NetworkType
    bridge_name: Optional[str] = None
    autostart: bool = True

class NetworkManager:
    def __init__(self, conn: libvirt.virConnect):
        self.conn = conn
        self.networks = {}
        self._load_networks()

    def _load_networks(self):
        """Load existing networks from libvirt."""
        try:
            for net in self.conn.listAllNetworks():
                name = net.name()
                xml = net.XMLDesc()
                root = ET.fromstring(xml)
                
                # Get network type
                forward = root.find('forward')
                net_type = NetworkType.NAT if forward is not None else NetworkType.BRIDGE
                
                # Get bridge name
                bridge = root.find('bridge')
                bridge_name = bridge.get('name') if bridge is not None else None
                
                # Get IP configuration
                ip = root.find('ip')
                if ip is not None:
                    address = ip.get('address')
                    netmask = ip.get('netmask')
                    if address and netmask:
                        subnet = str(ipaddress.IPv4Network(f"{address}/{netmask}", strict=False))
                    else:
                        subnet = None
                else:
                    subnet = None
                
                self.networks[name] = {
                    'type': net_type,
                    'bridge': bridge_name,
                    'subnet': subnet,
                    'active': net.isActive(),
                    'persistent': net.isPersistent()
                }
        except libvirt.libvirtError as e:
            logger.error(f"Failed to load networks: {e}")
            raise NetworkError(f"Failed to load networks: {e}")

    def _generate_network_xml(self, config: NetworkConfig) -> str:
        """Generate network XML configuration."""
        try:
            network = ipaddress.IPv4Network(config.subnet)
            bridge_name = config.bridge_name or f"virbr{len(self.networks)}"
            
            root = ET.Element('network')
            ET.SubElement(root, 'name').text = config.name
            
            bridge = ET.SubElement(root, 'bridge')
            bridge.set('name', bridge_name)
            bridge.set('stp', 'on')
            bridge.set('delay', '0')
            
            # Add IP configuration
            ip = ET.SubElement(root, 'ip')
            ip.set('address', str(network[1]))
            ip.set('netmask', str(network.netmask))
            
            # Add DHCP configuration
            dhcp = ET.SubElement(ip, 'dhcp')
            range_elem = ET.SubElement(dhcp, 'range')
            range_elem.set('start', str(network[2]))
            range_elem.set('end', str(network[-2]))
            
            if config.network_type == NetworkType.NAT:
                forward = ET.SubElement(root, 'forward')
                forward.set('mode', 'nat')
                nat = ET.SubElement(forward, 'nat')
                port = ET.SubElement(nat, 'port')
                port.set('start', '1024')
                port.set('end', '65535')
            
            return ET.tostring(root).decode()
        except Exception as e:
            logger.error(f"Failed to generate network XML: {e}")
            raise NetworkCreationError(f"Failed to generate network XML: {e}")

    def create_network(self, name: str, subnet: str, network_type: NetworkType = NetworkType.NAT) -> bool:
        """Create a new libvirt network."""
        try:
            config = NetworkConfig(name=name, subnet=subnet, network_type=network_type)
            xml = self._generate_network_xml(config)
            
            net = self.conn.networkDefineXML(xml)
            if config.autostart:
                net.setAutostart(True)
            net.create()
            
            self._load_networks()
            return True
        except (libvirt.libvirtError, NetworkError) as e:
            logger.error(f"Failed to create network {name}: {e}")
            self._cleanup_failed_network(name)
            raise NetworkCreationError(f"Failed to create network {name}: {e}")

    def _cleanup_failed_network(self, name: str):
        """Cleanup any leftover network resources after failed creation."""
        try:
            net = self.conn.networkLookupByName(name)
            if net.isActive():
                net.destroy()
            if net.isPersistent():
                net.undefine()
        except libvirt.libvirtError:
            pass

    def get_default_network(self) -> Optional[dict]:
        """Get the default network configuration."""
        return self.get_network('default')

    def ensure_network_exists(self, name: str) -> str:
        """Ensure a network exists, falling back to 'default' if specified network doesn't exist."""
        network = self.get_network(name)
        if network is None:
            default_network = self.get_default_network()
            if default_network is None:
                raise NetworkNotFoundError(f"Network '{name}' not found and no default network available")
            logger.warning(f"Network '{name}' not found, falling back to 'default'")
            return 'default'
        return name

    def delete_network(self, name: str) -> bool:
        """Delete a libvirt network."""
        try:
            net = self.conn.networkLookupByName(name)
            if net.isActive():
                net.destroy()
            if net.isPersistent():
                net.undefine()
            
            if name in self.networks:
                del self.networks[name]
            return True
        except libvirt.libvirtError as e:
            logger.error(f"Failed to delete network {name}: {e}")
            raise NetworkError(f"Failed to delete network {name}: {e}")

    def get_network(self, name: str) -> Optional[dict]:
        """Get network details."""
        return self.networks.get(name)

    def list_networks(self) -> List[Dict]:
        """List all networks."""
        return [
            {
                "name": name,
                "type": str(net["type"].value),
                "bridge": net["bridge"],
                "subnet": net["subnet"],
                "active": net["active"],
                "persistent": net.get("persistent", False)
            }
            for name, net in self.networks.items()
        ]

    def start_network(self, name: str) -> bool:
        """Start a network."""
        try:
            net = self.conn.networkLookupByName(name)
            if not net.isActive():
                net.create()
            self._load_networks()
            return True
        except libvirt.libvirtError as e:
            logger.error(f"Failed to start network {name}: {e}")
            raise NetworkError(f"Failed to start network {name}: {e}")

    def stop_network(self, name: str) -> bool:
        """Stop a network."""
        try:
            net = self.conn.networkLookupByName(name)
            if net.isActive():
                net.destroy()
            self._load_networks()
            return True
        except libvirt.libvirtError as e:
            logger.error(f"Failed to stop network {name}: {e}")
            raise NetworkError(f"Failed to stop network {name}: {e}") 