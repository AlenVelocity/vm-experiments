from flask import Blueprint, jsonify
import psutil
from datetime import datetime
from typing import Dict, List
import contextlib

monitoring = Blueprint('monitoring', __name__)

@contextlib.contextmanager
def managed_process_info(pid=None):
    """Context manager for process information gathering"""
    process = None
    try:
        process = psutil.Process(pid) if pid else psutil.Process()
        yield process
    finally:
        if process:
            process.oneshot(False)  # Disable oneshot mode to prevent caching

def safe_collect_network_info() -> Dict:
    """Safely collect network information with proper cleanup"""
    connections = []
    try:
        # Use a timeout to prevent hanging
        with contextlib.timeout(5):
            connections = psutil.net_connections()
        return {
            "connections": len(connections),
            "interfaces": {
                name: list(addrs) for name, addrs in psutil.net_if_addrs().items()
            }
        }
    except TimeoutError:
        return {"error": "Network info collection timed out"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        # Clear references to prevent memory leaks
        del connections

def get_system_metrics() -> Dict:
    """Get system resource metrics with proper resource cleanup"""
    metrics = {}
    try:
        # CPU metrics with interval
        with contextlib.timeout(2):
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_freq = psutil.cpu_freq()
            
        # Memory info
        memory = psutil.virtual_memory()
        memory_info = {
            "total": memory.total,
            "available": memory.available,
            "used": memory.used,
            "percent": memory.percent
        }
        
        # Disk info with proper cleanup
        disk = None
        try:
            disk = psutil.disk_usage('/')
            disk_info = {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent
            }
        finally:
            del disk
            
        metrics = {
            "cpu": {
                "percent": cpu_percent,
                "count": psutil.cpu_count(),
                "frequency": cpu_freq._asdict() if cpu_freq else {}
            },
            "memory": memory_info,
            "disk": disk_info,
            "network": safe_collect_network_info(),
            "timestamp": datetime.now().isoformat()
        }
    except TimeoutError:
        return {"error": "Metrics collection timed out"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        # Ensure proper cleanup of large objects
        if 'memory' in locals():
            del memory
        if 'cpu_freq' in locals():
            del cpu_freq
            
    return metrics

def get_process_metrics() -> Dict:
    """Get process-specific metrics with proper resource management"""
    try:
        with managed_process_info() as current_process:
            metrics = {
                "pid": current_process.pid,
                "status": current_process.status(),
                "metrics": {}
            }
            
            # Enable oneshot mode to prevent multiple system calls
            current_process.oneshot()
            
            # CPU metrics with error handling
            try:
                with contextlib.timeout(2):
                    cpu_times = current_process.cpu_times()
                    metrics["metrics"]["cpu"] = {
                        "percent": current_process.cpu_percent(),
                        "num_threads": current_process.num_threads(),
                        "cpu_times": cpu_times._asdict() if cpu_times else {}
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, TimeoutError) as e:
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
            
            # File descriptors with error handling and proper cleanup
            try:
                with contextlib.timeout(2):
                    open_files = current_process.open_files()
                    metrics["metrics"]["files"] = {
                        "open_count": len(open_files),
                        "files": [f.path for f in open_files]
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, TimeoutError) as e:
                metrics["metrics"]["files"] = {"error": str(e)}
            
            # Network connections with error handling and proper cleanup
            try:
                with contextlib.timeout(2):
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
            except (psutil.NoSuchProcess, psutil.AccessDenied, TimeoutError) as e:
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
    """Get all monitoring metrics with proper cleanup"""
    try:
        system_metrics = get_system_metrics()
        process_metrics = get_process_metrics()
        
        return jsonify({
            "system": system_metrics,
            "process": process_metrics
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get metrics: {str(e)}"}), 500
    finally:
        # Force garbage collection after heavy metrics collection
        import gc
        gc.collect()

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