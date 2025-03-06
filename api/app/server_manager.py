import os
import time
import json
import logging
import subprocess
import paramiko
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

@dataclass
class ServerMetrics:
    cpu_usage: float
    memory_total: int
    memory_used: int
    disk_total: int
    disk_used: int
    network_rx: int
    network_tx: int
    timestamp: float = field(default_factory=time.time)

@dataclass
class Server:
    id: str
    name: str
    host: str
    port: int = 22
    username: str = "ubuntu"
    key_path: Optional[str] = None
    password: Optional[str] = None
    status: str = "unknown"
    vm_capacity: int = 10
    vm_count: int = 0
    cpu_cores: int = 0
    memory_mb: int = 0
    disk_gb: int = 0
    metrics_history: List[ServerMetrics] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict:
        """Convert server to dictionary without sensitive info."""
        result = asdict(self)
        if 'password' in result:
            del result['password']
        return result
    
    def get_libvirt_uri(self) -> str:
        """Get the libvirt URI for connecting to this server."""
        return f"qemu+ssh://{self.username}@{self.host}/system"

class ServerError(Exception):
    """Error related to server operations."""
    pass

class ServerManager:
    """Manager for handling multiple bare metal servers."""
    
    def __init__(self, config_path: str = "data/servers.json"):
        """Initialize the server manager."""
        self.config_path = Path(config_path)
        self.servers: Dict[str, Server] = {}
        self._load_servers()
        
    def _load_servers(self) -> None:
        """Load servers from config file."""
        try:
            if self.config_path.exists():
                with open(self.config_path, "r") as f:
                    server_data = json.load(f)
                
                for server_id, data in server_data.items():
                    metrics_history = []
                    if "metrics_history" in data:
                        for metric in data["metrics_history"]:
                            metrics_history.append(ServerMetrics(**metric))
                        data["metrics_history"] = metrics_history
                    
                    self.servers[server_id] = Server(**data)
            else:
                logger.info(f"No server config found at {self.config_path}")
        except Exception as e:
            logger.error(f"Error loading servers: {e}")
    
    def _save_servers(self) -> None:
        """Save servers to config file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            server_data = {}
            for server_id, server in self.servers.items():
                server_dict = server.to_dict()
                if server_dict.get("metrics_history"):
                    server_dict["metrics_history"] = [asdict(m) for m in server.metrics_history]
                server_data[server_id] = server_dict
            
            with open(self.config_path, "w") as f:
                json.dump(server_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving servers: {e}")
    
    def add_server(self, server: Server) -> None:
        """Add a new server to the manager."""
        if server.id in self.servers:
            raise ServerError(f"Server with ID {server.id} already exists")
        
        try:
            self.test_connection(server)
            server.status = "online"
        except Exception as e:
            logger.warning(f"Could not connect to server {server.name} ({server.host}): {e}")
            server.status = "offline"
        
        if server.status == "online":
            try:
                self._collect_server_specs(server)
            except Exception as e:
                logger.warning(f"Could not collect specs for server {server.name}: {e}")
        
        self.servers[server.id] = server
        self._save_servers()
    
    def remove_server(self, server_id: str) -> None:
        """Remove a server from the manager."""
        if server_id not in self.servers:
            raise ServerError(f"Server with ID {server_id} not found")
        
        del self.servers[server_id]
        self._save_servers()
    
    def get_server(self, server_id: str) -> Server:
        """Get a server by ID."""
        if server_id not in self.servers:
            raise ServerError(f"Server with ID {server_id} not found")
        
        return self.servers[server_id]
    
    def list_servers(self) -> List[Server]:
        """List all servers."""
        return list(self.servers.values())
    
    def test_connection(self, server: Server) -> bool:
        """Test SSH connection to a server."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username,
                "timeout": 10
            }
            
            if server.key_path:
                connect_kwargs["key_filename"] = server.key_path
            elif server.password:
                connect_kwargs["password"] = server.password
            
            ssh.connect(**connect_kwargs)
            ssh.close()
            return True
        except Exception as e:
            logger.error(f"Connection test to server {server.name} failed: {e}")
            raise ServerError(f"Could not connect to server: {str(e)}")
    
    def _collect_server_specs(self, server: Server) -> None:
        """Collect server specifications."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username,
                "timeout": 10
            }
            
            if server.key_path:
                connect_kwargs["key_filename"] = server.key_path
            elif server.password:
                connect_kwargs["password"] = server.password
            
            ssh.connect(**connect_kwargs)
            
            _, stdout, _ = ssh.exec_command("grep -c processor /proc/cpuinfo")
            server.cpu_cores = int(stdout.read().decode().strip())
            
            _, stdout, _ = ssh.exec_command("grep MemTotal /proc/meminfo | awk '{print $2}'")
            memory_kb = int(stdout.read().decode().strip())
            server.memory_mb = memory_kb // 1024
            
            _, stdout, _ = ssh.exec_command("df -B1G / | awk '{print $2}' | tail -n 1")
            server.disk_gb = int(stdout.read().decode().strip())
            
            _, stdout, _ = ssh.exec_command("command -v virsh > /dev/null && virsh list --all | grep -v 'Id' | grep -v '^--' | wc -l || echo 0")
            server.vm_count = int(stdout.read().decode().strip())
            
            ssh.close()
        except Exception as e:
            logger.error(f"Error collecting specs for server {server.name}: {e}")
            raise ServerError(f"Could not collect server specs: {str(e)}")
    
    def update_server_status(self, server_id: str) -> None:
        """Update server status and metrics."""
        if server_id not in self.servers:
            raise ServerError(f"Server with ID {server_id} not found")
        
        server = self.servers[server_id]
        
        try:
            self.test_connection(server)
            server.status = "online"
            self._collect_server_metrics(server)
            self._collect_server_specs(server)
        except Exception as e:
            logger.error(f"Error updating server status: {e}")
            server.status = "offline"
        
        server.updated_at = time.time()
        self._save_servers()
    
    def _collect_server_metrics(self, server: Server) -> None:
        """Collect server metrics."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username,
                "timeout": 10
            }
            
            if server.key_path:
                connect_kwargs["key_filename"] = server.key_path
            elif server.password:
                connect_kwargs["password"] = server.password
            
            ssh.connect(**connect_kwargs)
            
            _, stdout, _ = ssh.exec_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2 + $4}'")
            cpu_usage = float(stdout.read().decode().strip())
            
            _, stdout, _ = ssh.exec_command("free -m | awk '/Mem:/ {print $2 \" \" $3}'")
            mem_output = stdout.read().decode().strip().split()
            memory_total = int(mem_output[0])
            memory_used = int(mem_output[1])
            
            _, stdout, _ = ssh.exec_command("df -B1G / | tail -1 | awk '{print $2 \" \" $3}'")
            disk_output = stdout.read().decode().strip().split()
            disk_total = int(disk_output[0])
            disk_used = int(disk_output[1])
            
            _, stdout, _ = ssh.exec_command("cat /proc/net/dev | grep -E 'eth0|ens|eno|enp' | awk '{print $2 \" \" $10}'")
            net_output = stdout.read().decode().strip().split()
            network_rx = int(net_output[0])
            network_tx = int(net_output[1])
            
            ssh.close()
            
            metrics = ServerMetrics(
                cpu_usage=cpu_usage,
                memory_total=memory_total,
                memory_used=memory_used,
                disk_total=disk_total,
                disk_used=disk_used,
                network_rx=network_rx,
                network_tx=network_tx
            )
            
            server.metrics_history.append(metrics)
            
            cutoff_time = time.time() - 86400
            server.metrics_history = [m for m in server.metrics_history if m.timestamp > cutoff_time]
            
        except Exception as e:
            logger.error(f"Error collecting metrics for server {server.name}: {e}")
            raise ServerError(f"Could not collect server metrics: {str(e)}")
    
    def select_server_for_vm(self, cpu_cores: int, memory_mb: int, disk_gb: int) -> Optional[Server]:
        """Select the best server for a new VM based on available resources."""
        for server_id in list(self.servers.keys()):
            try:
                self.update_server_status(server_id)
            except Exception:
                pass
        
        online_servers = [s for s in self.servers.values() if s.status == "online"]
        if not online_servers:
            return None
        
        suitable_servers = []
        for server in online_servers:
            if server.metrics_history:
                latest_metrics = server.metrics_history[-1]
                available_cores = max(0, server.cpu_cores - (server.cpu_cores * latest_metrics.cpu_usage / 100))
                available_memory = max(0, server.memory_mb - latest_metrics.memory_used)
                available_disk = max(0, server.disk_gb - latest_metrics.disk_used)
                
                if (available_cores >= cpu_cores and 
                    available_memory >= memory_mb and 
                    available_disk >= disk_gb and
                    server.vm_count < server.vm_capacity):
                    suitable_servers.append((server, available_cores + available_memory / 1024 + available_disk))
        
        if not suitable_servers:
            return None
        
        suitable_servers.sort(key=lambda x: x[1], reverse=True)
        return suitable_servers[0][0]
    
    def execute_command(self, server_id: str, command: str) -> Dict[str, Any]:
        """Execute a command on a server."""
        if server_id not in self.servers:
            raise ServerError(f"Server with ID {server_id} not found")
        
        server = self.servers[server_id]
        
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username,
                "timeout": 10
            }
            
            if server.key_path:
                connect_kwargs["key_filename"] = server.key_path
            elif server.password:
                connect_kwargs["password"] = server.password
            
            ssh.connect(**connect_kwargs)
            
            _, stdout, stderr = ssh.exec_command(command)
            exit_code = stdout.channel.recv_exit_status()
            
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            
            ssh.close()
            
            return {
                "exit_code": exit_code,
                "output": output,
                "error": error
            }
        except Exception as e:
            logger.error(f"Error executing command on server {server.name}: {e}")
            raise ServerError(f"Could not execute command: {str(e)}")
    
    def copy_file_to_server(self, server_id: str, local_path: str, remote_path: str) -> None:
        """Copy a file to a server."""
        if server_id not in self.servers:
            raise ServerError(f"Server with ID {server_id} not found")
        
        server = self.servers[server_id]
        
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username,
                "timeout": 30
            }
            
            if server.key_path:
                connect_kwargs["key_filename"] = server.key_path
            elif server.password:
                connect_kwargs["password"] = server.password
            
            ssh.connect(**connect_kwargs)
            
            sftp = ssh.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            ssh.close()
        except Exception as e:
            logger.error(f"Error copying file to server {server.name}: {e}")
            raise ServerError(f"Could not copy file: {str(e)}")
    
    def copy_file_from_server(self, server_id: str, remote_path: str, local_path: str) -> None:
        """Copy a file from a server."""
        if server_id not in self.servers:
            raise ServerError(f"Server with ID {server_id} not found")
        
        server = self.servers[server_id]
        
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username,
                "timeout": 30
            }
            
            if server.key_path:
                connect_kwargs["key_filename"] = server.key_path
            elif server.password:
                connect_kwargs["password"] = server.password
            
            ssh.connect(**connect_kwargs)
            
            sftp = ssh.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
            ssh.close()
        except Exception as e:
            logger.error(f"Error copying file from server {server.name}: {e}")
            raise ServerError(f"Could not copy file: {str(e)}") 