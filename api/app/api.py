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
from eventlet import wsgi
import eventlet.websocket

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add parent directory to Python path to import VPC and VM modules
root_dir = Path(__file__).parent.parent.parent
sys.path.append(str(root_dir))

# Import after adding to path
from vpc import VPCManager
from .vm import LibvirtManager
from .ip_manager import IPManager
from .firewall import FirewallManager

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', logger=True, engineio_logger=True)

vpc_manager = VPCManager()
vm_manager = LibvirtManager()
ip_manager = IPManager()
firewall_manager = FirewallManager()

# Store active console sessions
console_sessions = {}

class ConsoleSession:
    def __init__(self, vm_name):
        self.vm_name = vm_name
        self.domain = None
        self.stream = None
        self.thread = None
        self.running = False

    def start(self, socket_id):
        try:
            # Find the VM domain
            vm = None
            for vm_id, v in vm_manager.vms.items():
                if v.name == self.vm_name:
                    vm = v
                    break
            
            if not vm:
                raise Exception(f"VM {self.vm_name} not found")
            
            # Get the domain
            self.domain = vm_manager.conn.lookupByName(self.vm_name)
            if not self.domain:
                raise Exception(f"Domain {self.vm_name} not found")
            
            # Check if VM is running
            if not self.domain.isActive():
                raise Exception(f"VM {self.vm_name} is not running")
            
            # Open console stream
            self.stream = self.domain.openConsole(None, 0)
            if not self.stream:
                raise Exception("Failed to open console stream")
            
            # Start read thread
            self.running = True
            self.thread = threading.Thread(target=self._read_output, args=(socket_id,))
            self.thread.daemon = True
            self.thread.start()
            
            logger.info(f"Console session started for VM {self.vm_name}")
            
        except Exception as e:
            logger.error(f"Error starting console session: {str(e)}")
            self.stop()
            raise

    def _read_output(self, socket_id):
        buffer = bytearray()
        try:
            while self.running and self.stream:
                try:
                    data = self.stream.recv(1024)
                    if not data:
                        break
                    
                    buffer.extend(data)
                    
                    # Try to decode complete UTF-8 sequences
                    while buffer:
                        try:
                            text = buffer.decode('utf-8')
                            socketio.emit('console.output', {'text': text}, room=socket_id)
                            buffer.clear()
                            break
                        except UnicodeDecodeError:
                            # If we can't decode, wait for more data
                            if len(buffer) > 8192:  # Prevent buffer from growing too large
                                buffer.clear()
                            break
                            
                except Exception as e:
                    logger.error(f"Error reading console data: {str(e)}")
                    break
                    
        except Exception as e:
            logger.error(f"Error in console read thread: {str(e)}")
        finally:
            self.stop()
            socketio.emit('console.disconnected', {'reason': 'Console stream ended'}, room=socket_id)

    def write_input(self, data):
        try:
            if self.stream and self.running:
                self.stream.send(data.encode())
        except Exception as e:
            logger.error(f"Error writing to console: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False
        if self.stream:
            try:
                self.stream.finish()
            except:
                pass
            self.stream = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.domain = None

@socketio.on('connect')
def handle_connect():
    try:
        vm_name = request.args.get('vmName')
        if not vm_name:
            logger.error("No VM name provided in connection request")
            return False
        
        logger.info(f"Client connecting for VM: {vm_name}")
        return True
    except Exception as e:
        logger.error(f"Error in connect handler: {str(e)}")
        return False

@socketio.on('console.connect')
def handle_console_connect(data):
    try:
        vm_name = data.get('vmName')
        if not vm_name:
            raise Exception("VM name is required")
        
        logger.info(f"Starting console session for VM: {vm_name}")
        
        # Check if there's an existing session
        old_session = console_sessions.get(request.sid)
        if old_session:
            old_session.stop()
        
        session = ConsoleSession(vm_name)
        console_sessions[request.sid] = session
        session.start(request.sid)
        
        emit('console.connected', {'vmName': vm_name})
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in console connect: {error_msg}")
        emit('console.error', {'error': error_msg})
        return False

@socketio.on('console.input')
def handle_console_input(data):
    try:
        session = console_sessions.get(request.sid)
        if session and isinstance(data, dict):
            text = data.get('text', '')
            if text:
                logger.debug(f"Sending console input: {len(text)} bytes")
                session.write_input(text)
    except Exception as e:
        logger.error(f"Error in console input: {str(e)}")
        emit('console.error', {'error': str(e)})

@socketio.on('disconnect')
def handle_disconnect():
    try:
        session = console_sessions.pop(request.sid, None)
        if session:
            logger.info(f"Cleaning up console session for client: {request.sid}")
            session.stop()
    except Exception as e:
        logger.error(f"Error in disconnect handler: {str(e)}")

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
        cloud_init = data.get('cloud_init')  # Optional cloud-init configuration
        
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
        
        # Create VM config with cloud-init if provided
        config = VMConfig(
            name=name,
            network_name=vpc_name,
            cloud_init=cloud_init
        )
        
        # Create the VM
        vm = vm_manager.create_vm(name, vpc_name, config)
        
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

@app.route('/api/vms/<vm_id>/snapshots', methods=['GET'])
def list_snapshots(vm_id):
    try:
        snapshots = vm_manager.list_snapshots(vm_id)
        return jsonify({'snapshots': snapshots})
    except Exception as e:
        logger.error(f"Error listing snapshots: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/snapshots', methods=['POST'])
def create_snapshot(vm_id):
    try:
        data = request.json
        name = data.get('name')
        description = data.get('description', '')
        
        if not name:
            return jsonify({'error': 'Snapshot name is required'}), 400
        
        success = vm_manager.create_snapshot(vm_id, name, description)
        if success:
            return jsonify({'message': f'Snapshot {name} created successfully'})
        return jsonify({'error': 'Failed to create snapshot'}), 500
    except Exception as e:
        logger.error(f"Error creating snapshot: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/snapshots/<name>', methods=['DELETE'])
def delete_snapshot(vm_id, name):
    try:
        success = vm_manager.delete_snapshot(vm_id, name)
        if success:
            return jsonify({'message': f'Snapshot {name} deleted successfully'})
        return jsonify({'error': 'Failed to delete snapshot'}), 500
    except Exception as e:
        logger.error(f"Error deleting snapshot: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/snapshots/<name>/revert', methods=['POST'])
def revert_to_snapshot(vm_id, name):
    try:
        success = vm_manager.revert_to_snapshot(vm_id, name)
        if success:
            return jsonify({'message': f'Reverted to snapshot {name} successfully'})
        return jsonify({'error': 'Failed to revert to snapshot'}), 500
    except Exception as e:
        logger.error(f"Error reverting to snapshot: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/snapshots/export', methods=['POST'])
def export_snapshot(vm_id):
    try:
        data = request.json
        name = data.get('name')
        export_path = data.get('path')
        
        if not name or not export_path:
            return jsonify({'error': 'Snapshot name and export path are required'}), 400
        
        success = vm_manager.create_snapshot_and_export(vm_id, name, Path(export_path))
        if success:
            return jsonify({'message': f'Snapshot {name} exported successfully to {export_path}'})
        return jsonify({'error': 'Failed to export snapshot'}), 500
    except Exception as e:
        logger.error(f"Error exporting snapshot: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_id>/snapshots/import', methods=['POST'])
def import_snapshot(vm_id):
    try:
        data = request.json
        snapshot_path = data.get('path')
        
        if not snapshot_path:
            return jsonify({'error': 'Snapshot path is required'}), 400
        
        success = vm_manager.import_snapshot(vm_id, Path(snapshot_path))
        if success:
            return jsonify({'message': 'Snapshot imported successfully'})
        return jsonify({'error': 'Failed to import snapshot'}), 500
    except Exception as e:
        logger.error(f"Error importing snapshot: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# EC2-like endpoints

# Cluster operations
@app.route('/api/clusters', methods=['GET'])
def list_clusters():
    """List all VPCs (clusters)"""
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
        return jsonify({'clusters': vpc_data})
    except Exception as e:
        logger.error(f"Error listing clusters: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters', methods=['POST'])
def create_cluster():
    """Create a new VPC (cluster)"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        name = data.get('name')
        cidr = data.get('cidr', '192.168.0.0/16')
        
        if not name:
            return jsonify({'error': 'Cluster name is required'}), 400

        try:
            ipaddress.ip_network(cidr)
        except ValueError as e:
            return jsonify({'error': f'Invalid CIDR format: {str(e)}'}), 400
            
        vpc = vpc_manager.create_vpc(name, cidr)
        
        return jsonify({
            'cluster': {
                'name': vpc.name,
                'cidr': vpc.cidr,
                'used_private_ips': vpc.used_private_ips,
                'used_public_ips': vpc.used_public_ips
            }
        })
    except Exception as e:
        logger.error(f"Error creating cluster: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<name>', methods=['GET'])
def get_cluster(name):
    """Get details of a specific VPC (cluster)"""
    try:
        vpc = vpc_manager.get_vpc(name)
        if not vpc:
            return jsonify({'error': f'Cluster {name} not found'}), 404
            
        return jsonify({
            'cluster': {
                'name': vpc.name,
                'cidr': vpc.cidr,
                'used_private_ips': vpc.used_private_ips,
                'used_public_ips': vpc.used_public_ips
            }
        })
    except Exception as e:
        logger.error(f"Error getting cluster: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Firewall rules
@app.route('/api/clusters/<cluster>/firewall/rules', methods=['GET'])
def list_firewall_rules(cluster):
    """List firewall rules for a cluster."""
    try:
        rules = firewall_manager.list_rules(cluster)
        return jsonify({'rules': rules})
    except Exception as e:
        logger.error(f"Error listing firewall rules: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/firewall/rules', methods=['POST'])
def create_firewall_rule(cluster):
    """Create a new firewall rule."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        required_fields = ['direction', 'protocol', 'port_range', 'source']
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            return jsonify({'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400

        rule = firewall_manager.create_rule(
            cluster_id=cluster,
            direction=data['direction'],
            protocol=data['protocol'],
            port_range=data['port_range'],
            source=data['source'],
            description=data.get('description', '')
        )
        
        return jsonify({'rule': rule.to_dict()})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating firewall rule: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/firewall/rules/<rule>', methods=['DELETE'])
def delete_firewall_rule(cluster, rule):
    """Delete a firewall rule."""
    try:
        firewall_manager.delete_rule(cluster, rule)
        return jsonify({'message': f'Firewall rule {rule} deleted'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Error deleting firewall rule: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Images and machine types
@app.route('/api/images', methods=['GET'])
def list_images():
    """List available VM images"""
    try:
        # For now, we only support Ubuntu 20.04 ARM64
        images = [{
            'id': 'ubuntu-20.04-arm64',
            'name': 'Ubuntu 20.04 ARM64',
            'architecture': 'arm64',
            'version': '20.04',
            'type': 'linux'
        }]
        return jsonify({'images': images})
    except Exception as e:
        logger.error(f"Error listing images: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/machine-types', methods=['GET'])
def list_machine_types():
    """List available machine types"""
    try:
        types = [{
            'id': 'default',
            'name': 'Default',
            'cpu_cores': 2,
            'memory_mb': 2048,
            'disk_gb': 20
        }]
        return jsonify({'machine_types': types})
    except Exception as e:
        logger.error(f"Error listing machine types: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# VM operations in clusters
@app.route('/api/clusters/<cluster>/machines', methods=['GET'])
def list_cluster_machines(cluster):
    """List machines in a cluster"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        machines = []
        for vm_id, vm in vm_manager.vms.items():
            if vm.config.network_name == cluster:
                try:
                    status = vm_manager.get_vm_status(vm_id)
                    machines.append({
                        'id': vm_id,
                        'name': vm.name,
                        'status': status['state'],
                        'cpu_cores': status['cpu_cores'],
                        'memory_mb': status['memory_mb'],
                        'network': status['network']
                    })
                except Exception as e:
                    logger.error(f"Error getting status for VM {vm.name}: {str(e)}")
                    machines.append({
                        'id': vm_id,
                        'name': vm.name,
                        'status': 'error',
                        'error': str(e)
                    })
        
        return jsonify({'machines': machines})
    except Exception as e:
        logger.error(f"Error listing machines: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines', methods=['POST'])
def create_cluster_machine(cluster):
    """Create a new machine in a cluster"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        data = request.json
        name = data.get('name')
        
        if not name:
            return jsonify({'error': 'Machine name is required'}), 400
        
        vm = vm_manager.create_vm(name, cluster)
        
        return jsonify({
            'machine': {
                'id': vm.id,
                'name': vm.name,
                'cluster': cluster
            }
        })
    except Exception as e:
        logger.error(f"Error creating machine: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>', methods=['GET'])
def get_cluster_machine(cluster, machine):
    """Get details of a specific machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        vm = None
        for vm_id, v in vm_manager.vms.items():
            if v.name == machine and v.config.network_name == cluster:
                vm = v
                break
                
        if not vm:
            return jsonify({'error': f'Machine {machine} not found in cluster {cluster}'}), 404
            
        status = vm_manager.get_vm_status(vm.id)
        
        return jsonify({
            'machine': {
                'id': vm.id,
                'name': vm.name,
                'status': status['state'],
                'cpu_cores': status['cpu_cores'],
                'memory_mb': status['memory_mb'],
                'network': status['network'],
                'ssh_port': status.get('ssh_port')
            }
        })
    except Exception as e:
        logger.error(f"Error getting machine: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/start', methods=['POST'])
def start_cluster_machine(cluster, machine):
    """Start a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        vm = None
        for vm_id, v in vm_manager.vms.items():
            if v.name == machine and v.config.network_name == cluster:
                vm = v
                break
                
        if not vm:
            return jsonify({'error': f'Machine {machine} not found in cluster {cluster}'}), 404
            
        success = vm_manager.start_vm(vm.id)
        if success:
            return jsonify({'message': f'Machine {machine} started successfully'})
        return jsonify({'error': 'Failed to start machine'}), 500
    except Exception as e:
        logger.error(f"Error starting machine: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/stop', methods=['POST'])
def stop_cluster_machine(cluster, machine):
    """Stop a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        vm = None
        for vm_id, v in vm_manager.vms.items():
            if v.name == machine and v.config.network_name == cluster:
                vm = v
                break
                
        if not vm:
            return jsonify({'error': f'Machine {machine} not found in cluster {cluster}'}), 404
            
        success = vm_manager.stop_vm(vm.id)
        if success:
            return jsonify({'message': f'Machine {machine} stopped successfully'})
        return jsonify({'error': 'Failed to stop machine'}), 500
    except Exception as e:
        logger.error(f"Error stopping machine: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/restart', methods=['POST'])
def restart_cluster_machine(cluster, machine):
    """Restart a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        vm = None
        for vm_id, v in vm_manager.vms.items():
            if v.name == machine and v.config.network_name == cluster:
                vm = v
                break
                
        if not vm:
            return jsonify({'error': f'Machine {machine} not found in cluster {cluster}'}), 404
            
        success = vm_manager.stop_vm(vm.id)
        if not success:
            return jsonify({'error': 'Failed to stop machine for restart'}), 500
            
        success = vm_manager.start_vm(vm.id)
        if success:
            return jsonify({'message': f'Machine {machine} restarted successfully'})
        return jsonify({'error': 'Failed to restart machine'}), 500
    except Exception as e:
        logger.error(f"Error restarting machine: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/resize', methods=['POST'])
def resize_cluster_machine(cluster, machine):
    """Resize a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        # TODO: Implement machine resizing
        return jsonify({'message': 'Machine resize not implemented'}), 501
    except Exception as e:
        logger.error(f"Error resizing machine: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/terminate', methods=['POST'])
def terminate_cluster_machine(cluster, machine):
    """Terminate (delete) a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        vm = None
        for vm_id, v in vm_manager.vms.items():
            if v.name == machine and v.config.network_name == cluster:
                vm = v
                break
                
        if not vm:
            return jsonify({'error': f'Machine {machine} not found in cluster {cluster}'}), 404
            
        success = vm_manager.delete_vm(vm.id)
        if success:
            return jsonify({'message': f'Machine {machine} terminated successfully'})
        return jsonify({'error': 'Failed to terminate machine'}), 500
    except Exception as e:
        logger.error(f"Error terminating machine: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/snapshot', methods=['POST'])
def create_machine_snapshot(cluster, machine):
    """Create a data snapshot of a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        vm = None
        for vm_id, v in vm_manager.vms.items():
            if v.name == machine and v.config.network_name == cluster:
                vm = v
                break
                
        if not vm:
            return jsonify({'error': f'Machine {machine} not found in cluster {cluster}'}), 404
            
        data = request.json
        name = data.get('name')
        description = data.get('description', '')
        
        if not name:
            return jsonify({'error': 'Snapshot name is required'}), 400
            
        success = vm_manager.create_snapshot(vm.id, name, description)
        if success:
            return jsonify({'message': f'Snapshot {name} created successfully'})
        return jsonify({'error': 'Failed to create snapshot'}), 500
    except Exception as e:
        logger.error(f"Error creating snapshot: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/image', methods=['POST'])
def create_machine_image(cluster, machine):
    """Create a bootable image from a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        # TODO: Implement creating bootable images
        return jsonify({'message': 'Creating bootable images not implemented'}), 501
    except Exception as e:
        logger.error(f"Error creating image: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/serial-console', methods=['GET'])
def get_machine_console(cluster, machine):
    """Get serial console URL for a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        vm = None
        for vm_id, v in vm_manager.vms.items():
            if v.name == machine and v.config.network_name == cluster:
                vm = v
                break
                
        if not vm:
            return jsonify({'error': f'Machine {machine} not found in cluster {cluster}'}), 404
            
        # Return WebSocket URL for console
        return jsonify({
            'console_url': f'ws://localhost:5000/socket.io/?vmName={machine}'
        })
    except Exception as e:
        logger.error(f"Error getting console URL: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Disk operations
@app.route('/api/clusters/<cluster>/machines/<machine>/disks', methods=['POST'])
def attach_disk(cluster, machine):
    """Attach a disk to a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        # TODO: Implement disk operations
        return jsonify({'message': 'Disk operations not implemented'}), 501
    except Exception as e:
        logger.error(f"Error attaching disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/disks/<disk>', methods=['DELETE'])
def detach_disk(cluster, machine, disk):
    """Detach a disk from a machine"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        # TODO: Implement disk operations
        return jsonify({'message': 'Disk operations not implemented'}), 501
    except Exception as e:
        logger.error(f"Error detaching disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/disks', methods=['GET'])
def list_cluster_disks(cluster):
    """List all disks in a cluster"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        # TODO: Implement disk operations
        return jsonify({'disks': []})
    except Exception as e:
        logger.error(f"Error listing disks: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/disks/<disk>', methods=['GET'])
def get_cluster_disk(cluster, disk):
    """Get details of a specific disk"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        # TODO: Implement disk operations
        return jsonify({'error': 'Disk operations not implemented'}), 501
    except Exception as e:
        logger.error(f"Error getting disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/disks/<disk>/size', methods=['POST'])
def resize_disk(cluster, disk):
    """Resize a disk"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        # TODO: Implement disk operations
        return jsonify({'message': 'Disk operations not implemented'}), 501
    except Exception as e:
        logger.error(f"Error resizing disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/disks/<disk>/performance', methods=['POST'])
def update_disk_performance(cluster, disk):
    """Update disk performance settings"""
    try:
        vpc = vpc_manager.get_vpc(cluster)
        if not vpc:
            return jsonify({'error': f'Cluster {cluster} not found'}), 404
            
        # TODO: Implement disk operations
        return jsonify({'message': 'Disk operations not implemented'}), 501
    except Exception as e:
        logger.error(f"Error updating disk performance: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# IP Management endpoints
@app.route('/api/ips', methods=['GET'])
def list_ips():
    """List all IPs in the pool."""
    try:
        ips = ip_manager.list_ips()
        return jsonify({'ips': ips})
    except Exception as e:
        logger.error(f"Error listing IPs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/ips', methods=['POST'])
def add_ip():
    """Add a new IP to the pool."""
    try:
        data = request.json
        if not data or 'ip' not in data:
            return jsonify({'error': 'IP address is required'}), 400
        
        ip = data['ip']
        ip_manager.add_ip(ip)
        return jsonify({'message': f'IP {ip} added to pool'})
    except Exception as e:
        logger.error(f"Error adding IP: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/ips/<ip>', methods=['DELETE'])
def remove_ip(ip):
    """Remove an IP from the pool."""
    try:
        ip_manager.remove_ip(ip)
        return jsonify({'message': f'IP {ip} removed from pool'})
    except Exception as e:
        logger.error(f"Error removing IP: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/ips', methods=['GET'])
def list_machine_ips(cluster, machine):
    """List IPs attached to a machine."""
    try:
        ips = ip_manager.get_machine_ips(machine)
        return jsonify({'ips': ips})
    except Exception as e:
        logger.error(f"Error listing machine IPs: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/ips', methods=['POST'])
def attach_ip(cluster, machine):
    """Attach an IP to a machine."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # If specific IP is provided, use it (elastic IP case)
        if 'ip' in data:
            ip = data['ip']
            is_elastic = True
        else:
            # Get random available IP
            ip = ip_manager.get_available_ip()
            if not ip:
                return jsonify({'error': 'No available IPs in pool'}), 400
            is_elastic = False

        ip_manager.attach_ip(ip, machine, is_elastic)
        return jsonify({
            'message': f'IP {ip} attached to machine {machine}',
            'ip': ip
        })
    except Exception as e:
        logger.error(f"Error attaching IP: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/clusters/<cluster>/machines/<machine>/ips/<ip>', methods=['DELETE'])
def detach_ip(cluster, machine, ip):
    """Detach an IP from a machine."""
    try:
        ip_manager.detach_ip(ip)
        return jsonify({'message': f'IP {ip} detached from machine {machine}'})
    except Exception as e:
        logger.error(f"Error detaching IP: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Disk Management Endpoints
@app.route('/api/disks', methods=['GET'])
def list_disks():
    """List all disks."""
    try:
        disks = vm_manager.list_disks()
        return jsonify({'disks': disks})
    except Exception as e:
        logger.error(f"Error listing disks: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/disks', methods=['POST'])
def create_disk():
    """Create a new disk."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        name = data.get('name')
        size_gb = data.get('size_gb')

        if not name or not size_gb:
            return jsonify({'error': 'Name and size are required'}), 400

        try:
            size_gb = int(size_gb)
            if size_gb <= 0:
                raise ValueError()
        except ValueError:
            return jsonify({'error': 'Size must be a positive integer'}), 400

        disk = vm_manager.create_disk(name, size_gb)
        return jsonify({'disk': disk})
    except Exception as e:
        logger.error(f"Error creating disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/disks/<disk_id>', methods=['DELETE'])
def delete_disk(disk_id):
    """Delete a disk."""
    try:
        vm_manager.delete_disk(disk_id)
        return jsonify({'message': f'Disk {disk_id} deleted successfully'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Error deleting disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/disks/<disk_id>/attach', methods=['POST'])
def attach_disk(disk_id):
    """Attach a disk to a VM."""
    try:
        data = request.json
        if not data or 'vm_name' not in data:
            return jsonify({'error': 'VM name is required'}), 400

        vm_manager.attach_disk(disk_id, data['vm_name'])
        return jsonify({'message': f'Disk {disk_id} attached to VM {data["vm_name"]}'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error attaching disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/disks/<disk_id>/detach', methods=['POST'])
def detach_disk(disk_id):
    """Detach a disk from its VM."""
    try:
        vm_manager.detach_disk(disk_id)
        return jsonify({'message': f'Disk {disk_id} detached successfully'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error detaching disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/disks/<disk_id>/resize', methods=['POST'])
def resize_disk(disk_id):
    """Resize a disk."""
    try:
        data = request.json
        if not data or 'size_gb' not in data:
            return jsonify({'error': 'New size is required'}), 400

        try:
            size_gb = int(data['size_gb'])
            if size_gb <= 0:
                raise ValueError()
        except ValueError:
            return jsonify({'error': 'Size must be a positive integer'}), 400

        vm_manager.resize_disk(disk_id, size_gb)
        return jsonify({'message': f'Disk {disk_id} resized to {size_gb}GB'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error resizing disk: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/<vm_name>/disks', methods=['GET'])
def list_vm_disks(vm_name):
    """List all disks attached to a VM."""
    try:
        disks = vm_manager.get_machine_disks(vm_name)
        return jsonify({'disks': disks})
    except Exception as e:
        logger.error(f"Error listing VM disks: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Use eventlet's WSGI server
    logger.info("Starting server with eventlet WebSocket support...")
    socketio.run(
        app,
        host='0.0.0.0',
        port=5000,
        debug=True,
        use_reloader=False,
        log_output=True,
        websocket=True
    ) 