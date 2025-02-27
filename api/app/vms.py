from flask import Blueprint, request, jsonify
from pathlib import Path
import json
from datetime import datetime
import subprocess
import shutil
import os
import contextlib
from typing import Dict, Optional, Generator
from .hosts import execute_remote_command, get_hosts_metadata, HostError

vms = Blueprint('vms', __name__)

class VMError(Exception):
    """Base exception for VM-related errors"""
    pass

@contextlib.contextmanager
def managed_subprocess(cmd: list, **kwargs) -> Generator[subprocess.Popen, None, None]:
    """Context manager for subprocess to ensure proper cleanup"""
    process = None
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs
        )
        yield process
    finally:
        if process:
            try:
                process.kill()  # Ensure process is terminated
                process.wait(timeout=5)  # Wait for process to terminate
            except Exception:
                pass  # Best effort cleanup

def get_vms_metadata() -> Dict:
    """Get metadata for all VMs"""
    metadata_file = Path("data/vms/metadata.json")
    if not metadata_file.exists():
        return {}
    try:
        return json.loads(metadata_file.read_text())
    except json.JSONDecodeError:
        return {}

def save_vms_metadata(metadata: Dict) -> None:
    """Save VM metadata"""
    metadata_file = Path("data/vms/metadata.json")
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = metadata_file.with_suffix('.tmp')
    try:
        # Write to temporary file first
        temp_file.write_text(json.dumps(metadata, indent=2))
        # Atomic rename
        temp_file.replace(metadata_file)
    finally:
        # Cleanup temp file if it still exists
        if temp_file.exists():
            temp_file.unlink()

def get_vm_status(vm_id: str, host: Optional[Dict] = None) -> Optional[str]:
    """Get current status of a VM"""
    try:
        command = f"virsh domstate {vm_id}"
        if host:
            result = execute_remote_command(host, command, timeout=5)
            return result.strip()
        else:
            with managed_subprocess(["virsh", "domstate", vm_id], text=True) as process:
                try:
                    stdout, stderr = process.communicate(timeout=5)
                    if process.returncode == 0:
                        return stdout.strip()
                    return "error"
                except subprocess.TimeoutExpired:
                    return "timeout"
    except Exception as e:
        return None

def execute_vm_command(vm_id: str, command: str, host: Optional[Dict] = None, timeout: int = 5) -> str:
    """Execute a virsh command with timeout"""
    try:
        if host:
            result = execute_remote_command(host, f"virsh {command} {vm_id}", timeout=timeout)
            return result.strip()
        else:
            with managed_subprocess(["virsh", command, vm_id], text=True) as process:
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                    if process.returncode != 0:
                        raise VMError(f"Command failed: {stderr.strip()}")
                    return stdout.strip()
                except subprocess.TimeoutExpired:
                    raise VMError(f"Command timed out after {timeout} seconds")
    except Exception as e:
        raise VMError(f"Unexpected error: {str(e)}")

def get_host_for_vm(vm_id: str) -> Optional[Dict]:
    """Get the host metadata for a VM"""
    metadata = get_vms_metadata()
    if vm_id not in metadata or "host" not in metadata[vm_id]:
        return None
        
    hosts_metadata = get_hosts_metadata()
    host_id = metadata[vm_id]["host"]
    return hosts_metadata.get(host_id)

@vms.route('/', methods=['GET'])
def list_vms():
    """List all VMs"""
    try:
        metadata = get_vms_metadata()
        
        # Update status for each VM
        for vm_id in metadata:
            host = get_host_for_vm(vm_id)
            current_status = get_vm_status(vm_id, host)
            if current_status:
                metadata[vm_id]["status"] = current_status
        
        return jsonify(metadata)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@vms.route('/', methods=['POST'])
def create_vm():
    """Create a new VM"""
    try:
        data = request.get_json()
        if not data:
            raise VMError("No data provided")

        required = ["name", "network", "cpu", "memory", "disk"]
        missing = [field for field in required if field not in data]
        if missing:
            raise VMError(f"Missing required fields: {', '.join(missing)}")

        metadata = get_vms_metadata()
        if data["name"] in metadata:
            raise VMError(f"VM {data['name']} already exists")

        # Get host information
        host_id = data.get("host")
        host = None
        if host_id:
            hosts_metadata = get_hosts_metadata()
            if host_id not in hosts_metadata:
                raise VMError(f"Host {host_id} not found")
            host = hosts_metadata[host_id]

        # Initialize VM metadata
        metadata[data["name"]] = {
            "network": data["network"],
            "cpu": data["cpu"],
            "memory": data["memory"],
            "disk": data["disk"],
            "status": "creating",
            "created_at": datetime.now().isoformat(),
            "host": host_id
        }

        # Add optional cloud-init config if provided
        if "cloud_init" in data:
            metadata[data["name"]]["cloud_init"] = data["cloud_init"]

        save_vms_metadata(metadata)
        return jsonify({"message": f"VM {data['name']} created", "vm": metadata[data["name"]]})

    except VMError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@vms.route('/<vm_id>', methods=['GET'])
def get_vm(vm_id):
    """Get VM details"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        # Update current status
        host = get_host_for_vm(vm_id)
        current_status = get_vm_status(vm_id, host)
        if current_status:
            metadata[vm_id]["status"] = current_status
            
        return jsonify(metadata[vm_id])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@vms.route('/<vm_id>/start', methods=['POST'])
def start_vm(vm_id):
    """Start a VM"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404

        host = get_host_for_vm(vm_id)
        current_status = get_vm_status(vm_id, host)
        
        if current_status == "running":
            return jsonify({"message": "VM is already running"}), 200
            
        result = execute_vm_command(vm_id, "start", host)
        metadata[vm_id]["last_status"] = "running"
        metadata[vm_id]["last_updated"] = datetime.now().isoformat()
        save_vms_metadata(metadata)
        
        return jsonify({
            "message": "VM started successfully",
            "status": "running"
        })
    except VMError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@vms.route('/<vm_id>/stop', methods=['POST'])
def stop_vm(vm_id):
    """Stop a VM"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        host = get_host_for_vm(vm_id)
        current_status = get_vm_status(vm_id, host)
        if current_status != "running":
            return jsonify({"error": "VM is not running"}), 400
            
        # Stop the VM
        result = execute_vm_command(vm_id, "shutdown", host)
        
        # Update metadata
        metadata[vm_id]["status"] = "shutting down"
        metadata[vm_id]["stopped_at"] = datetime.now().isoformat()
        save_vms_metadata(metadata)
        
        return jsonify({
            "message": f"VM {vm_id} is shutting down",
            "status": "shutting down"
        })
    except VMError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@vms.route('/<vm_id>/force-stop', methods=['POST'])
def force_stop_vm(vm_id):
    """Force stop a VM"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        host = get_host_for_vm(vm_id)
        current_status = get_vm_status(vm_id, host)
        if current_status != "running":
            return jsonify({"error": "VM is not running"}), 400
            
        # Force stop the VM
        if host:
            execute_remote_command(host, f"virsh destroy {vm_id}")
        else:
            subprocess.run(
                ["virsh", "destroy", vm_id],
                check=True
            )
        
        # Update metadata
        metadata[vm_id]["status"] = "stopped"
        metadata[vm_id]["stopped_at"] = datetime.now().isoformat()
        save_vms_metadata(metadata)
        
        return jsonify({
            "message": f"VM {vm_id} force stopped",
            "status": "stopped"
        })
    except (subprocess.CalledProcessError, HostError) as e:
        return jsonify({"error": f"Failed to force stop VM: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@vms.route('/<vm_id>/delete', methods=['DELETE'])
def delete_vm(vm_id):
    """Delete a VM"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        host = get_host_for_vm(vm_id)
        current_status = get_vm_status(vm_id, host)
        if current_status == "running":
            return jsonify({"error": "Cannot delete running VM"}), 400
            
        # Delete the VM
        if host:
            execute_remote_command(host, f"virsh undefine {vm_id} --remove-all-storage")
        else:
            subprocess.run(
                ["virsh", "undefine", vm_id, "--remove-all-storage"],
                check=True
            )
        
        # Remove from metadata
        del metadata[vm_id]
        save_vms_metadata(metadata)
        
        return jsonify({
            "message": f"VM {vm_id} deleted successfully"
        })
    except (subprocess.CalledProcessError, HostError) as e:
        return jsonify({"error": f"Failed to delete VM: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@vms.route('/<vm_id>/console', methods=['GET'])
def get_console_url(vm_id):
    """Get VM console URL"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        host = get_host_for_vm(vm_id)
        if host:
            # For remote hosts, we need to set up VNC forwarding
            # This is a placeholder - implement proper VNC forwarding
            return jsonify({"error": "Console access for remote VMs not implemented yet"}), 501
            
        # For local VMs, return the console URL
        return jsonify({
            "console_url": f"vnc://localhost:{vm_id}"  # Implement proper URL generation
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500 