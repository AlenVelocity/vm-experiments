import os
import json
import time
import logging
import uuid
from typing import Dict, List, Optional, Any
from flask import Flask, request, jsonify, Blueprint
from marshmallow import Schema, fields, validate, ValidationError

from app.server_manager import ServerManager, Server
from app.cluster_vm_manager import ClusterVMManager
from app.cluster_network_manager import ClusterNetworkManager
from app.cluster_storage_manager import ClusterStorageManager
from app.cluster_monitoring import ClusterMonitoring
from app.ip_manager import IPManager
from app.vm import VMConfig

logger = logging.getLogger(__name__)

cluster_api = Blueprint('cluster_api', __name__)

server_manager = None
ip_manager = None
vm_manager = None
network_manager = None
storage_manager = None
monitoring = None

def init_cluster_managers(app_ip_manager: IPManager):
    """Initialize all cluster managers."""
    global server_manager, ip_manager, vm_manager, network_manager, storage_manager, monitoring
    
    ip_manager = app_ip_manager
    server_manager = ServerManager()
    vm_manager = ClusterVMManager(server_manager, ip_manager)
    network_manager = ClusterNetworkManager(server_manager, ip_manager)
    storage_manager = ClusterStorageManager(server_manager)
    monitoring = ClusterMonitoring(server_manager, vm_manager, network_manager, storage_manager)
    
    monitoring.start_monitoring()
    
    logger.info("Initialized cluster managers")

class ServerCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    host = fields.Str(required=True)
    port = fields.Int(missing=22)
    username = fields.Str(missing="ubuntu")
    key_path = fields.Str(allow_none=True)
    password = fields.Str(allow_none=True)

class MigrationSchema(Schema):
    vm_id = fields.Str(required=True)
    destination_server_id = fields.Str(required=True)
    live = fields.Bool(missing=True)

class ElasticIPSchema(Schema):
    vm_id = fields.Str(required=True)

class VolumeCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    size_gb = fields.Int(required=True, validate=validate.Range(min=1, max=2048))
    replicated = fields.Bool(missing=False)

class VolumeAttachSchema(Schema):
    volume_id = fields.Str(required=True)
    vm_id = fields.Str(required=True)

class BackupCreateSchema(Schema):
    volume_id = fields.Str(required=True)
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))

class RestoreBackupSchema(Schema):
    backup_id = fields.Str(required=True)
    target_volume_id = fields.Str(required=False, allow_none=True)

class OverlayNetworkSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    cidr = fields.Str(required=True, validate=validate.Regexp(r'^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$'))

@cluster_api.route('/api/cluster/servers', methods=['GET'])
def list_servers():
    """List all servers in the cluster."""
    servers = [server.to_dict() for server in server_manager.list_servers()]
    return jsonify(servers)

@cluster_api.route('/api/cluster/servers/<server_id>', methods=['GET'])
def get_server(server_id):
    """Get a server by ID."""
    try:
        server = server_manager.get_server(server_id)
        return jsonify(server.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/servers', methods=['POST'])
def add_server():
    """Add a new server to the cluster."""
    try:
        schema = ServerCreateSchema()
        data = schema.load(request.json)
        
        server_id = str(uuid.uuid4())[:8]
        
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data['port'],
            username=data['username'],
            key_path=data.get('key_path'),
            password=data.get('password')
        )
        
        server_manager.add_server(server)
        
        return jsonify(server.to_dict()), 201
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/servers/<server_id>', methods=['DELETE'])
def remove_server(server_id):
    """Remove a server from the cluster."""
    try:
        server_manager.remove_server(server_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/servers/<server_id>/status', methods=['GET'])
def get_server_status(server_id):
    """Get the status of a server."""
    try:
        server_manager.update_server_status(server_id)
        server = server_manager.get_server(server_id)
        return jsonify({
            "id": server.id,
            "name": server.name,
            "status": server.status,
            "vm_count": server.vm_count,
            "vm_capacity": server.vm_capacity
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/vms', methods=['GET'])
def list_vms():
    """List all VMs across all servers."""
    try:
        vms = vm_manager.list_vms()
        return jsonify([vm.to_dict() for vm in vms])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/vms/<vm_id>', methods=['GET'])
def get_vm(vm_id):
    """Get a VM by ID."""
    try:
        vm = vm_manager.get_vm(vm_id)
        return jsonify(vm.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/vms', methods=['POST'])
def create_vm():
    """Create a new VM on the most suitable server."""
    try:
        data = request.json
        
        config = VMConfig(
            name=data['name'],
            network_name=data['network_name'],
            cpu_cores=data['cpu_cores'],
            memory_mb=data['memory_mb'],
            disk_size_gb=data['disk_size_gb'],
            image_id=data['image_id'],
            cloud_init=data.get('cloud_init'),
            arch=data.get('arch')
        )
        
        vm = vm_manager.create_vm(config)
        
        return jsonify(vm.to_dict()), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/vms/<vm_id>', methods=['DELETE'])
def delete_vm(vm_id):
    """Delete a VM."""
    try:
        vm_manager.delete_vm(vm_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/vms/<vm_id>/status', methods=['GET'])
def get_vm_status(vm_id):
    """Get the status of a VM."""
    try:
        status = vm_manager.get_vm_status(vm_id)
        return jsonify({"id": vm_id, "status": status})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/vms/<vm_id>/metrics', methods=['GET'])
def get_vm_metrics(vm_id):
    """Get metrics for a VM."""
    try:
        metrics = vm_manager.get_vm_metrics(vm_id)
        return jsonify(metrics)
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/vms/<vm_id>/migrate', methods=['POST'])
def migrate_vm(vm_id):
    """Migrate a VM to another server."""
    try:
        schema = MigrationSchema()
        data = schema.load(request.json)
        
        vm_manager.migrate_vm(vm_id, data['destination_server_id'], data['live'])
        
        return jsonify({"success": True})
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/networks', methods=['GET'])
def list_networks():
    """List all overlay networks."""
    try:
        networks = network_manager.list_overlay_networks()
        return jsonify(networks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/networks', methods=['POST'])
def create_network():
    """Create a new overlay network."""
    try:
        schema = OverlayNetworkSchema()
        data = schema.load(request.json)
        
        network = network_manager.create_overlay_network(data['name'], data['cidr'])
        
        return jsonify(network), 201
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/networks/<network_name>', methods=['GET'])
def get_network(network_name):
    """Get an overlay network by name."""
    try:
        network = network_manager.get_overlay_network(network_name)
        return jsonify(network)
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/networks/<network_name>', methods=['DELETE'])
def delete_network(network_name):
    """Delete an overlay network."""
    try:
        network_manager.delete_overlay_network(network_name)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/elastic-ips', methods=['GET'])
def list_elastic_ips():
    """List all elastic IPs."""
    try:
        ips = network_manager.list_elastic_ips()
        return jsonify(ips)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/elastic-ips/allocate', methods=['POST'])
def allocate_elastic_ip():
    """Allocate a new elastic IP."""
    try:
        ip = network_manager.allocate_elastic_ip()
        return jsonify({"ip": ip}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/elastic-ips/<ip>/attach', methods=['POST'])
def attach_elastic_ip(ip):
    """Attach an elastic IP to a VM."""
    try:
        schema = ElasticIPSchema()
        data = schema.load(request.json)
        
        vm = vm_manager.get_vm(data['vm_id'])
        if not vm:
            return jsonify({"error": f"VM {data['vm_id']} not found"}), 404
        
        server_id = vm_manager.vm_servers.get(data['vm_id'])
        if not server_id:
            return jsonify({"error": f"Server for VM {data['vm_id']} not found"}), 404
        
        network_manager.attach_elastic_ip(ip, data['vm_id'], server_id)
        
        return jsonify({"success": True})
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/elastic-ips/<ip>/detach', methods=['POST'])
def detach_elastic_ip(ip):
    """Detach an elastic IP from a VM."""
    try:
        network_manager.detach_elastic_ip(ip)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/volumes', methods=['GET'])
def list_volumes():
    """List all volumes."""
    try:
        volumes = storage_manager.list_volumes()
        return jsonify(volumes)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/volumes', methods=['POST'])
def create_volume():
    """Create a new volume."""
    try:
        schema = VolumeCreateSchema()
        data = schema.load(request.json)
        
        volume = storage_manager.create_volume(data['name'], data['size_gb'], data['replicated'])
        
        return jsonify(volume.to_dict()), 201
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/volumes/<volume_id>', methods=['GET'])
def get_volume(volume_id):
    """Get a volume by ID."""
    try:
        volume = storage_manager.get_volume(volume_id)
        return jsonify(volume)
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/volumes/<volume_id>', methods=['DELETE'])
def delete_volume(volume_id):
    """Delete a volume."""
    try:
        storage_manager.delete_volume(volume_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/volumes/<volume_id>/attach', methods=['POST'])
def attach_volume(volume_id):
    """Attach a volume to a VM."""
    try:
        schema = VolumeAttachSchema()
        data = schema.load(request.json)
        
        vm = vm_manager.get_vm(data['vm_id'])
        if not vm:
            return jsonify({"error": f"VM {data['vm_id']} not found"}), 404
        
        server_id = vm_manager.vm_servers.get(data['vm_id'])
        if not server_id:
            return jsonify({"error": f"Server for VM {data['vm_id']} not found"}), 404
        
        storage_manager.attach_volume(volume_id, data['vm_id'], server_id)
        
        return jsonify({"success": True})
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/volumes/<volume_id>/detach', methods=['POST'])
def detach_volume(volume_id):
    """Detach a volume from a VM."""
    try:
        storage_manager.detach_volume(volume_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/volumes/<volume_id>/resize', methods=['POST'])
def resize_volume(volume_id):
    """Resize a volume."""
    try:
        data = request.json
        new_size_gb = data.get('size_gb')
        
        if not new_size_gb or not isinstance(new_size_gb, int) or new_size_gb < 1:
            return jsonify({"error": "Invalid size_gb parameter"}), 400
        
        storage_manager.resize_volume(volume_id, new_size_gb)
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/backups', methods=['GET'])
def list_backups():
    """List all backups."""
    try:
        volume_id = request.args.get('volume_id')
        backups = storage_manager.list_backups(volume_id)
        return jsonify(backups)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/backups', methods=['POST'])
def create_backup():
    """Create a new backup."""
    try:
        schema = BackupCreateSchema()
        data = schema.load(request.json)
        
        backup = storage_manager.create_backup(data['volume_id'], data['name'])
        
        return jsonify(backup), 201
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/backups/<backup_id>/restore', methods=['POST'])
def restore_backup(backup_id):
    """Restore a backup."""
    try:
        schema = RestoreBackupSchema()
        data = schema.load(request.json)
        
        restore_job = storage_manager.restore_backup(backup_id, data.get('target_volume_id'))
        
        return jsonify(restore_job)
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/monitoring/health', methods=['GET'])
def get_health():
    """Get the health of the cluster."""
    try:
        health = monitoring.get_cluster_health()
        return jsonify(health)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/monitoring/alerts', methods=['GET'])
def list_alerts():
    """List all alerts."""
    try:
        include_resolved = request.args.get('include_resolved', 'false').lower() == 'true'
        alerts = monitoring.list_alerts(include_resolved)
        return jsonify(alerts)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/monitoring/alerts/<alert_id>/acknowledge', methods=['POST'])
def acknowledge_alert(alert_id):
    """Acknowledge an alert."""
    try:
        monitoring.acknowledge_alert(alert_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/monitoring/alerts/<alert_id>/resolve', methods=['POST'])
def resolve_alert(alert_id):
    """Resolve an alert."""
    try:
        monitoring.resolve_alert(alert_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@cluster_api.route('/api/cluster/monitoring/metrics', methods=['GET'])
def get_metrics():
    """Get metrics for a specific resource type."""
    try:
        resource_type = request.args.get('resource_type')
        if not resource_type:
            return jsonify({"error": "resource_type parameter is required"}), 400
        
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')
        
        if start_time:
            start_time = float(start_time)
        if end_time:
            end_time = float(end_time)
        
        metrics = monitoring.get_metrics(resource_type, start_time, end_time)
        return jsonify(metrics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/monitoring/logs/server/<server_id>', methods=['GET'])
def get_server_logs(server_id):
    """Get logs from a server."""
    try:
        lines = request.args.get('lines', 100, type=int)
        logs = monitoring.get_server_logs(server_id, lines)
        return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/monitoring/logs/vm/<vm_id>', methods=['GET'])
def get_vm_logs(vm_id):
    """Get logs for a VM."""
    try:
        lines = request.args.get('lines', 100, type=int)
        logs = monitoring.get_vm_logs(vm_id, lines)
        return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/setup/networking', methods=['POST'])
def setup_cross_server_networking():
    """Set up networking between servers."""
    try:
        network_manager.setup_cross_server_networking()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/setup/nat', methods=['POST'])
def setup_nat():
    """Set up NAT for outbound connections on all servers."""
    try:
        network_manager.configure_nat_for_all_servers()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cluster_api.route('/api/cluster/setup/storage', methods=['POST'])
def setup_distributed_storage():
    """Set up distributed storage across all servers."""
    try:
        storage_manager.setup_distributed_storage()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500 