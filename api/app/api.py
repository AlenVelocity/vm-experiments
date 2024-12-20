import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, disconnect
import subprocess
import json
import pty
import os
import select
import termios
import struct
import fcntl
import signal
from pathlib import Path
import sys
import threading
import traceback
import logging
import ipaddress
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add parent directory to Python path to import VPC and VM modules
root_dir = Path(__file__).parent.parent.parent
sys.path.append(str(root_dir))

# Import after adding to path
from vpc import VPCManager
from .vm import LibvirtManager

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

vpc_manager = VPCManager()
vm_manager = LibvirtManager()

# Store active console sessions
console_sessions = {}

class ConsoleSession:
    def __init__(self, vm_name):
        self.vm_name = vm_name
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self.thread = None
        self.running = False

    def start(self, socket_id):
        # Create PTY
        self.master_fd, self.slave_fd = pty.openpty()
        
        # Set terminal size
        term_size = struct.pack('HHHH', 24, 80, 0, 0)
        fcntl.ioctl(self.slave_fd, termios.TIOCSWINSZ, term_size)
        
        # Start VM process
        start_script = vm_manager.vm_dir / f"start-{self.vm_name}.sh"
        if not start_script.exists():
            raise Exception(f"VM {self.vm_name} not found")
            
        self.process = subprocess.Popen(
            [str(start_script)],
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            preexec_fn=os.setsid
        )
        
        # Start read thread
        self.running = True
        self.thread = threading.Thread(target=self._read_output, args=(socket_id,))
        self.thread.daemon = True
        self.thread.start()

    def _read_output(self, socket_id):
        while self.running:
            r, _, _ = select.select([self.master_fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(self.master_fd, 1024).decode()
                    socketio.emit('output', data, room=socket_id)
                except (OSError, IOError):
                    break
                except Exception as e:
                    print(f"Error reading from console: {e}")
                    break

    def write_input(self, data):
        if self.master_fd:
            os.write(self.master_fd, data.encode())

    def stop(self):
        self.running = False
        if self.process:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            self.process = None
        if self.master_fd:
            os.close(self.master_fd)
        if self.slave_fd:
            os.close(self.slave_fd)
        if self.thread:
            self.thread.join()

@socketio.on('connect')
def handle_connect():
    vm_name = request.args.get('vmName')
    if not vm_name:
        disconnect()
        return
    
    try:
        session = ConsoleSession(vm_name)
        console_sessions[request.sid] = session
        session.start(request.sid)
    except Exception as e:
        print(f"Error starting console session: {e}")
        disconnect()

@socketio.on('input')
def handle_input(data):
    session = console_sessions.get(request.sid)
    if session:
        session.write_input(data)

@socketio.on('disconnect')
def handle_disconnect():
    session = console_sessions.pop(request.sid, None)
    if session:
        session.stop()

# VPC Routes
@app.route('/api/vpc/list', methods=['GET'])
def list_vpcs():
    try:
        vpcs = vpc_manager.list_vpcs()
        vpc_data = []
        for vpc_name in vpcs:
            vpc = vpc_manager.get_vpc(vpc_name)
            if vpc:
                vpc_data.append({
                    'name': vpc.name,
                    'cidr': vpc.cidr,
                    'used_private_ips': vpc.used_private_ips,
                    'used_public_ips': vpc.used_public_ips
                })
        return jsonify({'vpcs': vpc_data})
    except Exception as e:
        logger.error(f"Error listing VPCs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpc/create', methods=['POST'])
def create_vpc():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        name = data.get('name')
        cidr = data.get('cidr', '192.168.0.0/16')
        
        logger.info(f"Creating VPC with name: {name}, CIDR: {cidr}")
        
        if not name:
            return jsonify({'error': 'VPC name is required'}), 400

        # Validate CIDR format
        try:
            ipaddress.ip_network(cidr)
        except ValueError as e:
            return jsonify({'error': f'Invalid CIDR format: {str(e)}'}), 400
            
        vpc = vpc_manager.create_vpc(name, cidr)
        logger.info(f"Successfully created VPC: {name}")
        
        return jsonify({
            'vpc': {
                'name': vpc.name,
                'cidr': vpc.cidr,
                'used_private_ips': vpc.used_private_ips,
                'used_public_ips': vpc.used_public_ips
            }
        })
    except Exception as e:
        logger.error(f"Error creating VPC: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpc/<name>', methods=['DELETE'])
def delete_vpc(name):
    try:
        vpc_manager.delete_vpc(name)
        return jsonify({'message': f'VPC {name} deleted successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# VM Routes
@app.route('/api/vms/list', methods=['GET'])
def list_vms():
    try:
        vms_list = []
        for vm_id, vm in vm_manager.vms.items():
            try:
                status = vm_manager.get_vm_status(vm_id)
                vms_list.append({
                    'name': vm.name,
                    'vpc': vm.config.network_name,
                    'status': status['state'],
                    'cpu_cores': status['cpu_cores'],
                    'memory_mb': status['memory_mb'],
                    'network': status['network']
                })
            except Exception as e:
                logger.error(f"Error getting status for VM {vm.name}: {str(e)}")
                vms_list.append({
                    'name': vm.name,
                    'vpc': vm.config.network_name,
                    'status': 'error',
                    'error': str(e)
                })
            
        return jsonify({'vms': vms_list})
    except Exception as e:
        logger.error(f"Error listing VMs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/create', methods=['POST'])
def create_vm():
    try:
        data = request.json
        name = data.get('name')
        vpc_name = data.get('vpc')
        
        if not name or not vpc_name:
            return jsonify({'error': 'VM name and VPC are required'}), 400
        
        # Check if VPC exists first
        vpc = vpc_manager.get_vpc(vpc_name)
        if not vpc:
            return jsonify({'error': f'VPC {vpc_name} does not exist'}), 404
            
        # Check if Ubuntu image exists before downloading
        img_file = vm_manager.vm_dir / "ubuntu-20.04-server-cloudimg-arm64.img"
        if not img_file.exists():
            logger.info("Ubuntu image not found, downloading...")
            try:
                vm_manager.download_ubuntu_iso()
            except requests.exceptions.ConnectionError as e:
                error_msg = "Failed to download Ubuntu image: Connection error. Please check your internet connection and try again."
                logger.error(f"{error_msg} Details: {str(e)}")
                return jsonify({'error': error_msg}), 503
            except Exception as e:
                error_msg = f"Failed to download Ubuntu image: {str(e)}"
                logger.error(error_msg)
                return jsonify({'error': error_msg}), 500
        else:
            logger.info("Ubuntu image already exists, skipping download")
        
        # Create the VM
        vm_manager.create_vm(name, vpc_name)
        
        return jsonify({'message': f'VM {name} created successfully in VPC {vpc_name}'})
    except Exception as e:
        logger.error(f"Error creating VM: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<name>/start', methods=['POST'])
def start_vm(name):
    try:
        logger.info(f"Attempting to start VM: {name}")
        
        # Get the start script path
        start_script = vm_manager.vm_dir / f"start-{name}.sh"
        logger.info(f"Start script path: {start_script}")
        
        if not start_script.exists():
            logger.error(f"Start script not found for VM: {name}")
            return jsonify({'error': f'VM {name} not found'}), 404
        
        # Check if VM is already running
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if f'{name}.qcow2' in line and 'qemu-system-aarch64' in line:
                logger.warning(f"VM {name} is already running")
                return jsonify({'message': f'VM {name} is already running'})
        
        # Make sure script is executable
        start_script.chmod(0o755)
        
        # Start the VM in the background
        logger.info(f"Executing start script for VM: {name}")
        process = subprocess.Popen(
            [str(start_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True  # This ensures the process runs in its own session
        )
        
        # Wait a bit to check if process started successfully
        try:
            return_code = process.wait(timeout=2)
            if return_code != 0:
                stdout, stderr = process.communicate()
                error_msg = stderr.decode() if stderr else stdout.decode()
                logger.error(f"VM start failed with code {return_code}: {error_msg}")
                return jsonify({'error': f'Failed to start VM: {error_msg}'}), 500
        except subprocess.TimeoutExpired:
            # Process is still running after 2 seconds, which is good
            logger.info(f"VM {name} started successfully")
            return jsonify({'message': f'VM {name} started successfully'})
        
    except Exception as e:
        logger.error(f"Error starting VM {name}: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<name>/stop', methods=['POST'])
def stop_vm(name):
    try:
        # Find the VM's process
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if f'{name}.qcow2' in line and 'qemu-system-aarch64' in line:
                pid = line.split()[1]
                subprocess.run(['kill', '-TERM', pid], check=True)
                return jsonify({'message': f'VM {name} stopped successfully'})
        
        return jsonify({'error': f'VM {name} not found or not running'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False) 