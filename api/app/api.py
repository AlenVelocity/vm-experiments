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

app = Flask(__name__)
CORS(app)

logger = logging.getLogger(__name__)

# Initialize managers and other components
from app.vm import VMManager, VMConfig
from app.vpc import VPCManager, VPCError
from app.networking import NetworkManager, NetworkError
from app.migration import MigrationManager, MigrationConfig, MigrationError
from app.db import db

# Initialize libvirt connection
def get_libvirt_connection():
    try:
        conn = libvirt.open('qemu:///system')
        if conn is None:
            raise Exception('Failed to connect to QEMU/KVM')
        return conn
    except libvirt.libvirtError as e:
        logger.error(f"Failed to connect to libvirt: {e}")
        raise Exception(f"Failed to connect to libvirt: {e}")

def init_managers():
    global network_manager, vpc_manager, vm_manager, migration_manager
    conn = get_libvirt_connection()
    network_manager = NetworkManager(conn)
    vpc_manager = VPCManager(network_manager)
    vm_manager = VMManager(network_manager=network_manager)
    migration_manager = MigrationManager(conn)

try:
    init_managers()
except Exception as e:
    logger.error(f"Failed to initialize managers: {e}")
    
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
    name = fields.Str(required=True, validate=validate.Regexp(r'^[a-zA-Z0-9-]+$'))
    cidr = fields.Str(validate=validate.Regexp(r'^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$'))

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

def validate_request(schema_class):
    """Decorator to validate request data against a schema."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            schema = schema_class()
            try:
                data = request.get_json()
                if not data:
                    return jsonify({'error': 'No JSON data provided'}), 400
                validated_data = schema.load(data)
                return f(*args, **kwargs)
            except ValidationError as err:
                return jsonify({'error': 'Validation error', 'details': err.messages}), 400
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

@app.route('/api/vpcs', methods=['POST'])
@validate_request(VPCCreateSchema)
def create_vpc():
    try:
        data = request.json
        name = data['name']
        cidr = data.get('cidr', '192.168.0.0/16')
        
        existing_vpc = vpc_manager.get_vpc(name)
        if existing_vpc:
            return jsonify({'error': f'VPC {name} already exists'}), 400
            
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 