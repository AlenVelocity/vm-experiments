import os
import json
import time
import logging
import uuid
from typing import Dict, List, Optional, Any
from flask import Flask, request, jsonify, Blueprint
from marshmallow import Schema, fields, validate, ValidationError

from app.server_manager import ServerManager, Server, ServerError
from app.cluster_vm_manager import ClusterVMManager
from app.cluster_network_manager import ClusterNetworkManager
from app.cluster_storage_manager import ClusterStorageManager
from app.cluster_monitoring import ClusterMonitoring
from app.ip_manager import IPManager
from app.vm import VMConfig
from app.network_manager import NetworkManager, NetworkConfig
from app.storage_manager import StorageManager, VolumeConfig

logger = logging.getLogger(__name__)

cluster_api = Blueprint('cluster_api', __name__)

class ServerCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    host = fields.Str(required=True)
    port = fields.Int(missing=22)
    username = fields.Str(missing="ubuntu")
    key_path = fields.Str(allow_none=True)
    password = fields.Str(allow_none=True)

class VMCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    cpu_cores = fields.Int(required=True, validate=validate.Range(min=1, max=32))
    memory_mb = fields.Int(required=True, validate=validate.Range(min=512, max=262144))
    disk_gb = fields.Int(required=True, validate=validate.Range(min=1, max=2048))
    image = fields.Str(required=True)
    network = fields.Str(required=True)
    server_id = fields.Str(required=True)

class NetworkCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    cidr = fields.Str(required=True, validate=validate.Regexp(r'^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$'))
    server_id = fields.Str(required=True)

class VolumeCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    size_gb = fields.Int(required=True, validate=validate.Range(min=1, max=2048))
    server_id = fields.Str(required=True)

@cluster_api.route('/api/v1/servers', methods=['POST'])
def add_server():
    """Add a new server to the cluster."""
    try:
        data = ServerCreateSchema().load(request.json)
        server = Server(
            id=str(uuid.uuid4()),
            name=data['name'],
            host=data['host'],
            port=data['port'],
            username=data['username'],
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        server_manager = ServerManager()
        if not server_manager.validate_server(server):
            return jsonify({"error": "Server validation failed"}), 400
            
        return jsonify(server.to_dict()), 201
        
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error adding server: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/status', methods=['GET'])
def get_server_status(server_id):
    """Get server status and metrics."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        server_manager = ServerManager()
        if not server_manager.validate_server(server):
            return jsonify({"error": "Server not accessible"}), 404
            
        metrics = server_manager.collect_server_metrics(server)
        
        return jsonify({
            "server": server.to_dict(),
            "metrics": {
                "cpu_usage": metrics.cpu_usage,
                "memory_total": metrics.memory_total,
                "memory_used": metrics.memory_used,
                "disk_total": metrics.disk_total,
                "disk_used": metrics.disk_used,
                "network_rx": metrics.network_rx,
                "network_tx": metrics.network_tx,
                "timestamp": metrics.timestamp
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting server status: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/command', methods=['POST'])
def execute_command(server_id):
    """Execute a command on a server."""
    try:
        data = request.json
        if 'command' not in data:
            return jsonify({"error": "Command is required"}), 400
            
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        server_manager = ServerManager()
        if not server_manager.validate_server(server):
            return jsonify({"error": "Server not accessible"}), 404
            
        result = server_manager.execute_command(server, data['command'])
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Error executing command: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/files', methods=['PUT'])
def upload_file(server_id):
    """Upload a file to a server."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
            
        file = request.files['file']
        remote_path = request.form.get('remote_path')
        if not remote_path:
            return jsonify({"error": "Remote path is required"}), 400
            
        data = json.loads(request.form.get('server', '{}'))
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        server_manager = ServerManager()
        if not server_manager.validate_server(server):
            return jsonify({"error": "Server not accessible"}), 404
            
        local_path = f"/tmp/{file.filename}"
        file.save(local_path)
        
        try:
            server_manager.copy_file_to_server(server, local_path, remote_path)
            return jsonify({"message": "File uploaded successfully"}), 200
        finally:
            os.remove(local_path)
            
    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/files', methods=['GET'])
def download_file(server_id):
    """Download a file from a server."""
    try:
        remote_path = request.args.get('remote_path')
        if not remote_path:
            return jsonify({"error": "Remote path is required"}), 400
            
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        server_manager = ServerManager()
        if not server_manager.validate_server(server):
            return jsonify({"error": "Server not accessible"}), 404
            
        local_path = f"/tmp/{os.path.basename(remote_path)}"
        try:
            server_manager.copy_file_from_server(server, remote_path, local_path)
            return send_file(local_path, as_attachment=True)
        finally:
            os.remove(local_path)
            
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/networks', methods=['GET'])
def list_networks(server_id):
    """List all networks on a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        network_manager = NetworkManager()
        networks = network_manager.list_networks(server)
        
        return jsonify(networks), 200
        
    except Exception as e:
        logger.error(f"Error listing networks: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/networks', methods=['POST'])
def create_network(server_id):
    """Create a new network on a server."""
    try:
        data = request.json
        if not all(k in data for k in ['name', 'cidr']):
            return jsonify({"error": "Missing required fields: name, cidr"}), 400
            
        server = Server(
            id=server_id,
            name=data['server_name'],
            host=data['server_host'],
            port=data.get('server_port', 22),
            username=data.get('server_username', 'ubuntu'),
            key_path=data.get('server_key_path'),
            password=data.get('server_password')
        )
        
        config = NetworkConfig(
            name=data['name'],
            cidr=data['cidr'],
            gateway=data.get('gateway')
        )
        
        network_manager = NetworkManager()
        network = network_manager.create_network(config, server)
        
        return jsonify(network), 201
        
    except Exception as e:
        logger.error(f"Error creating network: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/networks/<network_name>', methods=['GET'])
def get_network(server_id, network_name):
    """Get details of a network on a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        network_manager = NetworkManager()
        network = network_manager.get_network(network_name, server)
        
        return jsonify(network), 200
        
    except Exception as e:
        logger.error(f"Error getting network details: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/networks/<network_name>', methods=['DELETE'])
def delete_network(server_id, network_name):
    """Delete a network from a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        network_manager = NetworkManager()
        network_manager.delete_network(network_name, server)
        
        return jsonify({"message": "Network deleted successfully"}), 200
        
    except Exception as e:
        logger.error(f"Error deleting network: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/networks/<network_name>/nat', methods=['POST'])
def configure_nat(server_id, network_name):
    """Configure NAT for a network on a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        network_manager = NetworkManager()
        network_manager.configure_nat(network_name, server)
        
        return jsonify({"message": "NAT configured successfully"}), 200
        
    except Exception as e:
        logger.error(f"Error configuring NAT: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/networks/<network_name>/metrics', methods=['GET'])
def get_network_metrics(server_id, network_name):
    """Get metrics for a network on a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        network_manager = NetworkManager()
        metrics = network_manager.get_network_metrics(network_name, server)
        
        return jsonify(metrics), 200
        
    except Exception as e:
        logger.error(f"Error getting network metrics: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/networks/<network_name>/interfaces', methods=['POST'])
def attach_interface(server_id, network_name):
    """Attach a network interface to a network on a server."""
    try:
        data = request.json
        if 'interface_name' not in data:
            return jsonify({"error": "Missing required field: interface_name"}), 400
            
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        network_manager = NetworkManager()
        network_manager.attach_interface(network_name, data['interface_name'], server)
        
        return jsonify({"message": "Interface attached successfully"}), 200
        
    except Exception as e:
        logger.error(f"Error attaching interface: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/networks/interfaces/<interface_name>', methods=['DELETE'])
def detach_interface(server_id, interface_name):
    """Detach a network interface from its network on a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        network_manager = NetworkManager()
        network_manager.detach_interface(interface_name, server)
        
        return jsonify({"message": "Interface detached successfully"}), 200
        
    except Exception as e:
        logger.error(f"Error detaching interface: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/volumes', methods=['GET'])
def list_volumes(server_id):
    """List all volumes on a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        storage_manager = StorageManager()
        volumes = storage_manager.list_volumes(server)
        
        return jsonify(volumes), 200
        
    except Exception as e:
        logger.error(f"Error listing volumes: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/volumes', methods=['POST'])
def create_volume(server_id):
    """Create a new volume on a server."""
    try:
        data = request.json
        if not all(k in data for k in ['name', 'size_gb']):
            return jsonify({"error": "Missing required fields: name, size_gb"}), 400
            
        server = Server(
            id=server_id,
            name=data['server_name'],
            host=data['server_host'],
            port=data.get('server_port', 22),
            username=data.get('server_username', 'ubuntu'),
            key_path=data.get('server_key_path'),
            password=data.get('server_password')
        )
        
        config = VolumeConfig(
            name=data['name'],
            size_gb=data['size_gb'],
            path=data.get('path')
        )
        
        storage_manager = StorageManager()
        volume = storage_manager.create_volume(config, server)
        
        return jsonify(volume), 201
        
    except Exception as e:
        logger.error(f"Error creating volume: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/volumes/<path:volume_path>', methods=['GET'])
def get_volume(server_id, volume_path):
    """Get details of a volume on a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        storage_manager = StorageManager()
        volume = storage_manager.get_volume(volume_path, server)
        
        return jsonify(volume), 200
        
    except Exception as e:
        logger.error(f"Error getting volume details: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/volumes/<path:volume_path>', methods=['DELETE'])
def delete_volume(server_id, volume_path):
    """Delete a volume from a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        storage_manager = StorageManager()
        storage_manager.delete_volume(volume_path, server)
        
        return jsonify({"message": "Volume deleted successfully"}), 200
        
    except Exception as e:
        logger.error(f"Error deleting volume: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/volumes/<path:volume_path>/resize', methods=['POST'])
def resize_volume(server_id, volume_path):
    """Resize a volume on a server."""
    try:
        data = request.json
        if 'new_size_gb' not in data:
            return jsonify({"error": "Missing required field: new_size_gb"}), 400
            
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        storage_manager = StorageManager()
        storage_manager.resize_volume(volume_path, data['new_size_gb'], server)
        
        return jsonify({"message": "Volume resized successfully"}), 200
        
    except Exception as e:
        logger.error(f"Error resizing volume: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/volumes/<path:volume_path>/snapshots', methods=['GET'])
def list_snapshots(server_id, volume_path):
    """List all snapshots of a volume on a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        storage_manager = StorageManager()
        snapshots = storage_manager.list_snapshots(volume_path, server)
        
        return jsonify(snapshots), 200
        
    except Exception as e:
        logger.error(f"Error listing snapshots: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/volumes/<path:volume_path>/snapshots', methods=['POST'])
def create_snapshot(server_id, volume_path):
    """Create a snapshot of a volume on a server."""
    try:
        data = request.json
        if 'name' not in data:
            return jsonify({"error": "Missing required field: name"}), 400
            
        server = Server(
            id=server_id,
            name=data['server_name'],
            host=data['server_host'],
            port=data.get('server_port', 22),
            username=data.get('server_username', 'ubuntu'),
            key_path=data.get('server_key_path'),
            password=data.get('server_password')
        )
        
        storage_manager = StorageManager()
        snapshot = storage_manager.create_snapshot(volume_path, data['name'], server)
        
        return jsonify(snapshot), 201
        
    except Exception as e:
        logger.error(f"Error creating snapshot: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/volumes/snapshots/<path:snapshot_path>', methods=['DELETE'])
def delete_snapshot(server_id, snapshot_path):
    """Delete a snapshot from a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        storage_manager = StorageManager()
        storage_manager.delete_snapshot(snapshot_path, server)
        
        return jsonify({"message": "Snapshot deleted successfully"}), 200
        
    except Exception as e:
        logger.error(f"Error deleting snapshot: {str(e)}")
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/v1/servers/<server_id>/storage/metrics', methods=['GET'])
def get_storage_metrics(server_id):
    """Get storage metrics for a server."""
    try:
        data = request.json
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        storage_manager = StorageManager()
        metrics = storage_manager.get_storage_metrics(server)
        
        return jsonify(metrics), 200
        
    except Exception as e:
        logger.error(f"Error getting storage metrics: {str(e)}")
        return jsonify({"error": str(e)}), 500 