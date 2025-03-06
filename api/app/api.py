import os
import json
import logging
import traceback
import ipaddress
from flask import Flask, request, jsonify
from flask_cors import CORS
from dataclasses import asdict
import libvirt
from flask_caching import Cache
import functools
from flask_compress import Compress
from marshmallow import Schema, fields, validate, ValidationError
import platform
import random
import uuid

app = Flask(__name__)
CORS(app)

logger = logging.getLogger(__name__)

# Initialize managers and other components
from app.vm import VMManager, VMConfig
from app.vpc import VPCManager, VPCError
from app.networking import NetworkManager, NetworkError
from app.migration import MigrationManager, MigrationConfig, MigrationError
from app.db import db
from app.libvirt_utils import get_libvirt_connection
from app.ip_manager import IPManager
from app.server_manager import ServerManager, Server
from app.cluster_vm_manager import ClusterVMManager
from app.cluster_network_manager import ClusterNetworkManager
from app.cluster_storage_manager import ClusterStorageManager
from app.cluster_monitoring import ClusterMonitoring
from app.cluster_api import cluster_api, init_cluster_managers

# Initialize libvirt connection
def init_managers():
    global network_manager, vpc_manager, vm_manager, migration_manager, ip_manager
    global server_manager, cluster_vm_manager, cluster_network_manager, cluster_storage_manager, cluster_monitoring
    conn = get_libvirt_connection()
    network_manager = NetworkManager(conn)
    vpc_manager = VPCManager(network_manager)
    ip_manager = IPManager()
    vm_manager = VMManager(network_manager=network_manager, ip_manager=ip_manager)
    migration_manager = MigrationManager(conn)
    
    # Initialize cluster managers
    server_manager = ServerManager()
    cluster_vm_manager = ClusterVMManager(server_manager, vpc_manager)
    cluster_network_manager = ClusterNetworkManager(server_manager, vpc_manager)
    cluster_storage_manager = ClusterStorageManager(server_manager)
    cluster_monitoring = ClusterMonitoring(
        server_manager, 
        cluster_vm_manager,
        cluster_network_manager,
        cluster_storage_manager
    )
    
    # Start monitoring
    try:
        cluster_monitoring.start_monitoring()
    except Exception as e:
        logger.error(f"Failed to start monitoring: {e}")
    
    logger.info("All managers initialized successfully")

try:
    init_managers()
except Exception as e:
    logger.error(f"Failed to initialize managers: {e}")
    raise

# Add error handler for libvirt errors
@app.errorhandler(libvirt.libvirtError)
def handle_libvirt_error(error):
    logger.error(f"Libvirt error: {error}")
    try:
        # Try to reinitialize managers on connection errors
        if error.get_error_code() in [
            libvirt.VIR_ERR_SYSTEM_ERROR,
            libvirt.VIR_ERR_NO_CONNECT,
            libvirt.VIR_ERR_INTERNAL_ERROR
        ]:
            init_managers()
    except Exception as e:
        logger.error(f"Failed to recover from libvirt error: {e}")
    return jsonify({'error': str(error)}), 500

# Add platform check
def check_architecture_compatibility():
    arch = platform.machine().lower()
    is_arm = 'arm' in arch or 'aarch64' in arch
    
    try:
        # Check QEMU/KVM support for current architecture
        conn = get_libvirt_connection()
        capabilities = conn.getCapabilities()
        
        return {
            'architecture': arch,
            'is_arm': is_arm,
            'kvm_available': 'kvm' in capabilities.lower(),
            'qemu_support': True,
            'capabilities': capabilities
        }
    except Exception as e:
        logger.error(f"Error checking architecture compatibility: {e}")
        return {
            'architecture': arch,
            'is_arm': is_arm,
            'error': str(e)
        }

# Add health check endpoint
@app.route('/api/health', methods=['GET'])
def health_check():
    try:
        # Check libvirt connection
        conn = get_libvirt_connection()
        conn.getVersion()
        
        # Check managers
        network_manager.list_networks()
        vpc_manager.list_vpcs()
        
        # Check architecture compatibility
        arch_info = check_architecture_compatibility()
        
        return jsonify({
            'status': 'healthy',
            'libvirt_connection': 'ok',
            'managers': {
                'network': 'ok',
                'vpc': 'ok',
                'vm': 'ok',
                'migration': 'ok'
            },
            'platform': arch_info
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

# Configure cache
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 300
})

def cache_key_prefix():
    """Generate cache key prefix based on request."""
    return f"{request.path}:{request.args}"

# Enable compression
Compress(app)

# Request validation schemas
class CloudInitConfigSchema(Schema):
    hostname = fields.Str()
    users = fields.List(fields.Dict(keys=fields.Str(), values=fields.Raw()))
    ssh_authorized_keys = fields.List(fields.Str())
    packages = fields.List(fields.Str())
    runcmd = fields.List(fields.Str())
    write_files = fields.List(fields.Dict(keys=fields.Str(), values=fields.Raw()))
    timezone = fields.Str()
    ntp = fields.Dict(keys=fields.Str(), values=fields.Raw(), allow_none=True)
    growpart = fields.Dict(keys=fields.Str(), values=fields.Raw(), allow_none=True)
    apt = fields.Dict(keys=fields.Str(), values=fields.Raw(), allow_none=True)

class VMCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    network_name = fields.Str(required=True)
    cpu_cores = fields.Int(required=True, validate=validate.Range(min=1, max=32))
    memory_mb = fields.Int(required=True, validate=validate.Range(min=512, max=262144))
    disk_size_gb = fields.Int(required=True, validate=validate.Range(min=1, max=2048))
    image_id = fields.Str(required=True)
    cloud_init = fields.Nested(CloudInitConfigSchema, allow_none=True)
    arch = fields.Str(validate=validate.OneOf(['x86_64', 'aarch64']), allow_none=True)

class VPCCreateSchema(Schema):
    name = fields.Str(required=True, validate=[
        validate.Length(min=1, max=64),
        validate.Regexp(r'^[a-zA-Z0-9-]+$', error='VPC name can only contain letters, numbers, and hyphens')
    ])
    cidr = fields.Str(validate=[
        validate.Regexp(r'^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$', error='Invalid CIDR format')
    ])

class SubnetCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Regexp(r'^[a-zA-Z0-9-]+$'))
    cidr = fields.Str(required=True, validate=validate.Regexp(r'^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$'))

class DiskCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    size_gb = fields.Int(required=True, validate=validate.Range(min=1, max=2048))

class MigrationCreateSchema(Schema):
    vm_name = fields.Str(required=True)
    destination_uri = fields.Str(required=True)
    migration_type = fields.Str(validate=validate.OneOf(['direct', 'tunneled']))
    bandwidth = fields.Int(validate=validate.Range(min=1))
    max_downtime = fields.Int(validate=validate.Range(min=1))
    compressed = fields.Bool()

class ServerSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    host = fields.Str(required=True)
    port = fields.Int(validate=validate.Range(min=1, max=65535))
    username = fields.Str()
    password = fields.Str()
    key_path = fields.Str()
    vm_capacity = fields.Int(validate=validate.Range(min=1, max=1000))

def validate_request(schema_class):
    """Decorator to validate request data against a schema."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            schema = schema_class()
            try:
                data = request.get_json()
                if not data:
                    return jsonify({'error': 'No JSON data provided', 'details': 'Request body is empty or not valid JSON'}), 400
                validated_data = schema.load(data)
                return f(*args, **kwargs)
            except ValidationError as err:
                error_messages = []
                for field, messages in err.messages.items():
                    if isinstance(messages, list):
                        error_messages.extend([f"{field}: {msg}" for msg in messages])
                    else:
                        error_messages.append(f"{field}: {messages}")
                return jsonify({
                    'error': 'Validation error',
                    'details': error_messages,
                    'schema': schema_class.__name__
                }), 400
            except json.JSONDecodeError as e:
                return jsonify({
                    'error': 'Invalid JSON',
                    'details': str(e)
                }), 400
        return wrapper
    return decorator

# VM Routes
@app.route('/api/vms', methods=['GET'])
@cache.cached(timeout=60, key_prefix=cache_key_prefix)
def list_vms():
    try:
        vms = vm_manager.list_vms()
        return jsonify({'vms': [asdict(vm) for vm in vms]})
    except Exception as e:
        logger.error(f"Error listing VMs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>', methods=['GET'])
def get_vm(vm_id):
    try:
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
        return jsonify({'vm': asdict(vm)})
    except Exception as e:
        logger.error(f"Error getting VM: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms', methods=['POST'])
@validate_request(VMCreateSchema)
def create_vm():
    try:
        data = request.json
        
        # Check architecture compatibility if specified
        if 'arch' in data:
            current_arch = platform.machine().lower()
            requested_arch = data['arch'].lower()
            if 'arm' in current_arch or 'aarch64' in current_arch:
                if requested_arch not in ['arm64', 'aarch64']:
                    return jsonify({'error': 'Requested architecture not supported on this platform'}), 400
            elif requested_arch not in ['x86_64', 'amd64']:
                return jsonify({'error': 'Requested architecture not supported on this platform'}), 400
        
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
        
        existing_vm = vm_manager.get_vm(config.name)
        if existing_vm:
            return jsonify({'error': f"VM with name {config.name} already exists"}), 400
            
        vm = vm_manager.create_vm(config)
        return jsonify({'vm': asdict(vm)}), 201
    except Exception as e:
        logger.error(f"Error creating VM: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>', methods=['DELETE'])
def delete_vm(vm_id):
    try:
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
            
        vm_manager.delete_vm(vm_id)
        return jsonify({'success': True, 'message': f"VM {vm_id} deleted successfully"})
    except Exception as e:
        logger.error(f"Error deleting VM: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/resize', methods=['POST'])
def resize_vm(vm_id):
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
        
        if 'cpu_cores' in data:
            if not isinstance(data['cpu_cores'], int) or data['cpu_cores'] < 1:
                return jsonify({'error': 'Invalid CPU cores value'}), 400
            vm_manager.resize_cpu(vm, data['cpu_cores'])
            
        if 'memory_mb' in data:
            if not isinstance(data['memory_mb'], int) or data['memory_mb'] < 512:
                return jsonify({'error': 'Invalid memory value'}), 400
            vm_manager.resize_memory(vm, data['memory_mb'])
        
        return jsonify({'success': True, 'message': f"VM {vm_id} resized successfully"})
    except Exception as e:
        logger.error(f"Error resizing VM: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/status', methods=['GET'])
def get_vm_status(vm_id):
    try:
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
            
        status = vm_manager.get_vm_status(vm_id)
        return jsonify({'status': status})
    except Exception as e:
        logger.error(f"Error getting VM status: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/metrics', methods=['GET'])
@cache.cached(timeout=30, key_prefix=cache_key_prefix)
def get_vm_metrics(vm_id):
    try:
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
        
        metrics = vm_manager.get_metrics(vm)
        return jsonify({'metrics': metrics})
    except Exception as e:
        logger.error(f"Error getting VM metrics: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# VPC Routes
@app.route('/api/vpcs', methods=['GET'])
@cache.cached(timeout=60, key_prefix=cache_key_prefix)
def list_vpcs():
    try:
        vpcs = vpc_manager.list_vpcs()
        return jsonify({'vpcs': [vpc.to_dict() for vpc in vpcs]})
    except Exception as e:
        logger.error(f"Error listing VPCs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

def is_private_cidr(cidr: str) -> bool:
    """Check if a CIDR block is within private network ranges."""
    try:
        network = ipaddress.ip_network(cidr)
        return (
            network.is_private and
            network.prefixlen >= 16 and
            network.prefixlen <= 28
        )
    except ValueError:
        return False

def generate_random_cidr() -> str:
    """Generate a random private network CIDR."""
    private_ranges = [
        '10.0.0.0/8',
        '172.16.0.0/12',
        '192.168.0.0/16'
    ]
    base_network = ipaddress.ip_network(random.choice(private_ranges))
    # Generate a random subnet within the base network
    prefix_length = random.randint(16, 28)
    subnets = list(base_network.subnets(new_prefix=prefix_length))
    return str(random.choice(subnets))

@app.route('/api/vpcs', methods=['POST'])
@validate_request(VPCCreateSchema)
def create_vpc():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Validate required fields
        if 'name' not in data:
            return jsonify({'error': 'VPC name is required'}), 400

        name = data['name']
        cidr = data.get('cidr')
        
        # If no CIDR provided, generate a random one
        if not cidr:
            cidr = generate_random_cidr()
        # If CIDR provided, validate it's a private network
        elif not is_private_cidr(cidr):
            return jsonify({
                'error': 'Invalid CIDR range',
                'details': 'CIDR must be a private network range with prefix length between /16 and /28'
            }), 400

        # Check for existing VPC
        existing_vpc = vpc_manager.get_vpc(name)
        if existing_vpc:
            return jsonify({'error': f'VPC {name} already exists'}), 400

        # Create VPC
        vpc = vpc_manager.create_vpc(name, cidr)
        return jsonify({'vpc': vpc.to_dict()}), 201
    except (VPCError, NetworkError) as e:
        logger.error(f"Error creating VPC: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating VPC: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs/<vpc_name>', methods=['GET'])
def get_vpc(vpc_name):
    try:
        vpc = vpc_manager.get_vpc(vpc_name)
        if not vpc:
            return jsonify({'error': 'VPC not found'}), 404
        return jsonify({'vpc': vpc.to_dict()})
    except Exception as e:
        logger.error(f"Error getting VPC: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs/<vpc_name>', methods=['DELETE'])
def delete_vpc(vpc_name):
    try:
        success = vpc_manager.delete_vpc(vpc_name)
        if not success:
            return jsonify({'error': 'VPC not found'}), 404
        return jsonify({'success': True, 'message': f"VPC {vpc_name} deleted successfully"})
    except (VPCError, NetworkError) as e:
        logger.error(f"Error deleting VPC: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error deleting VPC: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs/<vpc_name>/subnets', methods=['POST'])
@validate_request(SubnetCreateSchema)
def add_subnet(vpc_name):
    try:
        data = request.json
        subnet_name = data['name']
        cidr = data['cidr']
        
        success = vpc_manager.add_subnet(vpc_name, subnet_name, cidr)
        if not success:
            return jsonify({'error': 'Failed to add subnet'}), 400
        
        vpc = vpc_manager.get_vpc(vpc_name)
        return jsonify({'vpc': vpc.to_dict()}), 201
    except (VPCError, NetworkError) as e:
        logger.error(f"Error adding subnet: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error adding subnet: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs/<vpc_name>/subnets/<subnet_name>', methods=['DELETE'])
def remove_subnet(vpc_name, subnet_name):
    try:
        success = vpc_manager.remove_subnet(vpc_name, subnet_name)
        if not success:
            return jsonify({'error': 'Subnet not found'}), 404
        return jsonify({'success': True, 'message': f"Subnet {subnet_name} removed from VPC {vpc_name}"})
    except (VPCError, NetworkError) as e:
        logger.error(f"Error removing subnet: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error removing subnet: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Disk Routes
@app.route('/api/disks', methods=['GET'])
@cache.cached(timeout=60, key_prefix=cache_key_prefix)
def list_disks():
    try:
        disks = vm_manager.list_disks()
        return jsonify({'disks': disks})
    except Exception as e:
        logger.error(f"Error listing disks: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/disks/create', methods=['POST'])
@validate_request(DiskCreateSchema)
def create_disk():
    try:
        data = request.json
        disk = vm_manager.create_disk(data['name'], data['size_gb'])
        return jsonify({'disk': disk})
    except Exception as e:
        logger.error(f"Error creating disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/disks/attach', methods=['POST'])
def attach_disk_to_vm(vm_id):
    try:
        data = request.json
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
            
        vm_manager.attach_disk(data['disk_id'], vm_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error attaching disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/disks/detach', methods=['POST'])
def detach_disk_from_vm(vm_id):
    try:
        data = request.json
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
            
        vm_manager.detach_disk(data['disk_id'])
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error detaching disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Image Routes
@app.route('/api/images', methods=['GET'])
@cache.cached(timeout=300, key_prefix=cache_key_prefix)
def list_images():
    try:
        images = vm_manager.libvirt_manager.list_images()
        return jsonify({'images': images})
    except Exception as e:
        logger.error(f"Error listing images: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Migration Routes
@app.route('/api/migrations', methods=['GET'])
def list_migrations():
    try:
        migrations = migration_manager.list_migrations()
        return jsonify({'migrations': migrations})
    except Exception as e:
        logger.error(f"Error listing migrations: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/migrations', methods=['POST'])
@validate_request(MigrationCreateSchema)
def start_migration():
    try:
        data = request.json
        config = MigrationConfig(
            vm_name=data['vm_name'],
            destination_uri=data['destination_uri'],
            migration_type=MigrationType(data.get('migration_type', 'direct')),
            bandwidth=data.get('bandwidth'),
            max_downtime=data.get('max_downtime'),
            compressed=data.get('compressed', True)
        )
        
        migration_manager.start_migration(config)
        return jsonify({
            'success': True, 
            'message': f"Started {config.migration_type.value} migration of VM {config.vm_name}"
        }), 201
    except MigrationError as e:
        logger.error(f"Migration error: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error starting migration: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/migrations/<vm_name>/status', methods=['GET'])
def get_migration_status(vm_name):
    try:
        status = migration_manager.get_migration_status(vm_name)
        if status is None:
            return jsonify({'error': 'No migration found for VM'}), 404
            
        return jsonify({
            'status': status.status.value,
            'progress': status.progress,
            'data_total': status.data_total,
            'data_processed': status.data_processed,
            'data_remaining': status.data_remaining,
            'speed': status.speed
        })
    except MigrationError as e:
        logger.error(f"Migration error: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error getting migration status: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/migrations/<vm_name>', methods=['DELETE'])
def cancel_migration(vm_name):
    try:
        migration_manager.cancel_migration(vm_name)
        return jsonify({'success': True, 'message': f"Cancelled migration of VM {vm_name}"})
    except MigrationError as e:
        logger.error(f"Migration error: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error cancelling migration: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Add cache invalidation for write operations
def invalidate_cache(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        # Invalidate relevant caches after write operations
        cache.delete_memoized(list_vms)
        cache.delete_memoized(list_vpcs)
        cache.delete_memoized(list_disks)
        return result
    return wrapper

# Apply cache invalidation to write operations
create_vm = invalidate_cache(create_vm)
create_vpc = invalidate_cache(create_vpc)
start_migration = invalidate_cache(start_migration)

# Add IP pool metrics endpoint
@app.route('/api/ip-pool/metrics', methods=['GET'])
@cache.cached(timeout=30, key_prefix=cache_key_prefix)
def get_ip_pool_metrics():
    """Get metrics about the IP pool."""
    try:
        metrics = ip_manager.get_pool_metrics()
        return jsonify({'metrics': metrics})
    except Exception as e:
        logger.error(f"Error getting IP pool metrics: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Server management endpoints
@app.route('/api/servers', methods=['GET'])
@cache.cached(timeout=30, key_prefix=cache_key_prefix)
def list_servers():
    """List all servers."""
    try:
        servers = server_manager.list_servers()
        result = [server.to_dict() for server in servers]
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error listing servers: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/servers/<server_id>', methods=['GET'])
def get_server(server_id):
    """Get a server by ID."""
    try:
        server = server_manager.get_server(server_id)
        return jsonify(server.to_dict())
    except Exception as e:
        logger.error(f"Error getting server {server_id}: {e}")
        return jsonify({"error": str(e)}), 404

@app.route('/api/servers', methods=['POST'])
@validate_request(ServerSchema)
def add_server():
    """Add a new server."""
    try:
        data = request.json
        
        # Generate a unique ID for the server
        server_id = str(uuid.uuid4())[:8]
        
        # Create server object
        server = Server(
            id=server_id,
            name=data['name'],
            host=data['host'],
            port=data.get('port', 22),
            username=data.get('username', 'ubuntu'),
            password=data.get('password'),
            key_path=data.get('key_path'),
            vm_capacity=data.get('vm_capacity', 10)
        )
        
        # Add server to manager
        server_manager.add_server(server)
        
        return jsonify(server.to_dict()), 201
    except Exception as e:
        logger.error(f"Error adding server: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/servers/<server_id>', methods=['DELETE'])
def remove_server(server_id):
    """Remove a server."""
    try:
        server_manager.remove_server(server_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error removing server {server_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/servers/<server_id>/status', methods=['GET'])
def get_server_status(server_id):
    """Get server status."""
    try:
        server_manager.update_server_status(server_id)
        server = server_manager.get_server(server_id)
        
        # Return server with metrics
        result = server.to_dict()
        if server.metrics_history:
            latest_metrics = server.metrics_history[-1]
            result['metrics'] = {
                'cpu_usage': latest_metrics.cpu_usage,
                'memory_total': latest_metrics.memory_total,
                'memory_used': latest_metrics.memory_used,
                'disk_total': latest_metrics.disk_total,
                'disk_used': latest_metrics.disk_used,
                'network_rx': latest_metrics.network_rx,
                'network_tx': latest_metrics.network_tx,
                'timestamp': latest_metrics.timestamp
            }
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting server status {server_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/servers/<server_id>/command', methods=['POST'])
def execute_command(server_id):
    """Execute a command on a server."""
    try:
        data = request.json
        command = data.get('command')
        
        if not command:
            return jsonify({"error": "No command provided"}), 400
        
        result = server_manager.execute_command(server_id, command)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error executing command on server {server_id}: {e}")
        return jsonify({"error": str(e)}), 500

# Cluster VM endpoints

@app.route('/api/cluster/vms', methods=['GET'])
@cache.cached(timeout=30, key_prefix=cache_key_prefix)
def list_cluster_vms():
    """List all VMs across all servers."""
    try:
        vms = cluster_vm_manager.list_vms()
        result = [vm.to_dict() for vm in vms]
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error listing cluster VMs: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cluster/vms/<vm_id>', methods=['GET'])
def get_cluster_vm(vm_id):
    """Get a VM by ID from any server."""
    try:
        vm = cluster_vm_manager.get_vm(vm_id)
        return jsonify(vm.to_dict())
    except Exception as e:
        logger.error(f"Error getting cluster VM {vm_id}: {e}")
        return jsonify({"error": str(e)}), 404

@app.route('/api/cluster/vms', methods=['POST'])
@validate_request(VMCreateSchema)
def create_cluster_vm():
    """Create a new VM on the most suitable server."""
    try:
        data = request.json
        
        # Convert to VMConfig
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
        
        # Create VM
        vm = cluster_vm_manager.create_vm(config)
        
        return jsonify(vm.to_dict()), 201
    except Exception as e:
        logger.error(f"Error creating cluster VM: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cluster/vms/<vm_id>', methods=['DELETE'])
def delete_cluster_vm(vm_id):
    """Delete a VM from any server."""
    try:
        cluster_vm_manager.delete_vm(vm_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting cluster VM {vm_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cluster/vms/<vm_id>/migrate', methods=['POST'])
def migrate_vm(vm_id):
    """Migrate a VM to another server."""
    try:
        data = request.json
        destination_server_id = data.get('destination_server_id')
        
        if not destination_server_id:
            return jsonify({"error": "No destination server ID provided"}), 400
        
        live = data.get('live', True)
        
        cluster_vm_manager.migrate_vm(vm_id, destination_server_id, live)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error migrating VM {vm_id}: {e}")
        return jsonify({"error": str(e)}), 500

# Cluster Storage endpoints

@app.route('/api/cluster/volumes', methods=['GET'])
@cache.cached(timeout=60, key_prefix=cache_key_prefix)
def list_cluster_volumes():
    """List all storage volumes across all servers."""
    try:
        volumes = cluster_storage_manager.list_volumes()
        return jsonify(volumes)
    except Exception as e:
        logger.error(f"Error listing cluster volumes: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cluster/volumes/<volume_id>', methods=['GET'])
def get_cluster_volume(volume_id):
    """Get a storage volume by ID."""
    try:
        volume = cluster_storage_manager.get_volume(volume_id)
        return jsonify(volume)
    except Exception as e:
        logger.error(f"Error getting cluster volume {volume_id}: {e}")
        return jsonify({"error": str(e)}), 404

@app.route('/api/cluster/volumes', methods=['POST'])
def create_cluster_volume():
    """Create a new storage volume."""
    try:
        data = request.json
        name = data.get('name')
        size_gb = data.get('size_gb')
        replicated = data.get('replicated', False)
        
        if not name or not size_gb:
            return jsonify({"error": "Name and size_gb are required"}), 400
        
        volume = cluster_storage_manager.create_volume(name, size_gb, replicated)
        return jsonify(volume.to_dict()), 201
    except Exception as e:
        logger.error(f"Error creating cluster volume: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cluster/volumes/<volume_id>', methods=['DELETE'])
def delete_cluster_volume(volume_id):
    """Delete a storage volume."""
    try:
        cluster_storage_manager.delete_volume(volume_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting cluster volume {volume_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cluster/volumes/<volume_id>/attach', methods=['POST'])
def attach_cluster_volume(volume_id):
    """Attach a volume to a VM."""
    try:
        data = request.json
        vm_id = data.get('vm_id')
        vm_server_id = data.get('vm_server_id')
        
        if not vm_id or not vm_server_id:
            return jsonify({"error": "VM ID and server ID are required"}), 400
        
        cluster_storage_manager.attach_volume(volume_id, vm_id, vm_server_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error attaching cluster volume {volume_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cluster/volumes/<volume_id>/detach', methods=['POST'])
def detach_cluster_volume(volume_id):
    """Detach a volume from a VM."""
    try:
        cluster_storage_manager.detach_volume(volume_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error detaching cluster volume {volume_id}: {e}")
        return jsonify({"error": str(e)}), 500

# Monitoring and Logging endpoints

@app.route('/api/monitoring/alerts', methods=['GET'])
def list_alerts():
    """List all active alerts."""
    try:
        include_resolved = request.args.get('include_resolved', 'false').lower() == 'true'
        alerts = cluster_monitoring.list_alerts(include_resolved)
        return jsonify(alerts)
    except Exception as e:
        logger.error(f"Error listing alerts: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/monitoring/alerts/<alert_id>/acknowledge', methods=['POST'])
def acknowledge_alert(alert_id):
    """Acknowledge an alert."""
    try:
        cluster_monitoring.acknowledge_alert(alert_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error acknowledging alert {alert_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/monitoring/alerts/<alert_id>/resolve', methods=['POST'])
def resolve_alert(alert_id):
    """Resolve an alert."""
    try:
        cluster_monitoring.resolve_alert(alert_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error resolving alert {alert_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/monitoring/metrics/<resource_type>', methods=['GET'])
def get_metrics(resource_type):
    """Get metrics for a specific resource type."""
    try:
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')
        
        if start_time:
            start_time = float(start_time)
        if end_time:
            end_time = float(end_time)
        
        metrics = cluster_monitoring.get_metrics(resource_type, start_time, end_time)
        return jsonify(metrics)
    except Exception as e:
        logger.error(f"Error getting metrics for {resource_type}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/monitoring/logs/server/<server_id>', methods=['GET'])
def get_server_logs(server_id):
    """Get logs from a server."""
    try:
        lines = request.args.get('lines', 100)
        logs = cluster_monitoring.get_server_logs(server_id, int(lines))
        return jsonify(logs)
    except Exception as e:
        logger.error(f"Error getting logs for server {server_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/monitoring/logs/vm/<vm_id>', methods=['GET'])
def get_vm_logs(vm_id):
    """Get logs for a VM."""
    try:
        lines = request.args.get('lines', 100)
        logs = cluster_monitoring.get_vm_logs(vm_id, int(lines))
        return jsonify(logs)
    except Exception as e:
        logger.error(f"Error getting logs for VM {vm_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/monitoring/health', methods=['GET'])
def get_cluster_health():
    """Get overall cluster health status."""
    try:
        health = cluster_monitoring.get_cluster_health()
        return jsonify(health)
    except Exception as e:
        logger.error(f"Error getting cluster health: {e}")
        return jsonify({"error": str(e)}), 500

# Register the cluster API blueprint
app.register_blueprint(cluster_api)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 