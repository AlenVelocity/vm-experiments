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
    try:
        current_process = psutil.Process()
        metrics = {
            "pid": current_process.pid,
            "status": current_process.status(),
            "metrics": {}
        }
        
        # CPU metrics with error handling
        try:
            metrics["metrics"]["cpu"] = {
                "percent": current_process.cpu_percent(),
                "num_threads": current_process.num_threads(),
                "cpu_times": current_process.cpu_times()._asdict()
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            metrics["metrics"]["cpu"] = {"error": str(e)}
        
        # Memory metrics with error handling
        try:
            memory_info = current_process.memory_info()
            metrics["metrics"]["memory"] = {
                "rss": memory_info.rss,
                "vms": memory_info.vms,
                "percent": current_process.memory_percent()
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            metrics["metrics"]["memory"] = {"error": str(e)}
        
        # File descriptors with error handling
        try:
            open_files = current_process.open_files()
            metrics["metrics"]["files"] = {
                "open_count": len(open_files),
                "files": [f.path for f in open_files]
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            metrics["metrics"]["files"] = {"error": str(e)}
        
        # Network connections with error handling
        try:
            connections = current_process.connections()
            metrics["metrics"]["network"] = {
                "connection_count": len(connections),
                "connections": [
                    {
                        "fd": c.fd,
                        "family": str(c.family),
                        "type": str(c.type),
                        "status": c.status
                    }
                    for c in connections
                ]
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            metrics["metrics"]["network"] = {"error": str(e)}
        
        metrics["timestamp"] = datetime.now().isoformat()
        return metrics
        
    except psutil.NoSuchProcess:
        return {"error": "Process no longer exists"}
    except psutil.AccessDenied:
        return {"error": "Access denied to process information"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}

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
    """Get process metrics endpoint"""
    return jsonify(get_process_metrics()) 