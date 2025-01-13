import os
import json
import logging
import traceback
import ipaddress
from flask import Flask, request, jsonify
from flask_cors import CORS
from dataclasses import asdict

app = Flask(__name__)
CORS(app)

logger = logging.getLogger(__name__)

# Initialize managers and other components
from app.vm import VMManager, VMConfig
from app.vpc import VPCManager
from app.db import db

vm_manager = VMManager()
vpc_manager = VPCManager()

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
        return jsonify({'vpcs': vpcs})
    except Exception as e:
        logger.error(f"Error listing VPCs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs/create', methods=['POST'])
def create_vpc():
    try:
        data = request.json
        vpc = vpc_manager.create_vpc(data['name'], data['cidr'])
        return jsonify({'vpc': vpc})
    except Exception as e:
        logger.error(f"Error creating VPC: {str(e)}")
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 