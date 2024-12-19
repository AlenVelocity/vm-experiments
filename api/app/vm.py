import subprocess
import json
from pathlib import Path
from typing import Optional, List, Dict
import shutil
from dataclasses import dataclass
import uuid
import os
from .networking import NetworkManager, NetworkType

@dataclass
class VMConfig:
    name: str
    cpu_cores: int = 2
    memory_mb: int = 2048
    disk_size_gb: int = 20
    network_name: Optional[str] = None

@dataclass
class VM:
    id: str
    name: str
    config: VMConfig
    pid: Optional[int] = None
    ssh_port: Optional[int] = None
    network_info: Optional[Dict] = None

class VMManager:
    def __init__(self):
        self.vm_dir = Path("vms")
        self.vm_dir.mkdir(parents=True, exist_ok=True)
        self.network_manager = NetworkManager()
        self.vms: dict[str, VM] = self._load_vms()

        # Create default networks if they don't exist
        self._ensure_default_networks()

    def _ensure_default_networks(self):
        """Ensure default public and private networks exist."""
        networks = self.network_manager.list_networks()
        network_names = {net["name"] for net in networks}

        if "public-net" not in network_names:
            self.network_manager.create_network(
                "public-net",
                "192.168.100.0/24",
                NetworkType.PUBLIC
            )

        if "private-net" not in network_names:
            self.network_manager.create_network(
                "private-net",
                "10.0.0.0/24",
                NetworkType.PRIVATE
            )

    def _load_vms(self) -> dict[str, VM]:
        """Load existing VMs from disk."""
        vms = {}
        for vm_dir in self.vm_dir.glob("*"):
            if not vm_dir.is_dir():
                continue
            
            config_file = vm_dir / "config.json"
            if not config_file.exists():
                continue

            try:
                with open(config_file) as f:
                    config_data = json.load(f)
                    config = VMConfig(**config_data)
                    vm = VM(
                        id=vm_dir.name,
                        name=config.name,
                        config=config,
                        network_info=config_data.get("network_info")
                    )
                    vms[vm.id] = vm
            except Exception:
                continue

        return vms

    def create_vm(self, config: VMConfig) -> VM:
        """Create a new VM."""
        vm_id = str(uuid.uuid4())
        vm = VM(id=vm_id, name=config.name, config=config)
        self.vms[vm_id] = vm

        # Create VM directory
        vm_path = self.vm_dir / vm_id
        vm_path.mkdir(parents=True, exist_ok=True)

        # Save VM config
        with open(vm_path / "config.json", "w") as f:
            config_dict = {
                "name": config.name,
                "cpu_cores": config.cpu_cores,
                "memory_mb": config.memory_mb,
                "disk_size_gb": config.disk_size_gb,
                "network_name": config.network_name
            }
            json.dump(config_dict, f)

        # Create disk image
        qcow2_file = vm_path / f"{config.name}.qcow2"
        subprocess.run([
            'qemu-img', 'create', '-f', 'qcow2',
            str(qcow2_file), f"{config.disk_size_gb}G"
        ], check=True)

        return vm

    def delete_vm(self, vm_id: str) -> bool:
        """Delete a VM."""
        if vm_id not in self.vms:
            return False

        vm = self.vms[vm_id]
        if vm.pid:
            try:
                subprocess.run(['kill', '-TERM', str(vm.pid)], check=True)
            except subprocess.CalledProcessError:
                pass

        # Disconnect from network
        if vm.config.network_name:
            self.network_manager.disconnect_vm(vm_id)

        # Remove VM directory
        vm_path = self.vm_dir / vm_id
        if vm_path.exists():
            shutil.rmtree(vm_path)

        del self.vms[vm_id]
        return True

    def start_vm(self, vm_id: str) -> bool:
        """Start a VM."""
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        vm_path = self.vm_dir / vm_id
        qcow2_file = vm_path / f"{vm.config.name}.qcow2"

        # Prepare network arguments
        network_args = self.network_manager.get_network_args(
            vm_id, vm.config.network_name
        )

        # Start VM
        cmd = [
            'qemu-system-aarch64',
            '-M', 'virt,highmem=off',
            '-accel', 'hvf',
            '-cpu', 'host',
            '-smp', str(vm.config.cpu_cores),
            '-m', str(vm.config.memory_mb),
            '-drive', f'file={qcow2_file},if=virtio,format=qcow2'
        ] + network_args + ['-nographic']

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        vm.pid = process.pid
        vm.ssh_port = 2222 if not vm.config.network_name else None

        # Update network info
        if vm.config.network_name:
            network = self.network_manager.get_network(vm.config.network_name)
            if network:
                vm.network_info = {
                    "name": network.name,
                    "type": network.network_type,
                    "subnet": network.subnet
                }

        return True

    def stop_vm(self, vm_id: str) -> bool:
        """Stop a VM."""
        vm = self.vms.get(vm_id)
        if not vm or not vm.pid:
            return False

        try:
            subprocess.run(['kill', '-TERM', str(vm.pid)], check=True)
            vm.pid = None
            vm.ssh_port = None
            return True
        except subprocess.CalledProcessError:
            return False

    def attach_network(self, vm_id: str, network_name: str) -> bool:
        """Attach a VM to a network."""
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        network = self.network_manager.get_network(network_name)
        if not network:
            return False

        # Update VM config
        vm.config.network_name = network_name
        vm.network_info = {
            "name": network.name,
            "type": network.network_type,
            "subnet": network.subnet
        }

        # Save updated config
        config_file = self.vm_dir / vm_id / "config.json"
        with open(config_file, "w") as f:
            config_dict = {
                "name": vm.config.name,
                "cpu_cores": vm.config.cpu_cores,
                "memory_mb": vm.config.memory_mb,
                "disk_size_gb": vm.config.disk_size_gb,
                "network_name": network_name,
                "network_info": vm.network_info
            }
            json.dump(config_dict, f)

        return True

    def detach_network(self, vm_id: str) -> bool:
        """Detach a VM from its network."""
        vm = self.vms.get(vm_id)
        if not vm:
            return False

        if vm.config.network_name:
            self.network_manager.disconnect_vm(vm_id)
            vm.config.network_name = None
            vm.network_info = None

            # Save updated config
            config_file = self.vm_dir / vm_id / "config.json"
            with open(config_file, "w") as f:
                config_dict = {
                    "name": vm.config.name,
                    "cpu_cores": vm.config.cpu_cores,
                    "memory_mb": vm.config.memory_mb,
                    "disk_size_gb": vm.config.disk_size_gb,
                    "network_name": None,
                    "network_info": None
                }
                json.dump(config_dict, f)

        return True

    def list_vms(self) -> List[VM]:
        """List all VMs."""
        return list(self.vms.values())

    def get_vm(self, vm_id: str) -> Optional[VM]:
        """Get a VM by ID."""
        return self.vms.get(vm_id)

    def list_networks(self) -> List[Dict]:
        """List all available networks."""
        return self.network_manager.list_networks()

    def cleanup(self):
        """Clean up all resources."""
        for vm in self.vms.values():
            if vm.pid:
                try:
                    subprocess.run(['kill', '-TERM', str(vm.pid)], check=True)
                except subprocess.CalledProcessError:
                    pass

        self.network_manager.cleanup_networks() 