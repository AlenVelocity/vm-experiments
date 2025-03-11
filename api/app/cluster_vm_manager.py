import os
import time
import uuid
import json
import logging
import subprocess
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import libvirt

from app.vm import LibvirtManager, VMConfig, VM, VMStatus, VMError
from app.server_manager import ServerManager, Server, ServerError
from app.ip_manager import IPManager

logger = logging.getLogger(__name__)

class ClusterVMError(Exception):
    """Error related to cluster VM operations."""
    pass

class ClusterVMManager:
    """
    Manager for handling VMs on a single server.
    This class provides a stateless interface for VM operations.
    """
    
    def __init__(self):
        """Initialize the cluster VM manager."""
        self.logger = logging.getLogger(__name__)

    def validate_vm_config(self, config: VMConfig, server: Server) -> bool:
        """Validate if a VM configuration can be supported on the server."""
        try:
            server_manager = ServerManager()
            metrics = server_manager.collect_server_metrics(server)
            
            # Check available resources
            available_memory = metrics.memory_total - metrics.memory_used
            available_disk = metrics.disk_total - metrics.disk_used
            available_cpu = server.cpu_cores * (1 - metrics.cpu_usage / 100)
            
            return (
                available_memory >= config.memory_mb * 1024 * 1024 and  # Convert MB to bytes
                available_disk >= config.disk_size_gb * 1024 * 1024 * 1024 and  # Convert GB to bytes
                available_cpu >= config.cpu_cores
            )
            
        except Exception as e:
            self.logger.error(f"Failed to validate VM config: {str(e)}")
            return False

    def create_vm(self, config: VMConfig, server: Server) -> VM:
        """Create a new VM on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterVMError("Server not accessible")
            
            # Validate VM configuration
            if not self.validate_vm_config(config, server):
                raise ClusterVMError("Server does not have sufficient resources")
            
            # Create LibvirtManager for the server
            libvirt_manager = LibvirtManager(server.get_libvirt_uri())
            
            # Create the VM
            vm = libvirt_manager.create_vm(config)
            
            return vm
            
        except Exception as e:
            raise ClusterVMError(f"Failed to create VM: {str(e)}")

    def get_vm(self, vm_id: str, server: Server) -> VM:
        """Get a VM from the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterVMError("Server not accessible")
            
            # Get VM details
            libvirt_manager = LibvirtManager(server.get_libvirt_uri())
            return libvirt_manager.get_vm(vm_id)
            
        except Exception as e:
            raise ClusterVMError(f"Failed to get VM: {str(e)}")

    def list_vms(self, server: Server) -> List[VM]:
        """List all VMs on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterVMError("Server not accessible")
            
            # List VMs
            libvirt_manager = LibvirtManager(server.get_libvirt_uri())
            return libvirt_manager.list_vms()
            
        except Exception as e:
            raise ClusterVMError(f"Failed to list VMs: {str(e)}")

    def delete_vm(self, vm_id: str, server: Server) -> None:
        """Delete a VM from the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterVMError("Server not accessible")
            
            # Delete VM
            libvirt_manager = LibvirtManager(server.get_libvirt_uri())
            libvirt_manager.delete_vm(vm_id)
            
        except Exception as e:
            raise ClusterVMError(f"Failed to delete VM: {str(e)}")

    def get_vm_status(self, vm_id: str, server: Server) -> str:
        """Get the status of a VM on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterVMError("Server not accessible")
            
            # Get VM status
            libvirt_manager = LibvirtManager(server.get_libvirt_uri())
            return libvirt_manager.get_vm_status(vm_id)
            
        except Exception as e:
            raise ClusterVMError(f"Failed to get VM status: {str(e)}")

    def get_vm_metrics(self, vm_id: str, server: Server) -> Dict[str, Any]:
        """Get metrics for a VM on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterVMError("Server not accessible")
            
            # Get VM metrics
            libvirt_manager = LibvirtManager(server.get_libvirt_uri())
            return libvirt_manager.get_vm_metrics(vm_id)
            
        except Exception as e:
            raise ClusterVMError(f"Failed to get VM metrics: {str(e)}")

    def migrate_vm(self, vm_id: str, source_server: Server, destination_server: Server, live: bool = True) -> None:
        """Migrate a VM from one server to another."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(source_server):
                raise ClusterVMError("Source server not accessible")
            if not server_manager.validate_server(destination_server):
                raise ClusterVMError("Destination server not accessible")
            
            # Get VM details
            source_libvirt = LibvirtManager(source_server.get_libvirt_uri())
            dest_libvirt = LibvirtManager(destination_server.get_libvirt_uri())
            
            # Get VM configuration
            vm = source_libvirt.get_vm(vm_id)
            if not self.validate_vm_config(vm.config, destination_server):
                raise ClusterVMError("Destination server does not have sufficient resources")
            
            # Perform migration
            source_libvirt.migrate_vm(vm_id, dest_libvirt, live)
            
        except Exception as e:
            raise ClusterVMError(f"Failed to migrate VM: {str(e)}")

    def attach_disk(self, vm_id: str, disk_path: str, server: Server) -> None:
        """Attach a disk to a VM."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterVMError("Server not accessible")
            
            # Attach disk
            libvirt_manager = LibvirtManager(server.get_libvirt_uri())
            libvirt_manager.attach_disk(vm_id, disk_path)
            
        except Exception as e:
            raise ClusterVMError(f"Failed to attach disk: {str(e)}")

    def detach_disk(self, vm_id: str, disk_path: str, server: Server) -> None:
        """Detach a disk from a VM."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise ClusterVMError("Server not accessible")
            
            # Detach disk
            libvirt_manager = LibvirtManager(server.get_libvirt_uri())
            libvirt_manager.detach_disk(vm_id, disk_path)
            
        except Exception as e:
            raise ClusterVMError(f"Failed to detach disk: {str(e)}")

    def create_disk(self, name: str, size_gb: int) -> Dict:
        """Create a new disk."""
        online_servers = [s for s in self.server_manager.list_servers() if s.status == "online"]
        if not online_servers:
            raise ClusterVMError("No online servers available to create disk")
        
        server = online_servers[0]
        
        try:
            libvirt_uri = server.get_libvirt_uri()
            conn = libvirt.open(libvirt_uri)
            if not conn:
                raise ClusterVMError(f"Failed to connect to libvirt on server {server.name}")
            
            vm_manager = LibvirtManager(ip_manager=self.ip_manager)
            vm_manager.conn = conn
            
            return vm_manager.create_disk(name, size_gb)
        except Exception as e:
            logger.error(f"Error creating disk on server {server.name}: {e}")
            raise ClusterVMError(f"Failed to create disk on server {server.name}: {str(e)}")
    
    def list_disks(self) -> List[Dict]:
        """List all disks across all servers."""
        all_disks = []
        
        for server in self.server_manager.list_servers():
            if server.status != "online":
                continue
            
            try:
                libvirt_uri = server.get_libvirt_uri()
                conn = libvirt.open(libvirt_uri)
                if not conn:
                    logger.warning(f"Failed to connect to libvirt on server {server.name}")
                    continue
                
                vm_manager = LibvirtManager(ip_manager=self.ip_manager)
                vm_manager.conn = conn
                
                server_disks = vm_manager.list_disks()
                all_disks.extend(server_disks)
            except Exception as e:
                logger.error(f"Error listing disks on server {server.name}: {e}")
        
        return all_disks 