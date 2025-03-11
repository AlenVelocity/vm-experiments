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
    """Manages VM host servers without maintaining state."""
    
    def __init__(self):
        """Initialize the server manager without state."""
        self.logger = logging.getLogger(__name__)

    def validate_server(self, server: Server) -> bool:
        """Validate server configuration and connectivity."""
        try:
            return self.test_connection(server)
        except Exception as e:
            self.logger.error(f"Server validation failed: {str(e)}")
            return False

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
            self.logger.error(f"Connection test failed: {str(e)}")
            return False

    def collect_server_metrics(self, server: Server) -> ServerMetrics:
        """Collect current server metrics."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username
            }
            
            if server.key_path:
                connect_kwargs["key_filename"] = server.key_path
            elif server.password:
                connect_kwargs["password"] = server.password
            
            ssh.connect(**connect_kwargs)
            
            # Collect CPU usage
            _, stdout, _ = ssh.exec_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'")
            cpu_usage = float(stdout.read().decode().strip())
            
            # Collect memory info
            _, stdout, _ = ssh.exec_command("free -b | grep Mem")
            mem_info = stdout.read().decode().strip().split()
            memory_total = int(mem_info[1])
            memory_used = int(mem_info[2])
            
            # Collect disk info
            _, stdout, _ = ssh.exec_command("df -B1 / | tail -1")
            disk_info = stdout.read().decode().strip().split()
            disk_total = int(disk_info[1])
            disk_used = int(disk_info[2])
            
            # Collect network info
            _, stdout, _ = ssh.exec_command("cat /proc/net/dev | grep eth0")
            net_info = stdout.read().decode().strip().split()
            network_rx = int(net_info[1])
            network_tx = int(net_info[9])
            
            ssh.close()
            
            return ServerMetrics(
                cpu_usage=cpu_usage,
                memory_total=memory_total,
                memory_used=memory_used,
                disk_total=disk_total,
                disk_used=disk_used,
                network_rx=network_rx,
                network_tx=network_tx
            )
            
        except Exception as e:
            self.logger.error(f"Failed to collect metrics: {str(e)}")
            raise ServerError(f"Failed to collect metrics: {str(e)}")

    def execute_command(self, server: Server, command: str) -> Dict[str, Any]:
        """Execute a command on the server."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username
            }
            
            if server.key_path:
                connect_kwargs["key_filename"] = server.key_path
            elif server.password:
                connect_kwargs["password"] = server.password
            
            ssh.connect(**connect_kwargs)
            
            stdin, stdout, stderr = ssh.exec_command(command)
            exit_code = stdout.channel.recv_exit_status()
            
            result = {
                "stdout": stdout.read().decode(),
                "stderr": stderr.read().decode(),
                "exit_code": exit_code
            }
            
            ssh.close()
            return result
            
        except Exception as e:
            self.logger.error(f"Command execution failed: {str(e)}")
            raise ServerError(f"Command execution failed: {str(e)}")

    def copy_file_to_server(self, server: Server, local_path: str, remote_path: str) -> None:
        """Copy a file to the server."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username
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
            self.logger.error(f"File copy failed: {str(e)}")
            raise ServerError(f"File copy failed: {str(e)}")

    def copy_file_from_server(self, server: Server, remote_path: str, local_path: str) -> None:
        """Copy a file from the server."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": server.host,
                "port": server.port,
                "username": server.username
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
            self.logger.error(f"File copy failed: {str(e)}")
            raise ServerError(f"File copy failed: {str(e)}") 