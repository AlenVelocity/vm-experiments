import os
import time
import uuid
import json
import logging
import subprocess
from typing import Dict, List, Optional, Any
from pathlib import Path

from app.server_manager import ServerManager, Server, ServerError

logger = logging.getLogger(__name__)

class StorageError(Exception):
    """Error related to storage operations."""
    pass

class VolumeConfig:
    """Volume configuration for a server."""
    def __init__(self, name: str, size_gb: int, path: Optional[str] = None):
        self.name = name
        self.size_gb = size_gb
        self.path = path or f"/var/lib/libvirt/images/{name}.qcow2"
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "size_gb": self.size_gb,
            "path": self.path
        }

class StorageManager:
    """
    Manager for handling storage on a single server.
    This class provides a stateless interface for storage operations.
    """
    
    def __init__(self):
        """Initialize the storage manager."""
        self.logger = logging.getLogger(__name__)

    def validate_volume_config(self, config: VolumeConfig) -> bool:
        """Validate volume configuration."""
        try:
            if config.size_gb <= 0:
                return False
            
            if not config.path.endswith('.qcow2'):
                return False
            
            return True
        except Exception:
            return False

    def create_volume(self, config: VolumeConfig, server: Server) -> Dict:
        """Create a new volume on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # Validate volume configuration
            if not self.validate_volume_config(config):
                raise StorageError("Invalid volume configuration")
            
            # Create volume directory if it doesn't exist
            volume_dir = os.path.dirname(config.path)
            cmd = f"mkdir -p {volume_dir}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to create volume directory: {result['stderr']}")
            
            # Create volume
            cmd = f"qemu-img create -f qcow2 {config.path} {config.size_gb}G"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to create volume: {result['stderr']}")
            
            return config.to_dict()
            
        except Exception as e:
            raise StorageError(f"Failed to create volume: {str(e)}")

    def delete_volume(self, volume_path: str, server: Server) -> None:
        """Delete a volume from the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # Delete volume
            cmd = f"rm -f {volume_path}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to delete volume: {result['stderr']}")
            
        except Exception as e:
            raise StorageError(f"Failed to delete volume: {str(e)}")

    def list_volumes(self, server: Server) -> List[Dict]:
        """List all volumes on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # List volumes
            cmd = "find /var/lib/libvirt/images -name '*.qcow2' -type f"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to list volumes: {result['stderr']}")
            
            volumes = []
            for path in result["stdout"].splitlines():
                if path:
                    # Get volume info
                    cmd = f"qemu-img info --output=json {path}"
                    info_result = server_manager.execute_command(server, cmd)
                    if info_result["exit_code"] == 0:
                        info = json.loads(info_result["stdout"])
                        volumes.append({
                            "name": os.path.basename(path).replace('.qcow2', ''),
                            "path": path,
                            "size_gb": info["virtual-size"] // (1024 * 1024 * 1024),
                            "format": info["format"]
                        })
            
            return volumes
            
        except Exception as e:
            raise StorageError(f"Failed to list volumes: {str(e)}")

    def get_volume(self, volume_path: str, server: Server) -> Dict:
        """Get details of a volume on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # Get volume info
            cmd = f"qemu-img info --output=json {volume_path}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to get volume info: {result['stderr']}")
            
            info = json.loads(result["stdout"])
            return {
                "name": os.path.basename(volume_path).replace('.qcow2', ''),
                "path": volume_path,
                "size_gb": info["virtual-size"] // (1024 * 1024 * 1024),
                "format": info["format"],
                "actual_size": info["actual-size"],
                "cluster_size": info["cluster-size"]
            }
            
        except Exception as e:
            raise StorageError(f"Failed to get volume info: {str(e)}")

    def resize_volume(self, volume_path: str, new_size_gb: int, server: Server) -> None:
        """Resize a volume on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # Resize volume
            cmd = f"qemu-img resize {volume_path} {new_size_gb}G"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to resize volume: {result['stderr']}")
            
        except Exception as e:
            raise StorageError(f"Failed to resize volume: {str(e)}")

    def create_snapshot(self, volume_path: str, snapshot_name: str, server: Server) -> Dict:
        """Create a snapshot of a volume on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # Create snapshot
            snapshot_path = f"{os.path.dirname(volume_path)}/{snapshot_name}.qcow2"
            cmd = f"qemu-img create -f qcow2 -b {volume_path} {snapshot_path}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to create snapshot: {result['stderr']}")
            
            return {
                "name": snapshot_name,
                "path": snapshot_path,
                "base_volume": volume_path
            }
            
        except Exception as e:
            raise StorageError(f"Failed to create snapshot: {str(e)}")

    def list_snapshots(self, volume_path: str, server: Server) -> List[Dict]:
        """List all snapshots of a volume on the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # List snapshots
            cmd = f"qemu-img info --backing-chain --output=json {volume_path}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to list snapshots: {result['stderr']}")
            
            snapshots = []
            info = json.loads(result["stdout"])
            while "backing-filename" in info:
                snapshots.append({
                    "name": os.path.basename(info["filename"]).replace('.qcow2', ''),
                    "path": info["filename"],
                    "base_volume": info["backing-filename"]
                })
                info = info["backing-image"]
            
            return snapshots
            
        except Exception as e:
            raise StorageError(f"Failed to list snapshots: {str(e)}")

    def delete_snapshot(self, snapshot_path: str, server: Server) -> None:
        """Delete a snapshot from the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # Delete snapshot
            cmd = f"rm -f {snapshot_path}"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to delete snapshot: {result['stderr']}")
            
        except Exception as e:
            raise StorageError(f"Failed to delete snapshot: {str(e)}")

    def get_storage_metrics(self, server: Server) -> Dict:
        """Get storage metrics for the specified server."""
        try:
            # Validate server access
            server_manager = ServerManager()
            if not server_manager.validate_server(server):
                raise StorageError("Server not accessible")
            
            # Get storage metrics
            cmd = "df -B1 /var/lib/libvirt/images"
            result = server_manager.execute_command(server, cmd)
            if result["exit_code"] != 0:
                raise StorageError(f"Failed to get storage metrics: {result['stderr']}")
            
            # Parse df output
            lines = result["stdout"].splitlines()
            if len(lines) < 2:
                raise StorageError("Invalid df output")
            
            parts = lines[1].split()
            return {
                "total_bytes": int(parts[1]),
                "used_bytes": int(parts[2]),
                "available_bytes": int(parts[3]),
                "use_percent": int(parts[4].rstrip('%'))
            }
            
        except Exception as e:
            raise StorageError(f"Failed to get storage metrics: {str(e)}") 