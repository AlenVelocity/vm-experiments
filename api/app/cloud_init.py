from flask import Blueprint, request, jsonify
from jinja2 import Template
import yaml
import os
from pathlib import Path
from typing import Dict, Optional
import copy

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
        return copy.deepcopy(base)
    
    result = copy.deepcopy(base)
    
    def recursive_merge(d1: Dict, d2: Dict) -> Dict:
        for k, v in d2.items():
            if k in d1 and isinstance(d1[k], dict) and isinstance(v, dict):
                recursive_merge(d1[k], v)
            elif k in d1 and isinstance(d1[k], list) and isinstance(v, list):
                d1[k] = d1[k].copy() + v
            else:
                d1[k] = copy.deepcopy(v)
        return d1
    
    return recursive_merge(result, custom)

@cloud_init.route('/generate', methods=['POST'])
def generate_config():
    """Generate cloud-init configuration"""
    try:
        request_data = request.get_json() or {}
        
        # Start with a deep copy of default context
        context = copy.deepcopy(get_default_context())
        
        # Merge with custom context if provided
        if 'context' in request_data:
            context = merge_configs(context, request_data['context'])
        
        # Load and validate templates
        user_data_template = load_template('user-data.yaml', """#cloud-config
packages:
{% for package in packages %}
  - {{ package }}
{% endfor %}

timezone: {{ timezone }}

ntp:
  enabled: true
  servers:
{% for server in ntp_servers %}
    - {{ server }}
{% endfor %}

users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
{% for key in ssh_authorized_keys %}
      - {{ key }}
{% endfor %}
""")

        network_config_template = load_template('network-config.yaml', """version: 2
ethernets:
    ens3:
        dhcp4: true
        nameservers:
            addresses:
{% for ns in nameservers %}
                - {{ ns }}
{% endfor %}
""")

        # Render templates with context
        user_data = Template(user_data_template).render(**context)
        network_config = Template(network_config_template).render(**context)
        
        # Validate generated YAML
        validate_yaml(user_data, 'user-data')
        validate_yaml(network_config, 'network-config')
        
        return jsonify({
            'user_data': user_data,
            'network_config': network_config
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 400

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