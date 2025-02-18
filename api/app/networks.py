from flask import Blueprint, request, jsonify
from pathlib import Path
import json
import ipaddress
from datetime import datetime
from typing import Dict, List

networks = Blueprint('networks', __name__)

class NetworkError(Exception):
    """Base exception for network-related errors"""
    pass

def get_networks_metadata() -> Dict:
    """Get networks metadata from file"""
    metadata_file = Path("networks/networks.json")
    if not metadata_file.exists():
        return {}
    try:
        return json.loads(metadata_file.read_text())
    except json.JSONDecodeError:
        return {}

def save_networks_metadata(metadata: Dict) -> None:
    """Save networks metadata to file"""
    metadata_file = Path("networks/networks.json")
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.write_text(json.dumps(metadata, indent=2))

def validate_cidr(cidr: str) -> None:
    """Validate CIDR format"""
    try:
        network = ipaddress.ip_network(cidr)
        if network.prefixlen > 28:  # Ensure subnet isn't too small
            raise NetworkError("CIDR prefix length must be 28 or less")
    except ValueError as e:
        raise NetworkError(f"Invalid CIDR format: {str(e)}")

@networks.route('/', methods=['GET'])
def list_networks():
    """List all networks"""
    metadata = get_networks_metadata()
    return jsonify({"networks": [
        {"id": id, **data}
        for id, data in metadata.items()
    ]})

@networks.route('/', methods=['POST'])
def create_network():
    """Create a new network"""
    try:
        data = request.get_json()
        if not data:
            raise NetworkError("No data provided")

        required = ["name", "cidr"]
        missing = [field for field in required if field not in data]
        if missing:
            raise NetworkError(f"Missing required fields: {', '.join(missing)}")

        # Validate CIDR
        validate_cidr(data["cidr"])

        # Generate unique ID
        network_id = str(len(get_networks_metadata()) + 1)

        metadata = get_networks_metadata()
        metadata[network_id] = {
            "name": data["name"],
            "cidr": data["cidr"],
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "subnets": data.get("subnets", []),
            "used_ips": []
        }

        save_networks_metadata(metadata)
        return jsonify({
            "message": f"Network {data['name']} created",
            "network": {"id": network_id, **metadata[network_id]}
        })

    except NetworkError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@networks.route('/<id>', methods=['GET'])
def get_network(id):
    """Get network details"""
    metadata = get_networks_metadata()
    if id not in metadata:
        return jsonify({"error": "Network not found"}), 404
    return jsonify({"id": id, **metadata[id]})

@networks.route('/<id>/ips', methods=['POST'])
def allocate_ip(id):
    """Allocate an IP from the network"""
    metadata = get_networks_metadata()
    if id not in metadata:
        return jsonify({"error": "Network not found"}), 404

    network_data = metadata[id]
    # Initialize used_ips if not present
    network_data['used_ips'] = network_data.get('used_ips', [])

    try:
        network = ipaddress.ip_network(network_data['cidr'])
        # Skip network address, broadcast address and gateway
        available_ips = [str(ip) for ip in network.hosts()][1:]
        # Filter out already used IPs
        available_ips = [ip for ip in available_ips if ip not in network_data['used_ips']]
        
        if not available_ips:
            return jsonify({"error": "No available IPs"}), 409

        # Atomic operation: get and update in one go
        allocated_ip = available_ips[0]
        network_data['used_ips'].append(allocated_ip)
        network_data['last_updated'] = datetime.now().isoformat()
        
        # Save immediately to prevent race conditions
        save_networks_metadata(metadata)
        
        return jsonify({
            "ip": allocated_ip,
            "network_id": id,
            "cidr": network_data['cidr']
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@networks.route('/<id>/ips/<ip>', methods=['DELETE'])
def release_ip(id, ip):
    """Release an allocated IP"""
    metadata = get_networks_metadata()
    if id not in metadata:
        return jsonify({"error": "Network not found"}), 404

    network_data = metadata[id]
    # Initialize used_ips if not present
    network_data['used_ips'] = network_data.get('used_ips', [])

    if ip not in network_data['used_ips']:
        return jsonify({"error": "IP not allocated"}), 404

    try:
        network_data['used_ips'].remove(ip)
        network_data['last_updated'] = datetime.now().isoformat()
        save_networks_metadata(metadata)
        return jsonify({"message": "IP released successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@networks.route('/<id>', methods=['DELETE'])
def delete_network(id):
    """Delete a network"""
    try:
        metadata = get_networks_metadata()
        if id not in metadata:
            raise NetworkError("Network not found")

        network = metadata[id]
        if network["used_ips"]:
            raise NetworkError("Cannot delete network with allocated IPs")

        del metadata[id]
        save_networks_metadata(metadata)
        return jsonify({"message": f"Network {network['name']} deleted"})

    except NetworkError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500 