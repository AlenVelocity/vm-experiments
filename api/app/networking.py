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

logger = logging.getLogger(__name__)

class NetworkType(Enum):
    BRIDGE = "bridge"
    NAT = "nat"

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
                    'active': net.isActive()
                }
        except libvirt.libvirtError as e:
            logger.error(f"Failed to load networks: {e}")

    def create_network(self, name: str, subnet: str, network_type: NetworkType = NetworkType.NAT) -> bool:
        """Create a new libvirt network."""
        try:
            network = ipaddress.IPv4Network(subnet)
            bridge_name = f"virbr{len(self.networks)}"
            
            xml = f"""
            <network>
                <name>{name}</name>
                <bridge name='{bridge_name}'/>
                <forward mode='nat'/>
                <ip address='{str(network[1])}' netmask='{str(network.netmask)}'>
                    <dhcp>
                        <range start='{str(network[2])}' end='{str(network[-2])}'/>
                    </dhcp>
                </ip>
            </network>
            """
            
            net = self.conn.networkDefineXML(xml)
            net.setAutostart(True)
            net.create()
            
            # Enable IP forwarding
            subprocess.run(['sudo', 'sysctl', '-w', 'net.ipv4.ip_forward=1'], check=True)
            subprocess.run(['sudo', 'sysctl', '-w', 'net.ipv4.conf.all.forwarding=1'], check=True)
            
            # Save sysctl changes
            with open('/etc/sysctl.d/99-libvirt.conf', 'w') as f:
                f.write('net.ipv4.ip_forward=1\n')
                f.write('net.ipv4.conf.all.forwarding=1\n')
            
            self._load_networks()  # Reload networks
            return True
        except (libvirt.libvirtError, ValueError) as e:
            logger.error(f"Failed to create network: {e}")
            return False

    def delete_network(self, name: str) -> bool:
        """Delete a libvirt network."""
        try:
            net = self.conn.networkLookupByName(name)
            if net.isActive():
                net.destroy()
            net.undefine()
            
            if name in self.networks:
                del self.networks[name]
            return True
        except libvirt.libvirtError as e:
            logger.error(f"Failed to delete network: {e}")
            return False

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
                "active": net["active"]
            }
            for name, net in self.networks.items()
        ]

    def start_network(self, name: str) -> bool:
        """Start a network."""
        try:
            net = self.conn.networkLookupByName(name)
            if not net.isActive():
                net.create()
            self._load_networks()  # Reload networks
            return True
        except libvirt.libvirtError as e:
            logger.error(f"Failed to start network: {e}")
            return False

    def stop_network(self, name: str) -> bool:
        """Stop a network."""
        try:
            net = self.conn.networkLookupByName(name)
            if net.isActive():
                net.destroy()
            self._load_networks()  # Reload networks
            return True
        except libvirt.libvirtError as e:
            logger.error(f"Failed to stop network: {e}")
            return False 