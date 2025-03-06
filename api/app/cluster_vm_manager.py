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
    Manager for handling VMs across multiple servers in a cluster.
    This class delegates operations to the appropriate server's LibvirtManager.
    """
    
    def __init__(self, server_manager: ServerManager, ip_manager: IPManager):
        """Initialize the cluster VM manager."""
        self.server_manager = server_manager
        self.ip_manager = ip_manager
        self.vm_servers = {}
        self._load_vm_server_mapping()
    
    def _load_vm_server_mapping(self) -> None:
        """Load VM to server mapping from config file."""
        config_path = Path("data/vm_server_mapping.json")
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    self.vm_servers = json.load(f)
            else:
                logger.info("No VM server mapping found, creating new one")
                self.vm_servers = {}
        except Exception as e:
            logger.error(f"Error loading VM server mapping: {e}")
            self.vm_servers = {}
    
    def _save_vm_server_mapping(self) -> None:
        """Save VM to server mapping to config file."""
        config_path = Path("data/vm_server_mapping.json")
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump(self.vm_servers, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving VM server mapping: {e}")
    
    def _select_server_for_vm(self, config: VMConfig) -> Server:
        """Select the best server for a new VM."""
        server = self.server_manager.select_server_for_vm(
            cpu_cores=config.cpu_cores,
            memory_mb=config.memory_mb,
            disk_gb=config.disk_size_gb
        )
        
        if not server:
            raise ClusterVMError("No suitable server found for VM with the requested resources")
        
        return server
    
    def _get_vm_server(self, vm_id: str) -> Tuple[Server, LibvirtManager]:
        """Get the server for a VM and initialize a LibvirtManager for it."""
        if vm_id not in self.vm_servers:
            raise ClusterVMError(f"VM with ID {vm_id} not found in server mapping")
        
        server_id = self.vm_servers[vm_id]
        
        try:
            server = self.server_manager.get_server(server_id)
        except ServerError as e:
            raise ClusterVMError(f"Error retrieving server for VM {vm_id}: {str(e)}")
        
        try:
            libvirt_uri = server.get_libvirt_uri()
            logger.info(f"Connecting to libvirt on server {server.name} at {libvirt_uri}")
            
            conn = libvirt.open(libvirt_uri)
            if not conn:
                raise ClusterVMError(f"Failed to connect to libvirt on server {server.name}")
            
            vm_manager = LibvirtManager(ip_manager=self.ip_manager)
            vm_manager.conn = conn
            
            return server, vm_manager
        except Exception as e:
            logger.error(f"Error creating LibvirtManager for server {server.name}: {e}")
            raise ClusterVMError(f"Failed to connect to server {server.name}: {str(e)}")
    
    def create_vm(self, config: VMConfig) -> VM:
        """Create a new VM on the most suitable server."""
        server = self._select_server_for_vm(config)
        logger.info(f"Selected server {server.name} for VM {config.name}")
        
        try:
            libvirt_uri = server.get_libvirt_uri()
            conn = libvirt.open(libvirt_uri)
            if not conn:
                raise ClusterVMError(f"Failed to connect to libvirt on server {server.name}")
            
            vm_manager = LibvirtManager(ip_manager=self.ip_manager)
            vm_manager.conn = conn
            
            vm = vm_manager.create_vm(config)
            
            self.vm_servers[vm.id] = server.id
            self._save_vm_server_mapping()
            
            server.vm_count += 1
            self.server_manager._save_servers()
            
            return vm
        except Exception as e:
            logger.error(f"Error creating VM on server {server.name}: {e}")
            raise ClusterVMError(f"Failed to create VM on server {server.name}: {str(e)}")
    
    def get_vm(self, vm_id: str) -> VM:
        """Get a VM by ID."""
        try:
            server, vm_manager = self._get_vm_server(vm_id)
            return vm_manager.get_vm(vm_id)
        except Exception as e:
            logger.error(f"Error retrieving VM {vm_id}: {e}")
            raise ClusterVMError(f"Failed to retrieve VM {vm_id}: {str(e)}")
    
    def list_vms(self) -> List[VM]:
        """List all VMs across all servers."""
        all_vms = []
        
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
                
                server_vms = vm_manager.list_vms()
                
                for vm in server_vms:
                    if vm.id not in self.vm_servers:
                        self.vm_servers[vm.id] = server.id
                
                all_vms.extend(server_vms)
            except Exception as e:
                logger.error(f"Error listing VMs on server {server.name}: {e}")
        
        self._save_vm_server_mapping()
        
        return all_vms
    
    def delete_vm(self, vm_id: str) -> None:
        """Delete a VM."""
        try:
            server, vm_manager = self._get_vm_server(vm_id)
            
            vm_manager.delete_vm(vm_id)
            
            if server.vm_count > 0:
                server.vm_count -= 1
                self.server_manager._save_servers()
            
            del self.vm_servers[vm_id]
            self._save_vm_server_mapping()
        except Exception as e:
            logger.error(f"Error deleting VM {vm_id}: {e}")
            raise ClusterVMError(f"Failed to delete VM {vm_id}: {str(e)}")
    
    def get_vm_status(self, vm_id: str) -> str:
        """Get the status of a VM."""
        try:
            server, vm_manager = self._get_vm_server(vm_id)
            return vm_manager.get_vm_status(vm_id)
        except Exception as e:
            logger.error(f"Error getting status for VM {vm_id}: {e}")
            return VMStatus.NOT_FOUND
    
    def get_vm_metrics(self, vm_id: str) -> Dict[str, Any]:
        """Get metrics for a VM."""
        try:
            server, vm_manager = self._get_vm_server(vm_id)
            vm = vm_manager.get_vm(vm_id)
            if not vm:
                raise ClusterVMError(f"VM with ID {vm_id} not found")
            
            return vm_manager.get_metrics(vm)
        except Exception as e:
            logger.error(f"Error getting metrics for VM {vm_id}: {e}")
            raise ClusterVMError(f"Failed to get metrics for VM {vm_id}: {str(e)}")
    
    def migrate_vm(self, vm_id: str, destination_server_id: str, live: bool = True) -> None:
        """Migrate a VM from its current server to a destination server."""
        try:
            source_server, source_vm_manager = self._get_vm_server(vm_id)
            
            try:
                destination_server = self.server_manager.get_server(destination_server_id)
            except ServerError as e:
                raise ClusterVMError(f"Destination server {destination_server_id} not found: {str(e)}")
            
            if destination_server.status != "online":
                raise ClusterVMError(f"Destination server {destination_server.name} is not online")
            
            vm = source_vm_manager.get_vm(vm_id)
            if not vm:
                raise ClusterVMError(f"VM with ID {vm_id} not found")
            
            if not self.server_manager.select_server_for_vm(
                cpu_cores=vm.config.cpu_cores,
                memory_mb=vm.config.memory_mb,
                disk_gb=vm.config.disk_size_gb
            ):
                raise ClusterVMError(f"Destination server {destination_server.name} does not have enough resources")
            
            dest_uri = destination_server.get_libvirt_uri()
            dest_conn = libvirt.open(dest_uri)
            if not dest_conn:
                raise ClusterVMError(f"Failed to connect to libvirt on destination server {destination_server.name}")
            
            try:
                domain = source_vm_manager.conn.lookupByName(vm.name)
            except libvirt.libvirtError:
                raise ClusterVMError(f"VM {vm.name} not found on source server")
            
            if domain.isActive():
                if live:
                    logger.info(f"Starting live migration of VM {vm.name} to server {destination_server.name}")
                    flags = libvirt.VIR_MIGRATE_LIVE | libvirt.VIR_MIGRATE_PERSIST_DEST | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
                    domain.migrate(dest_conn, flags, None, None, 0)
                else:
                    logger.info(f"Shutting down VM {vm.name} for migration")
                    domain.shutdown()
                    
                    for _ in range(30):
                        if not domain.isActive():
                            break
                        time.sleep(1)
                    
                    if domain.isActive():
                        logger.warning(f"VM {vm.name} did not shut down gracefully, forcing off")
                        domain.destroy()
                        
                        logger.info(f"Starting cold migration of VM {vm.name} to server {destination_server.name}")
                        flags = libvirt.VIR_MIGRATE_PERSIST_DEST | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
                        domain.migrate(dest_conn, flags, None, None, 0)
            else:
                logger.info(f"Starting offline migration of VM {vm.name} to server {destination_server.name}")
                flags = libvirt.VIR_MIGRATE_PERSIST_DEST | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
                domain.migrate(dest_conn, flags, None, None, 0)
            
            self.vm_servers[vm_id] = destination_server_id
            self._save_vm_server_mapping()
            
            source_server.vm_count -= 1
            destination_server.vm_count += 1
            self.server_manager._save_servers()
            
            logger.info(f"Successfully migrated VM {vm.name} to server {destination_server.name}")
        except Exception as e:
            logger.error(f"Error migrating VM {vm_id}: {e}")
            raise ClusterVMError(f"Failed to migrate VM {vm_id}: {str(e)}")
    
    def attach_disk(self, vm_id: str, disk_id: str) -> None:
        """Attach a disk to a VM."""
        try:
            server, vm_manager = self._get_vm_server(vm_id)
            vm_manager.attach_disk(disk_id, vm_id)
        except Exception as e:
            logger.error(f"Error attaching disk {disk_id} to VM {vm_id}: {e}")
            raise ClusterVMError(f"Failed to attach disk {disk_id} to VM {vm_id}: {str(e)}")
    
    def detach_disk(self, vm_id: str, disk_id: str) -> None:
        """Detach a disk from a VM."""
        try:
            server, vm_manager = self._get_vm_server(vm_id)
            vm_manager.detach_disk(disk_id)
        except Exception as e:
            logger.error(f"Error detaching disk {disk_id} from VM {vm_id}: {e}")
            raise ClusterVMError(f"Failed to detach disk {disk_id} from VM {vm_id}: {str(e)}")
    
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