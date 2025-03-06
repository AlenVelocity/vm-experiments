import os
import time
import uuid
import json
import logging
import subprocess
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import libvirt

from app.server_manager import ServerManager, Server, ServerError

logger = logging.getLogger(__name__)

class ClusterStorageError(Exception):
    """Error related to cluster storage operations."""
    pass

class StorageVolume:
    """Represents a storage volume that can be attached to VMs."""
    def __init__(
        self, 
        id: str, 
        name: str, 
        size_gb: int, 
        server_id: Optional[str] = None,
        attached_to: Optional[str] = None, 
        replicated: bool = False,
        state: str = "available",
    ):
        self.id = id
        self.name = name
        self.size_gb = size_gb
        self.server_id = server_id
        self.attached_to = attached_to
        self.replicated = replicated
        self.state = state
        self.created_at = time.time()
        self.updated_at = time.time()
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "size_gb": self.size_gb,
            "server_id": self.server_id,
            "attached_to": self.attached_to,
            "replicated": self.replicated,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'StorageVolume':
        """Create from dictionary."""
        volume = cls(
            id=data["id"],
            name=data["name"],
            size_gb=data["size_gb"],
            server_id=data.get("server_id"),
            attached_to=data.get("attached_to"),
            replicated=data.get("replicated", False),
            state=data.get("state", "available")
        )
        volume.created_at = data.get("created_at", time.time())
        volume.updated_at = data.get("updated_at", time.time())
        return volume

class ClusterStorageManager:
    """
    Manager for handling storage across multiple servers in a cluster.
    This includes:
    - Volume management and replication
    - Backup management
    - Storage metrics and monitoring
    """
    
    def __init__(self, server_manager: ServerManager):
        """Initialize the cluster storage manager."""
        self.server_manager = server_manager
        self.volumes: Dict[str, StorageVolume] = {}
        self.backup_jobs: Dict[str, Dict] = {}
        self._load_volumes()
        self._load_backup_jobs()
    
    def _load_volumes(self) -> None:
        """Load volumes from config file."""
        config_path = Path("data/volumes.json")
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    volume_data = json.load(f)
                
                for volume_id, data in volume_data.items():
                    self.volumes[volume_id] = StorageVolume.from_dict(data)
            else:
                logger.info("No volume config found, creating new one")
                self.volumes = {}
        except Exception as e:
            logger.error(f"Error loading volumes: {e}")
            self.volumes = {}
    
    def _save_volumes(self) -> None:
        """Save volumes to config file."""
        config_path = Path("data/volumes.json")
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            
            volume_data = {}
            for volume_id, volume in self.volumes.items():
                volume_data[volume_id] = volume.to_dict()
            
            with open(config_path, "w") as f:
                json.dump(volume_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving volumes: {e}")
    
    def _load_backup_jobs(self) -> None:
        """Load backup jobs from config file."""
        config_path = Path("data/backup_jobs.json")
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    self.backup_jobs = json.load(f)
            else:
                logger.info("No backup job config found, creating new one")
                self.backup_jobs = {}
        except Exception as e:
            logger.error(f"Error loading backup jobs: {e}")
            self.backup_jobs = {}
    
    def _save_backup_jobs(self) -> None:
        """Save backup jobs to config file."""
        config_path = Path("data/backup_jobs.json")
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump(self.backup_jobs, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving backup jobs: {e}")
    
    def _select_server_for_volume(self, size_gb: int) -> Server:
        """Select the best server for a new volume."""
        online_servers = []
        for server in self.server_manager.list_servers():
            if server.status != "online":
                continue
            
            if not server.metrics_history:
                continue
            
            latest_metrics = server.metrics_history[-1]
            available_disk = max(0, server.disk_gb - latest_metrics.disk_used)
            
            if available_disk >= size_gb:
                online_servers.append((server, available_disk))
        
        if not online_servers:
            raise ClusterStorageError("No suitable server found for volume with the requested size")
        
        online_servers.sort(key=lambda x: x[1], reverse=True)
        
        return online_servers[0][0]
    
    def create_volume(self, name: str, size_gb: int, replicated: bool = False) -> StorageVolume:
        """Create a new storage volume."""
        server = self._select_server_for_volume(size_gb)
        logger.info(f"Selected server {server.name} for volume {name}")
        
        volume_id = str(uuid.uuid4())[:8]
        
        try:
            create_cmd = f"echo 'Creating volume {name} with size {size_gb}GB'"
            self.server_manager.execute_command(server.id, create_cmd)
            
            volume = StorageVolume(
                id=volume_id,
                name=name,
                size_gb=size_gb,
                server_id=server.id,
                replicated=replicated
            )
            
            self.volumes[volume_id] = volume
            self._save_volumes()
            
            if replicated:
                self._setup_volume_replication(volume)
            
            return volume
        except Exception as e:
            logger.error(f"Error creating volume on server {server.name}: {e}")
            raise ClusterStorageError(f"Failed to create volume on server {server.name}: {str(e)}")
    
    def _setup_volume_replication(self, volume: StorageVolume) -> None:
        """Set up replication for a volume."""
        if not volume.server_id:
            raise ClusterStorageError("Volume has no assigned server")
        
        online_servers = [s for s in self.server_manager.list_servers() 
                         if s.status == "online" and s.id != volume.server_id]
        
        if not online_servers:
            logger.warning(f"Cannot set up replication for volume {volume.id}: no other online servers")
            return
        
        replica_server = online_servers[0]
        
        try:
            replicate_cmd = f"echo 'Setting up replication for volume {volume.name} to server {replica_server.name}'"
            self.server_manager.execute_command(volume.server_id, replicate_cmd)
            
            logger.info(f"Set up replication for volume {volume.id} to server {replica_server.name}")
        except Exception as e:
            logger.error(f"Error setting up replication for volume {volume.id}: {e}")
            volume.replicated = False
            self._save_volumes()
            raise ClusterStorageError(f"Failed to set up replication: {str(e)}")
    
    def delete_volume(self, volume_id: str) -> None:
        """Delete a storage volume."""
        if volume_id not in self.volumes:
            raise ClusterStorageError(f"Volume {volume_id} not found")
        
        volume = self.volumes[volume_id]
        
        if volume.attached_to:
            raise ClusterStorageError(f"Volume {volume_id} is attached to VM {volume.attached_to}")
        
        if not volume.server_id:
            logger.warning(f"Volume {volume_id} has no server assigned, removing from config only")
            del self.volumes[volume_id]
            self._save_volumes()
            return
        
        try:
            server = self.server_manager.get_server(volume.server_id)
            
            delete_cmd = f"echo 'Deleting volume {volume.name}'"
            self.server_manager.execute_command(volume.server_id, delete_cmd)
            
            if volume.replicated:
                for s in self.server_manager.list_servers():
                    if s.id != volume.server_id and s.status == "online":
                        cleanup_cmd = f"echo 'Cleaning up replica of volume {volume.name} on server {s.name}'"
                        self.server_manager.execute_command(s.id, cleanup_cmd)
            
            del self.volumes[volume_id]
            self._save_volumes()
            
            logger.info(f"Successfully deleted volume {volume_id}")
        except Exception as e:
            logger.error(f"Error deleting volume {volume_id}: {e}")
            raise ClusterStorageError(f"Failed to delete volume {volume_id}: {str(e)}")
    
    def attach_volume(self, volume_id: str, vm_id: str, vm_server_id: str) -> None:
        """Attach a volume to a VM."""
        if volume_id not in self.volumes:
            raise ClusterStorageError(f"Volume {volume_id} not found")
        
        volume = self.volumes[volume_id]
        
        if volume.attached_to:
            raise ClusterStorageError(f"Volume {volume_id} is already attached to VM {volume.attached_to}")
        
        if not volume.server_id:
            raise ClusterStorageError(f"Volume {volume_id} has no server assigned")
        
        volume.state = "attaching"
        volume.updated_at = time.time()
        self._save_volumes()
        
        try:
            if volume.server_id == vm_server_id:
                attach_cmd = f"echo 'Attaching volume {volume.name} to VM {vm_id} locally'"
                self.server_manager.execute_command(volume.server_id, attach_cmd)
            else:
                if volume.replicated:
                    attach_cmd = f"echo 'Attaching replicated volume {volume.name} to VM {vm_id} on server {vm_server_id}'"
                    self.server_manager.execute_command(vm_server_id, attach_cmd)
                else:
                    attach_cmd = f"echo 'Setting up remote access for volume {volume.name} from server {volume.server_id} to VM {vm_id} on server {vm_server_id}'"
                    self.server_manager.execute_command(vm_server_id, attach_cmd)
            
            volume.attached_to = vm_id
            volume.state = "attached"
            volume.updated_at = time.time()
            self._save_volumes()
            
            logger.info(f"Successfully attached volume {volume_id} to VM {vm_id}")
        except Exception as e:
            volume.state = "available"
            volume.updated_at = time.time()
            self._save_volumes()
            
            logger.error(f"Error attaching volume {volume_id} to VM {vm_id}: {e}")
            raise ClusterStorageError(f"Failed to attach volume {volume_id}: {str(e)}")
    
    def detach_volume(self, volume_id: str) -> None:
        """Detach a volume from a VM."""
        if volume_id not in self.volumes:
            raise ClusterStorageError(f"Volume {volume_id} not found")
        
        volume = self.volumes[volume_id]
        
        if not volume.attached_to:
            logger.warning(f"Volume {volume_id} is not attached to any VM")
            return
        
        if not volume.server_id:
            raise ClusterStorageError(f"Volume {volume_id} has no server assigned")
        
        vm_id = volume.attached_to
        
        volume.state = "detaching"
        volume.updated_at = time.time()
        self._save_volumes()
        
        try:
            detach_cmd = f"echo 'Detaching volume {volume.name} from VM {vm_id}'"
            self.server_manager.execute_command(volume.server_id, detach_cmd)
            
            volume.attached_to = None
            volume.state = "available"
            volume.updated_at = time.time()
            self._save_volumes()
            
            logger.info(f"Successfully detached volume {volume_id} from VM {vm_id}")
        except Exception as e:
            volume.state = "attached"
            volume.updated_at = time.time()
            self._save_volumes()
            
            logger.error(f"Error detaching volume {volume_id} from VM {vm_id}: {e}")
            raise ClusterStorageError(f"Failed to detach volume {volume_id}: {str(e)}")
    
    def resize_volume(self, volume_id: str, new_size_gb: int) -> None:
        """Resize a storage volume."""
        if volume_id not in self.volumes:
            raise ClusterStorageError(f"Volume {volume_id} not found")
        
        volume = self.volumes[volume_id]
        
        if volume.attached_to:
            raise ClusterStorageError(f"Volume {volume_id} is attached to VM {volume.attached_to}, detach first")
        
        if not volume.server_id:
            raise ClusterStorageError(f"Volume {volume_id} has no server assigned")
        
        if new_size_gb <= volume.size_gb:
            raise ClusterStorageError(f"New size must be larger than current size ({volume.size_gb}GB)")
        
        try:
            resize_cmd = f"echo 'Resizing volume {volume.name} from {volume.size_gb}GB to {new_size_gb}GB'"
            self.server_manager.execute_command(volume.server_id, resize_cmd)
            
            if volume.replicated:
                for s in self.server_manager.list_servers():
                    if s.id != volume.server_id and s.status == "online":
                        replica_cmd = f"echo 'Resizing replica of volume {volume.name} on server {s.name}'"
                        self.server_manager.execute_command(s.id, replica_cmd)
            
            volume.size_gb = new_size_gb
            volume.updated_at = time.time()
            self._save_volumes()
            
            logger.info(f"Successfully resized volume {volume_id} to {new_size_gb}GB")
        except Exception as e:
            logger.error(f"Error resizing volume {volume_id}: {e}")
            raise ClusterStorageError(f"Failed to resize volume {volume_id}: {str(e)}")
    
    def create_backup(self, volume_id: str, name: str) -> Dict:
        """Create a backup of a volume."""
        if volume_id not in self.volumes:
            raise ClusterStorageError(f"Volume {volume_id} not found")
        
        volume = self.volumes[volume_id]
        
        if not volume.server_id:
            raise ClusterStorageError(f"Volume {volume_id} has no server assigned")
        
        backup_id = str(uuid.uuid4())[:8]
        
        try:
            backup_cmd = f"echo 'Creating backup of volume {volume.name}'"
            self.server_manager.execute_command(volume.server_id, backup_cmd)
            
            backup_job = {
                "id": backup_id,
                "name": name,
                "volume_id": volume_id,
                "volume_name": volume.name,
                "server_id": volume.server_id,
                "status": "completed",
                "size_gb": volume.size_gb,
                "created_at": time.time(),
                "completed_at": time.time()
            }
            
            self.backup_jobs[backup_id] = backup_job
            self._save_backup_jobs()
            
            logger.info(f"Successfully created backup {backup_id} for volume {volume_id}")
            
            return backup_job
        except Exception as e:
            logger.error(f"Error creating backup for volume {volume_id}: {e}")
            raise ClusterStorageError(f"Failed to create backup for volume {volume_id}: {str(e)}")
    
    def restore_backup(self, backup_id: str, target_volume_id: Optional[str] = None) -> Dict:
        """Restore a backup to a volume."""
        if backup_id not in self.backup_jobs:
            raise ClusterStorageError(f"Backup {backup_id} not found")
        
        backup_job = self.backup_jobs[backup_id]
        
        if not target_volume_id:
            target_volume_id = backup_job["volume_id"]
        
        if target_volume_id not in self.volumes:
            raise ClusterStorageError(f"Target volume {target_volume_id} not found")
        
        target_volume = self.volumes[target_volume_id]
        
        if target_volume.attached_to:
            raise ClusterStorageError(f"Target volume {target_volume_id} is attached to VM {target_volume.attached_to}, detach first")
        
        if not target_volume.server_id:
            raise ClusterStorageError(f"Target volume {target_volume_id} has no server assigned")
        
        try:
            restore_cmd = f"echo 'Restoring backup {backup_id} to volume {target_volume.name}'"
            self.server_manager.execute_command(target_volume.server_id, restore_cmd)
            
            restore_job = {
                "id": str(uuid.uuid4())[:8],
                "backup_id": backup_id,
                "target_volume_id": target_volume_id,
                "status": "completed",
                "created_at": time.time(),
                "completed_at": time.time()
            }
            
            logger.info(f"Successfully restored backup {backup_id} to volume {target_volume_id}")
            
            return restore_job
        except Exception as e:
            logger.error(f"Error restoring backup {backup_id} to volume {target_volume_id}: {e}")
            raise ClusterStorageError(f"Failed to restore backup: {str(e)}")
    
    def list_volumes(self) -> List[Dict]:
        """List all volumes."""
        return [volume.to_dict() for volume in self.volumes.values()]
    
    def get_volume(self, volume_id: str) -> Dict:
        """Get a volume by ID."""
        if volume_id not in self.volumes:
            raise ClusterStorageError(f"Volume {volume_id} not found")
        
        return self.volumes[volume_id].to_dict()
    
    def list_backups(self, volume_id: Optional[str] = None) -> List[Dict]:
        """List all backups, optionally filtered by volume ID."""
        if volume_id:
            return [backup for backup in self.backup_jobs.values() if backup["volume_id"] == volume_id]
        else:
            return list(self.backup_jobs.values())
    
    def setup_distributed_storage(self) -> None:
        """Set up distributed storage across all servers."""
        online_servers = [s for s in self.server_manager.list_servers() if s.status == "online"]
        if len(online_servers) < 2:
            logger.info("Not enough online servers to set up distributed storage")
            return
        
        logger.info(f"Setting up distributed storage across {len(online_servers)} servers")
        
        for server in online_servers:
            try:
                setup_cmd = f"echo 'Setting up distributed storage on server {server.name}'"
                self.server_manager.execute_command(server.id, setup_cmd)
            except Exception as e:
                logger.error(f"Error setting up distributed storage on server {server.id}: {e}")
    
    def get_storage_metrics(self) -> Dict:
        """Get storage metrics across all servers."""
        metrics = {
            "total_volumes": len(self.volumes),
            "total_volume_size_gb": sum(v.size_gb for v in self.volumes.values()),
            "attached_volumes": sum(1 for v in self.volumes.values() if v.attached_to),
            "replicated_volumes": sum(1 for v in self.volumes.values() if v.replicated),
            "total_backups": len(self.backup_jobs),
            "server_storage_metrics": []
        }
        
        for server in self.server_manager.list_servers():
            if server.status != "online" or not server.metrics_history:
                continue
            
            latest_metrics = server.metrics_history[-1]
            metrics["server_storage_metrics"].append({
                "server_id": server.id,
                "server_name": server.name,
                "total_disk_gb": server.disk_gb,
                "used_disk_gb": latest_metrics.disk_used,
                "timestamp": latest_metrics.timestamp
            })
        
        return metrics 