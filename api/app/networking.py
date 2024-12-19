import ipaddress
import subprocess
from typing import Optional, Tuple, Dict, List
import os
from pathlib import Path
import json
import random

class NetworkType:
    PUBLIC = "public"
    PRIVATE = "private"

class Network:
    def __init__(self, name: str, subnet: str, network_type: str):
        self.name = name
        self.subnet = subnet
        self.network_type = network_type
        self.bridge_name = f"br{abs(hash(subnet)) % 1000}"
        self.connected_vms: List[str] = []
        self.is_configured = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "subnet": self.subnet,
            "network_type": self.network_type,
            "bridge_name": self.bridge_name,
            "connected_vms": self.connected_vms,
            "is_configured": self.is_configured
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Network':
        network = cls(data["name"], data["subnet"], data["network_type"])
        network.bridge_name = data["bridge_name"]
        network.connected_vms = data.get("connected_vms", [])
        network.is_configured = data.get("is_configured", False)
        return network

class NetworkManager:
    def __init__(self):
        self.networks_dir = Path("networks")
        self.networks_dir.mkdir(parents=True, exist_ok=True)
        self.networks: Dict[str, Network] = self._load_networks()
        
        # Create default networks if they don't exist
        self._ensure_default_networks()

    def _ensure_default_networks(self):
        """Ensure default networks exist in the configuration."""
        if "public-net" not in self.networks:
            self.networks["public-net"] = Network(
                "public-net",
                "192.168.100.0/24",
                NetworkType.PUBLIC
            )
            self._save_networks()

        if "private-net" not in self.networks:
            self.networks["private-net"] = Network(
                "private-net",
                "10.0.0.0/24",
                NetworkType.PRIVATE
            )
            self._save_networks()

    def _load_networks(self) -> Dict[str, Network]:
        """Load existing networks from disk."""
        networks = {}
        config_file = self.networks_dir / "networks.json"
        if config_file.exists():
            with open(config_file) as f:
                data = json.load(f)
                for network_data in data.values():
                    network = Network.from_dict(network_data)
                    networks[network.name] = network
        return networks

    def _save_networks(self) -> None:
        """Save networks configuration to disk."""
        config_file = self.networks_dir / "networks.json"
        with open(config_file, "w") as f:
            json.dump({name: net.to_dict() for name, net in self.networks.items()}, f)

    def create_network(self, name: str, subnet: str, network_type: str) -> Network:
        """Create a new network configuration (does not set up the actual network)."""
        if name in self.networks:
            raise ValueError(f"Network {name} already exists")

        network = Network(name, subnet, network_type)
        self.networks[name] = network
        self._save_networks()
        return network

    def delete_network(self, name: str) -> None:
        """Delete a network configuration."""
        if name not in self.networks:
            return

        network = self.networks[name]
        if network.is_configured:
            # Only try to delete network devices if they were configured
            try:
                if network.network_type == NetworkType.PUBLIC:
                    subprocess.run(['sudo', 'iptables', '-t', 'nat', '-D', 'POSTROUTING', 
                                '-s', network.subnet, '-j', 'MASQUERADE'], check=True)
                subprocess.run(['sudo', 'ip', 'link', 'delete', network.bridge_name], 
                            check=True)
            except subprocess.CalledProcessError:
                pass

        del self.networks[name]
        self._save_networks()

    def get_network_args(self, vm_id: str, network_name: Optional[str] = None) -> list[str]:
        """Get QEMU network arguments for a VM."""
        if not network_name:
            # Default user-mode networking
            return [
                '-device', 'virtio-net-pci,netdev=net0',
                '-netdev', 'user,id=net0,hostfwd=tcp::2222-:22'
            ]

        if network_name not in self.networks:
            raise ValueError(f"Network {network_name} does not exist")

        network = self.networks[network_name]
        tap_number = abs(hash(f"{network_name}-{vm_id}")) % 1000
        tap_name = f"tap{tap_number}"

        # Add VM to network's connected VMs
        if vm_id not in network.connected_vms:
            network.connected_vms.append(vm_id)
            self._save_networks()

        return [
            '-device', f'virtio-net-pci,netdev=net0',
            '-netdev', f'tap,id=net0,ifname={tap_name},script=no,downscript=no'
        ]

    def disconnect_vm(self, vm_id: str) -> None:
        """Disconnect a VM from its network."""
        for network in self.networks.values():
            if vm_id in network.connected_vms:
                network.connected_vms.remove(vm_id)
                if network.is_configured:
                    tap_name = f"tap{abs(hash(f'{network.name}-{vm_id}')) % 1000}"
                    try:
                        subprocess.run(['sudo', 'ip', 'link', 'delete', tap_name], 
                                    check=True)
                    except subprocess.CalledProcessError:
                        pass

        self._save_networks()

    def list_networks(self) -> List[Dict]:
        """List all networks."""
        return [
            {
                "name": name,
                "subnet": net.subnet,
                "type": net.network_type,
                "connected_vms": len(net.connected_vms),
                "is_configured": net.is_configured
            }
            for name, net in self.networks.items()
        ]

    def get_network(self, name: str) -> Optional[Network]:
        """Get a network by name."""
        return self.networks.get(name)

    def cleanup_networks(self) -> None:
        """Clean up all networks."""
        for name in list(self.networks.keys()):
            self.delete_network(name) 