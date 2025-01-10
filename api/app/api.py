import eventlet
eventlet.monkey_patch()

import os
import json
import logging
import traceback
import ipaddress
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from dataclasses import asdict

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

logger = logging.getLogger(__name__)

# Initialize managers and other components
from app.vm import VMManager
from app.vpc import VPCManager
from app.db import db

vm_manager = VMManager()
vpc_manager = VPCManager()

# API Routes
@app.route('/api/vms', methods=['GET'])
def list_vms():
    try:
        vms = vm_manager.list_vms()
        return jsonify({'vms': [asdict(vm) for vm in vms]})
    except Exception as e:
        logger.error(f"Error listing VMs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpcs', methods=['GET'])
def list_vpcs():
    try:
        vpcs = vpc_manager.list_vpcs()
        return jsonify({'vpcs': vpcs})
    except Exception as e:
        logger.error(f"Error listing VPCs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/disks', methods=['GET'])
def list_disks():
    try:
        disks = db.list_disks()
        return jsonify({'disks': disks})
    except Exception as e:
        logger.error(f"Error listing disks: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

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
    # Use eventlet's WSGI server
    from eventlet import wsgi
    wsgi.server(eventlet.listen(('0.0.0.0', 5000)), app) 