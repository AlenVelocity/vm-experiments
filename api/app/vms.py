from flask import Blueprint, request, jsonify
from pathlib import Path
import json
from datetime import datetime
import subprocess
import shutil
import os
from typing import Dict, Optional

vms = Blueprint('vms', __name__)

class VMError(Exception):
    """Base exception for VM-related errors"""
    pass

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
    metadata_file.write_text(json.dumps(metadata, indent=2))

def get_vm_status(vm_id: str) -> Optional[str]:
    """Get current status of a VM"""
    try:
        result = subprocess.run(
            ["virsh", "domstate", vm_id],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

@vms.route('/', methods=['GET'])
def list_vms():
    """List all VMs"""
    try:
        metadata = get_vms_metadata()
        
        # Update status for each VM
        for vm_id in metadata:
            current_status = get_vm_status(vm_id)
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

        # Initialize VM metadata
        metadata[data["name"]] = {
            "network": data["network"],
            "cpu": data["cpu"],
            "memory": data["memory"],
            "disk": data["disk"],
            "status": "creating",
            "created_at": datetime.now().isoformat()
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
        current_status = get_vm_status(vm_id)
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
            
        current_status = get_vm_status(vm_id)
        if current_status == "running":
            return jsonify({"error": "VM is already running"}), 400
            
        # Start the VM
        subprocess.run(
            ["virsh", "start", vm_id],
            check=True
        )
        
        # Update metadata
        metadata[vm_id]["status"] = "running"
        metadata[vm_id]["started_at"] = datetime.now().isoformat()
        save_vms_metadata(metadata)
        
        return jsonify({
            "message": f"VM {vm_id} started successfully",
            "status": "running"
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Failed to start VM: {e.stderr}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@vms.route('/<vm_id>/stop', methods=['POST'])
def stop_vm(vm_id):
    """Stop a VM"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        current_status = get_vm_status(vm_id)
        if current_status != "running":
            return jsonify({"error": "VM is not running"}), 400
            
        # Stop the VM
        subprocess.run(
            ["virsh", "shutdown", vm_id],
            check=True
        )
        
        # Update metadata
        metadata[vm_id]["status"] = "shutting down"
        metadata[vm_id]["stopped_at"] = datetime.now().isoformat()
        save_vms_metadata(metadata)
        
        return jsonify({
            "message": f"VM {vm_id} is shutting down",
            "status": "shutting down"
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Failed to stop VM: {e.stderr}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@vms.route('/<vm_id>/force-stop', methods=['POST'])
def force_stop_vm(vm_id):
    """Force stop a VM"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        current_status = get_vm_status(vm_id)
        if current_status != "running":
            return jsonify({"error": "VM is not running"}), 400
            
        # Force stop the VM
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
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Failed to force stop VM: {e.stderr}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@vms.route('/<vm_id>/delete', methods=['DELETE'])
def delete_vm(vm_id):
    """Delete a VM"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        current_status = get_vm_status(vm_id)
        if current_status == "running":
            return jsonify({"error": "Cannot delete running VM"}), 400
            
        # Delete the VM
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
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Failed to delete VM: {e.stderr}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@vms.route('/<vm_id>/console', methods=['GET'])
def get_console_url(vm_id):
    """Get VM console URL"""
    try:
        metadata = get_vms_metadata()
        if vm_id not in metadata:
            return jsonify({"error": "VM not found"}), 404
            
        current_status = get_vm_status(vm_id)
        if current_status != "running":
            return jsonify({"error": "VM is not running"}), 400
            
        # Get VNC port
        result = subprocess.run(
            ["virsh", "vncdisplay", vm_id],
            capture_output=True,
            text=True,
            check=True
        )
        vnc_port = result.stdout.strip()
        
        # Construct console URL (assuming noVNC is set up)
        console_url = f"/novnc/vnc.html?host={os.uname().nodename}&port={5900 + int(vnc_port[1:])}"
        
        return jsonify({
            "console_url": console_url
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Failed to get console URL: {e.stderr}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500 