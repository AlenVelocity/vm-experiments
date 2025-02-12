from flask import Blueprint, jsonify
import psutil
from datetime import datetime
from typing import Dict

monitoring = Blueprint('monitoring', __name__)

def get_system_metrics() -> Dict:
    """Get system resource metrics"""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    return {
        "cpu": {
            "percent": cpu_percent,
            "count": psutil.cpu_count(),
            "frequency": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else {}
        },
        "memory": {
            "total": memory.total,
            "available": memory.available,
            "used": memory.used,
            "percent": memory.percent
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent
        },
        "network": {
            "connections": len(psutil.net_connections()),
            "interfaces": {name: addrs for name, addrs in psutil.net_if_addrs().items()}
        },
        "timestamp": datetime.now().isoformat()
    }

def get_process_metrics() -> Dict:
    """Get process-specific metrics"""
    current_process = psutil.Process()
    return {
        "pid": current_process.pid,
        "cpu_percent": current_process.cpu_percent(),
        "memory_percent": current_process.memory_percent(),
        "threads": current_process.num_threads(),
        "open_files": len(current_process.open_files()),
        "connections": len(current_process.connections())
    }

@monitoring.route('/metrics', methods=['GET'])
def get_metrics():
    """Get all monitoring metrics"""
    try:
        return jsonify({
            "system": get_system_metrics(),
            "process": get_process_metrics()
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get metrics: {str(e)}"}), 500

@monitoring.route('/metrics/system', methods=['GET'])
def get_system_stats():
    """Get system-level metrics"""
    try:
        return jsonify(get_system_metrics())
    except Exception as e:
        return jsonify({"error": f"Failed to get system metrics: {str(e)}"}), 500

@monitoring.route('/metrics/process', methods=['GET'])
def get_process_stats():
    """Get process-level metrics"""
    try:
        return jsonify(get_process_metrics())
    except Exception as e:
        return jsonify({"error": f"Failed to get process metrics: {str(e)}"}), 500 