from flask import Blueprint, request, jsonify
from jinja2 import Template
import yaml
import os
from pathlib import Path
from typing import Dict, Optional

cloud_init = Blueprint('cloud_init', __name__)

class CloudInitError(Exception):
    """Base exception for cloud-init related errors"""
    pass

def validate_yaml(data: str, name: str) -> None:
    """Validate YAML format"""
    try:
        yaml.safe_load(data)
    except yaml.YAMLError as e:
        raise CloudInitError(f"Invalid YAML in {name}: {str(e)}")

def validate_template(template: str, name: str) -> None:
    """Validate Jinja2 template syntax"""
    try:
        Template(template)
    except Exception as e:
        raise CloudInitError(f"Invalid template in {name}: {str(e)}")

def get_default_context() -> Dict:
    """Get default context for templates"""
    return {
        "packages": [
            "net-tools",
            "iproute2",
            "iptables",
            "netcat",
            "curl",
            "wget",
            "vim"
        ],
        "nameservers": ["8.8.8.8", "8.8.4.4"],
        "timezone": "UTC",
        "ntp_servers": ["pool.ntp.org"],
        "ssh_authorized_keys": []
    }

def merge_configs(base: Dict, custom: Optional[Dict] = None) -> Dict:
    """Deep merge configurations"""
    if not custom:
        return base
        
    result = base.copy()
    for key, value in custom.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key].update(value)
        elif isinstance(value, list) and key in result and isinstance(result[key], list):
            result[key].extend(value)
        else:
            result[key] = value
    return result

@cloud_init.route('/generate', methods=['POST'])
def generate_config():
    """Generate cloud-init configuration"""
    try:
        data = request.get_json()
        if not data:
            raise CloudInitError("No data provided")

        # Validate required fields
        required = ["vm_name", "network_config"]
        missing = [field for field in required if field not in data]
        if missing:
            raise CloudInitError(f"Missing required fields: {', '.join(missing)}")

        network_config = data["network_config"]
        if not network_config.get("ip_address"):
            raise CloudInitError("IP address is required in network configuration")
        if not network_config.get("gateway"):
            raise CloudInitError("Gateway is required in network configuration")

        # Build context
        context = {
            "instance_id": data["vm_name"],
            "hostname": data["vm_name"],
            "ip_address": network_config["ip_address"],
            "gateway": network_config["gateway"],
            "netmask": network_config.get("netmask", "255.255.255.0"),
        }
        
        # Merge with defaults and custom config
        context = merge_configs(
            merge_configs(get_default_context(), context),
            data.get("custom_config")
        )

        # Load templates
        templates_dir = Path("data/templates")
        if not templates_dir.exists():
            templates_dir.mkdir(parents=True)

        def load_template(name: str, default: str) -> str:
            template_file = templates_dir / f"{name}.yaml"
            return template_file.read_text() if template_file.exists() else default

        default_user_data = load_template("user_data", """#cloud-config
packages: {packages}
package_update: true
package_upgrade: true

write_files:
  - path: /etc/netplan/50-cloud-init.yaml
    content: |
      network:
        version: 2
        ethernets:
          eth0:
            addresses: [{ip_address}/24]
            gateway4: {gateway}
            nameservers:
              addresses: {nameservers}

timezone: {timezone}

ntp:
  enabled: true
  servers: {ntp_servers}

ssh_authorized_keys: {ssh_authorized_keys}

runcmd:
  - netplan apply
  - iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
  - echo 1 > /proc/sys/net/ipv4/ip_forward
  - systemctl restart ntp""")

        default_meta_data = load_template("meta_data", """instance-id: {instance_id}
local-hostname: {hostname}
network-interfaces: |
  auto eth0
  iface eth0 inet static
  address {ip_address}
  netmask {netmask}
  gateway {gateway}
  dns-nameservers {nameservers}""")

        default_network_config = load_template("network_config", """version: 2
ethernets:
  eth0:
    addresses: [{ip_address}/24]
    gateway4: {gateway}
    nameservers:
      addresses: {nameservers}
    routes:
      - to: 0.0.0.0/0
        via: {gateway}""")

        # Use custom templates if provided
        templates = {
            "user_data": data.get("custom_user_data", default_user_data),
            "meta_data": data.get("custom_meta_data", default_meta_data),
            "network_config": data.get("custom_network_config", default_network_config)
        }

        # Validate and render templates
        result = {}
        for name, template in templates.items():
            validate_template(template, name)
            rendered = Template(template).render(**context)
            validate_yaml(rendered, name)
            result[name] = rendered

        # Include context for debugging
        result["context"] = context

        return jsonify(result)

    except CloudInitError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@cloud_init.route('/templates', methods=['GET'])
def list_templates():
    """List available templates"""
    templates_dir = Path("data/templates")
    if not templates_dir.exists():
        return jsonify({"templates": []})

    templates = []
    for template in templates_dir.glob("*.yaml"):
        templates.append({
            "name": template.stem,
            "path": str(template),
            "content": template.read_text()
        })
    return jsonify({"templates": templates})

@cloud_init.route('/templates/<name>', methods=['PUT'])
def update_template(name):
    """Update a template"""
    try:
        data = request.get_json()
        if not data or "content" not in data:
            raise CloudInitError("Template content is required")

        # Validate template syntax
        validate_template(data["content"], name)
        
        # Save template
        templates_dir = Path("data/templates")
        templates_dir.mkdir(parents=True, exist_ok=True)
        
        template_file = templates_dir / f"{name}.yaml"
        template_file.write_text(data["content"])
        
        return jsonify({"message": f"Template {name} updated successfully"})
        
    except CloudInitError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@cloud_init.route('/templates/<name>', methods=['DELETE'])
def delete_template(name):
    """Delete a template"""
    try:
        template_file = Path(f"data/templates/{name}.yaml")
        if template_file.exists():
            template_file.unlink()
            return jsonify({"message": f"Template {name} deleted successfully"})
        return jsonify({"error": "Template not found"}), 404
        
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500 