from flask import Blueprint, request, jsonify, send_file
from pathlib import Path
import json
import yaml
from datetime import datetime
import shutil
import tempfile
from typing import Dict, List
import zipfile
import io

templates = Blueprint('templates', __name__)

class TemplateError(Exception):
    """Base exception for template-related errors"""
    pass

def get_templates_metadata() -> Dict:
    """Get templates metadata from file"""
    metadata_file = Path("data/templates/metadata.json")
    if not metadata_file.exists():
        return {}
    try:
        return json.loads(metadata_file.read_text())
    except json.JSONDecodeError:
        return {}

def save_templates_metadata(metadata: Dict) -> None:
    """Save templates metadata to file"""
    metadata_file = Path("data/templates/metadata.json")
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.write_text(json.dumps(metadata, indent=2))

def validate_template_yaml(content: str, name: str) -> None:
    """Validate YAML content"""
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise TemplateError(f"Invalid YAML in {name}: {str(e)}")

@templates.route('/', methods=['GET'])
def list_templates():
    """List all templates"""
    return jsonify({"templates": get_templates_metadata()})

@templates.route('/', methods=['POST'])
def create_template():
    """Create a new template"""
    try:
        data = request.get_json()
        if not data:
            raise TemplateError("No data provided")

        required = ["name", "image_url", "image_size", "default_cpu", 
                   "default_memory", "default_disk"]
        missing = [field for field in required if field not in data]
        if missing:
            raise TemplateError(f"Missing required fields: {', '.join(missing)}")

        # Validate cloud-init templates if provided
        for template_type in ["user_data", "network_config"]:
            if template_type in data and data[template_type]:
                validate_template_yaml(data[template_type], template_type)

        metadata = get_templates_metadata()
        if data["name"] in metadata:
            raise TemplateError(f"Template {data['name']} already exists")

        # Save template data
        metadata[data["name"]] = {
            "image_url": data["image_url"],
            "image_size": data["image_size"],
            "default_cpu": data["default_cpu"],
            "default_memory": data["default_memory"],
            "default_disk": data["default_disk"],
            "description": data.get("description", ""),
            "user_data_template": data.get("user_data", ""),
            "meta_data_template": data.get("meta_data", ""),
            "network_config_template": data.get("network_config", ""),
            "created_at": datetime.now().isoformat()
        }
        save_templates_metadata(metadata)

        return jsonify({
            "message": f"Template {data['name']} created",
            "template": metadata[data["name"]]
        })

    except TemplateError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@templates.route('/<name>', methods=['GET'])
def get_template(name):
    """Get template details"""
    metadata = get_templates_metadata()
    if name not in metadata:
        return jsonify({"error": "Template not found"}), 404
    return jsonify(metadata[name])

@templates.route('/<name>', methods=['PUT'])
def update_template(name):
    """Update a template"""
    try:
        data = request.get_json()
        if not data:
            raise TemplateError("No data provided")

        metadata = get_templates_metadata()
        if name not in metadata:
            raise TemplateError("Template not found")

        # Validate cloud-init templates if provided
        for template_type in ["user_data", "network_config"]:
            if template_type in data and data[template_type]:
                validate_template_yaml(data[template_type], template_type)

        # Update template data
        template = metadata[name]
        updateable_fields = [
            "image_url", "image_size", "default_cpu", "default_memory",
            "default_disk", "description", "user_data_template",
            "meta_data_template", "network_config_template"
        ]
        
        for field in updateable_fields:
            if field in data:
                template[field] = data[field]

        template["updated_at"] = datetime.now().isoformat()
        save_templates_metadata(metadata)

        return jsonify({
            "message": f"Template {name} updated",
            "template": template
        })

    except TemplateError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@templates.route('/<name>', methods=['DELETE'])
def delete_template(name):
    """Delete a template"""
    try:
        metadata = get_templates_metadata()
        if name not in metadata:
            raise TemplateError("Template not found")

        del metadata[name]
        save_templates_metadata(metadata)

        return jsonify({"message": f"Template {name} deleted"})

    except TemplateError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@templates.route('/<name>/render', methods=['POST'])
def render_template(name):
    """Render template with provided context"""
    try:
        data = request.get_json()
        if not data or "context" not in data:
            raise TemplateError("Context is required")

        metadata = get_templates_metadata()
        if name not in metadata:
            raise TemplateError("Template not found")

        template = metadata[name]
        rendered = {}

        # Render each template type if available
        from jinja2 import Template as Jinja2Template
        for template_type in ["user_data_template", "meta_data_template", "network_config_template"]:
            if template.get(template_type):
                try:
                    rendered[template_type.replace("_template", "")] = (
                        Jinja2Template(template[template_type])
                        .render(**data["context"])
                    )
                except Exception as e:
                    raise TemplateError(f"Error rendering {template_type}: {str(e)}")

        return jsonify({
            "rendered": rendered,
            "context": data["context"]
        })

    except TemplateError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@templates.route('/backup', methods=['GET'])
def backup_templates():
    """Create a backup of all templates"""
    try:
        # Create a temporary directory for backup
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Copy templates metadata
            metadata = get_templates_metadata()
            (temp_path / "metadata.json").write_text(
                json.dumps(metadata, indent=2)
            )
            
            # Create ZIP file in memory
            memory_file = io.BytesIO()
            with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Add metadata file
                zf.write(temp_path / "metadata.json", "metadata.json")
                
                # Add any additional template files
                templates_dir = Path("data/templates")
                if templates_dir.exists():
                    for template_file in templates_dir.glob("*.yaml"):
                        zf.write(template_file, f"templates/{template_file.name}")
            
            # Prepare file for download
            memory_file.seek(0)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return send_file(
                memory_file,
                mimetype='application/zip',
                as_attachment=True,
                download_name=f'templates_backup_{timestamp}.zip'
            )
            
    except Exception as e:
        return jsonify({"error": f"Failed to create backup: {str(e)}"}), 500

@templates.route('/restore', methods=['POST'])
def restore_templates():
    """Restore templates from backup"""
    try:
        if 'file' not in request.files:
            raise TemplateError("No file provided")
            
        backup_file = request.files['file']
        if not backup_file.filename.endswith('.zip'):
            raise TemplateError("Invalid backup file format")
            
        # Create temporary directory for restoration
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            backup_path = temp_path / backup_file.filename
            
            # Save and extract backup file
            backup_file.save(backup_path)
            with zipfile.ZipFile(backup_path, 'r') as zf:
                zf.extractall(temp_path)
            
            # Load and validate metadata
            metadata_file = temp_path / "metadata.json"
            if not metadata_file.exists():
                raise TemplateError("Invalid backup: metadata.json not found")
                
            try:
                metadata = json.loads(metadata_file.read_text())
            except json.JSONDecodeError:
                raise TemplateError("Invalid metadata format")
            
            # Restore templates
            templates_dir = Path("data/templates")
            templates_dir.mkdir(parents=True, exist_ok=True)
            
            # Restore metadata
            save_templates_metadata(metadata)
            
            # Restore template files
            template_files_dir = temp_path / "templates"
            if template_files_dir.exists():
                for template_file in template_files_dir.glob("*.yaml"):
                    shutil.copy2(
                        template_file,
                        templates_dir / template_file.name
                    )
            
            return jsonify({
                "message": "Templates restored successfully",
                "templates": len(metadata)
            })
            
    except TemplateError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to restore backup: {str(e)}"}), 500

@templates.route('/validate', methods=['POST'])
def validate_templates():
    """Validate template syntax"""
    try:
        data = request.get_json()
        if not data:
            raise TemplateError("No data provided")
            
        templates_to_validate = data.get("templates", {})
        validation_results = {}
        
        for template_name, template_data in templates_to_validate.items():
            try:
                # Validate cloud-init templates
                for template_type in ["user_data", "network_config"]:
                    if template_type in template_data:
                        validate_template_yaml(
                            template_data[template_type],
                            f"{template_name}/{template_type}"
                        )
                
                # Validate resource values
                for field in ["default_cpu", "default_memory", "default_disk"]:
                    if field in template_data:
                        value = template_data[field]
                        if not isinstance(value, (int, float)) or value <= 0:
                            raise TemplateError(f"Invalid {field}: must be a positive number")
                
                validation_results[template_name] = {
                    "status": "valid",
                    "message": "Template is valid"
                }
            except Exception as e:
                validation_results[template_name] = {
                    "status": "invalid",
                    "message": str(e)
                }
        
        return jsonify({
            "validation_results": validation_results
        })
        
    except TemplateError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Validation failed: {str(e)}"}), 500

@templates.route('/clone/<name>', methods=['POST'])
def clone_template(name):
    """Clone an existing template"""
    try:
        data = request.get_json() or {}
        new_name = data.get("new_name")
        if not new_name:
            raise TemplateError("New template name is required")
            
        metadata = get_templates_metadata()
        if name not in metadata:
            raise TemplateError("Source template not found")
            
        if new_name in metadata:
            raise TemplateError("Target template name already exists")
            
        # Clone template data
        template = metadata[name].copy()
        template["created_at"] = datetime.now().isoformat()
        template["cloned_from"] = name
        
        # Save new template
        metadata[new_name] = template
        save_templates_metadata(metadata)
        
        return jsonify({
            "message": f"Template {name} cloned to {new_name}",
            "template": template
        })
        
    except TemplateError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to clone template: {str(e)}"}), 500 