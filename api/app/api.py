import os
import json
import logging
import traceback
import ipaddress
from flask import Flask, request, jsonify
from flask_cors import CORS
from dataclasses import asdict
import libvirt

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
conn = libvirt.open('qemu:///system')
if conn is None:
    raise Exception('Failed to connect to QEMU/KVM')

# Initialize managers
network_manager = NetworkManager(conn)
vpc_manager = VPCManager(network_manager)
vm_manager = VMManager(network_manager=network_manager)
migration_manager = MigrationManager(conn)

# VM Routes
@app.route('/api/vms', methods=['GET'])
def list_vms():
    try:
        vms = vm_manager.list_vms()
        return jsonify({'vms': [asdict(vm) for vm in vms]})
    except Exception as e:
        logger.error(f"Error listing VMs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/create', methods=['POST'])
def create_vm():
    try:
        data = request.json
        config = VMConfig(
            name=data['name'],
            network_name=data['network_name'],
            cpu_cores=data['cpu_cores'],
            memory_mb=data['memory_mb'],
            disk_size_gb=data['disk_size_gb'],
            image_id=data['image_id']
        )
        vm = vm_manager.create_vm(config)
        return jsonify({'vm': asdict(vm)})
    except Exception as e:
        logger.error(f"Error creating VM: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/resize', methods=['POST'])
def resize_vm(vm_id):
    try:
        data = request.json
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
        
        if 'cpu_cores' in data:
            vm_manager.resize_cpu(vm, data['cpu_cores'])
        if 'memory_mb' in data:
            vm_manager.resize_memory(vm, data['memory_mb'])
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error resizing VM: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/console', methods=['GET'])
def get_vm_console(vm_id):
    try:
        vm = vm_manager.get_vm(vm_id)
        if not vm:
            return jsonify({'error': 'VM not found'}), 404
        
        console_url = vm_manager.get_console_url(vm)
        return jsonify({'console_url': console_url})
    except Exception as e:
        logger.error(f"Error getting VM console: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/metrics', methods=['GET'])
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
def list_vpcs():
    try:
        vpcs = vpc_manager.list_vpcs()
        return jsonify({'vpcs': [vpc.to_dict() for vpc in vpcs]})
    except Exception as e:
        logger.error(f"Error listing VPCs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs/create', methods=['POST'])
def create_vpc():
    try:
        data = request.json
        name = data['name']
        cidr = data.get('cidr', '192.168.0.0/16')
        
        vpc = vpc_manager.create_vpc(name, cidr)
        return jsonify({'vpc': vpc.to_dict()})
    except (VPCError, NetworkError) as e:
        logger.error(f"Error creating VPC: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating VPC: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs/<name>', methods=['DELETE'])
def delete_vpc(name):
    try:
        success = vpc_manager.delete_vpc(name)
        if not success:
            return jsonify({'error': 'VPC not found'}), 404
        return jsonify({'success': True})
    except (VPCError, NetworkError) as e:
        logger.error(f"Error deleting VPC: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error deleting VPC: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs/<vpc_name>/subnets', methods=['POST'])
def add_subnet(vpc_name):
    try:
        data = request.json
        subnet_name = data['name']
        cidr = data['cidr']
        
        success = vpc_manager.add_subnet(vpc_name, subnet_name, cidr)
        if not success:
            return jsonify({'error': 'Failed to add subnet'}), 400
        
        vpc = vpc_manager.get_vpc(vpc_name)
        return jsonify({'vpc': vpc.to_dict()})
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
        return jsonify({'success': True})
    except (VPCError, NetworkError) as e:
        logger.error(f"Error removing subnet: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error removing subnet: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Disk Routes
@app.route('/api/disks', methods=['GET'])
def list_disks():
    try:
        disks = vm_manager.list_disks()
        return jsonify({'disks': disks})
    except Exception as e:
        logger.error(f"Error listing disks: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/disks/create', methods=['POST'])
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
    """List all active and recent migrations."""
    try:
        migrations = migration_manager.list_migrations()
        return jsonify({'migrations': migrations})
    except Exception as e:
        logger.error(f"Error listing migrations: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/migrations/start', methods=['POST'])
def start_migration():
    """Start a new VM migration."""
    try:
        data = request.json
        config = MigrationConfig(
            vm_name=data['vm_name'],
            destination_uri=data['destination_uri'],
            bandwidth=data.get('bandwidth'),
            max_downtime=data.get('max_downtime'),
            compressed=data.get('compressed', True),
            auto_converge=data.get('auto_converge', True)
        )
        
        migration_manager.start_migration(config)
        return jsonify({'success': True, 'message': f"Started migration of VM {config.vm_name}"})
    except MigrationError as e:
        logger.error(f"Migration error: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error starting migration: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/migrations/<vm_name>/status', methods=['GET'])
def get_migration_status(vm_name):
    """Get status of a VM migration."""
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
            'downtime': status.downtime,
            'speed': status.speed
        })
    except MigrationError as e:
        logger.error(f"Migration error: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error getting migration status: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/migrations/<vm_name>/cancel', methods=['POST'])
def cancel_migration(vm_name):
    """Cancel an ongoing migration."""
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 