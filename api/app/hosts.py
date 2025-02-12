from flask import Blueprint, request, jsonify
from pathlib import Path
import json
import paramiko
from typing import Dict, List, Optional
import subprocess
from datetime import datetime

hosts = Blueprint('hosts', __name__)

class HostError(Exception):
    """Base exception for host-related errors"""
    pass

def get_hosts_metadata() -> Dict:
    """Get metadata for all registered hosts"""
    metadata_file = Path("data/hosts/metadata.json")
    if not metadata_file.exists():
        return {}
    try:
        return json.loads(metadata_file.read_text())
    except json.JSONDecodeError:
        return {}

def save_hosts_metadata(metadata: Dict) -> None:
    """Save hosts metadata"""
    metadata_file = Path("data/hosts/metadata.json")
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.write_text(json.dumps(metadata, indent=2))

def execute_remote_command(host: Dict, command: str) -> str:
    """Execute a command on a remote host via SSH"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(
            hostname=host["address"],
            username=host["username"],
            key_filename=host.get("ssh_key_path"),
            password=host.get("password")
        )
        
        stdin, stdout, stderr = ssh.exec_command(command)
        result = stdout.read().decode()
        error = stderr.read().decode()
        
        if error:
            raise HostError(f"Command failed: {error}")
            
        return result
    finally:
        ssh.close()

def check_host_connection(host: Dict) -> bool:
    """Check if a host is reachable and has required capabilities"""
    try:
        # Test SSH connection
        result = execute_remote_command(host, "which virsh")
        return "virsh" in result
    except Exception:
        return False

@hosts.route('/', methods=['GET'])
def list_hosts():
    """List all registered hosts"""
    try:
        metadata = get_hosts_metadata()
        
        # Update status for each host
        for host_id in metadata:
            metadata[host_id]["is_available"] = check_host_connection(metadata[host_id])
            
        return jsonify(metadata)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@hosts.route('/', methods=['POST'])
def register_host():
    """Register a new host"""
    try:
        data = request.get_json()
        if not data:
            raise HostError("No data provided")

        required = ["name", "address", "username"]
        missing = [field for field in required if field not in data]
        if missing:
            raise HostError(f"Missing required fields: {', '.join(missing)}")

        metadata = get_hosts_metadata()
        if data["name"] in metadata:
            raise HostError(f"Host {data['name']} already exists")

        # Either SSH key or password must be provided
        if not data.get("ssh_key_path") and not data.get("password"):
            raise HostError("Either ssh_key_path or password must be provided")

        # Initialize host metadata
        metadata[data["name"]] = {
            "address": data["address"],
            "username": data["username"],
            "ssh_key_path": data.get("ssh_key_path"),
            "password": data.get("password"),  # Note: In production, encrypt this
            "created_at": datetime.now().isoformat()
        }

        # Test connection
        if not check_host_connection(metadata[data["name"]]):
            raise HostError("Could not connect to host or virsh not available")

        save_hosts_metadata(metadata)
        return jsonify({
            "message": f"Host {data['name']} registered successfully",
            "host": {k: v for k, v in metadata[data["name"]].items() if k != "password"}
        })

    except HostError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@hosts.route('/<host_id>', methods=['GET'])
def get_host(host_id):
    """Get host details"""
    try:
        metadata = get_hosts_metadata()
        if host_id not in metadata:
            return jsonify({"error": "Host not found"}), 404
            
        host_data = metadata[host_id].copy()
        host_data["is_available"] = check_host_connection(metadata[host_id])
        
        # Don't expose password in response
        if "password" in host_data:
            del host_data["password"]
            
        return jsonify(host_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@hosts.route('/<host_id>', methods=['DELETE'])
def unregister_host(host_id):
    """Unregister a host"""
    try:
        metadata = get_hosts_metadata()
        if host_id not in metadata:
            return jsonify({"error": "Host not found"}), 404

        del metadata[host_id]
        save_hosts_metadata(metadata)
        
        return jsonify({
            "message": f"Host {host_id} unregistered successfully"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500 