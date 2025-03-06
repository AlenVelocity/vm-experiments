import os
import time
import json
import logging
import threading
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path
import datetime

from app.server_manager import ServerManager, Server
from app.cluster_vm_manager import ClusterVMManager
from app.cluster_network_manager import ClusterNetworkManager
from app.cluster_storage_manager import ClusterStorageManager

logger = logging.getLogger(__name__)

class ClusterMonitoringError(Exception):
    """Error related to cluster monitoring operations."""
    pass

class Alert:
    """Represents an alert generated from monitoring."""
    SEVERITY_INFO = "info"
    SEVERITY_WARNING = "warning"
    SEVERITY_ERROR = "error"
    SEVERITY_CRITICAL = "critical"
    
    def __init__(
        self,
        id: str,
        title: str,
        message: str,
        severity: str,
        resource_type: str,
        resource_id: str,
        timestamp: float = None
    ):
        self.id = id
        self.title = title
        self.message = message
        self.severity = severity
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.timestamp = timestamp or time.time()
        self.acknowledged = False
        self.resolved = False
        self.resolved_at = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "severity": self.severity,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "timestamp": self.timestamp,
            "acknowledged": self.acknowledged,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Alert':
        """Create from dictionary."""
        alert = cls(
            id=data["id"],
            title=data["title"],
            message=data["message"],
            severity=data["severity"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            timestamp=data.get("timestamp", time.time())
        )
        alert.acknowledged = data.get("acknowledged", False)
        alert.resolved = data.get("resolved", False)
        alert.resolved_at = data.get("resolved_at")
        return alert

class ClusterMonitoring:
    """
    Manager for monitoring and logging across the cluster.
    This includes:
    - Resource monitoring for servers, VMs, networks, and storage
    - Alerting for critical events
    - Centralized logging
    - Metrics collection and aggregation
    """
    
    def __init__(
        self,
        server_manager: ServerManager,
        vm_manager: ClusterVMManager,
        network_manager: ClusterNetworkManager,
        storage_manager: ClusterStorageManager,
        config_path: str = "data/monitoring_config.json"
    ):
        """Initialize the cluster monitoring manager."""
        self.server_manager = server_manager
        self.vm_manager = vm_manager
        self.network_manager = network_manager
        self.storage_manager = storage_manager
        self.config_path = Path(config_path)
        self.alerts: Dict[str, Alert] = {}
        self.metrics_history: Dict[str, List[Dict]] = {
            "servers": [],
            "vms": [],
            "networks": [],
            "storage": []
        }
        self.alert_callbacks: List[Callable[[Alert], None]] = []
        self.monitoring_config = self._load_monitoring_config()
        self.monitoring_thread = None
        self.monitoring_active = False
        self._load_alerts()
    
    def _load_monitoring_config(self) -> Dict:
        """Load monitoring configuration from file."""
        default_config = {
            "collection_interval_seconds": 60,
            "metrics_retention_days": 7,
            "alert_thresholds": {
                "server_cpu_usage": 90,
                "server_memory_usage": 90,
                "server_disk_usage": 90,
                "vm_cpu_usage": 90,
                "vm_memory_usage": 90,
                "vm_disk_usage": 90,
                "network_bandwidth_usage": 90,
                "storage_usage": 90
            },
            "enabled_monitors": {
                "server": True,
                "vm": True,
                "network": True,
                "storage": True
            }
        }
        
        try:
            if self.config_path.exists():
                with open(self.config_path, "r") as f:
                    loaded_config = json.load(f)
                
                for key, value in loaded_config.items():
                    if key in default_config and isinstance(value, dict) and isinstance(default_config[key], dict):
                        default_config[key].update(value)
                    else:
                        default_config[key] = value
            else:
                logger.info(f"No monitoring config found at {self.config_path}, using defaults")
                self._save_monitoring_config(default_config)
        except Exception as e:
            logger.error(f"Error loading monitoring config: {e}")
        
        return default_config
    
    def _save_monitoring_config(self, config: Dict) -> None:
        """Save monitoring configuration to file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving monitoring config: {e}")
    
    def _load_alerts(self) -> None:
        """Load alerts from file."""
        alerts_path = Path("data/alerts.json")
        try:
            if alerts_path.exists():
                with open(alerts_path, "r") as f:
                    alerts_data = json.load(f)
                
                for alert_id, data in alerts_data.items():
                    self.alerts[alert_id] = Alert.from_dict(data)
            else:
                logger.info("No alerts file found, creating new one")
                self.alerts = {}
        except Exception as e:
            logger.error(f"Error loading alerts: {e}")
            self.alerts = {}
    
    def _save_alerts(self) -> None:
        """Save alerts to file."""
        alerts_path = Path("data/alerts.json")
        try:
            alerts_path.parent.mkdir(parents=True, exist_ok=True)
            
            alerts_data = {}
            for alert_id, alert in self.alerts.items():
                alerts_data[alert_id] = alert.to_dict()
            
            with open(alerts_path, "w") as f:
                json.dump(alerts_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving alerts: {e}")
    
    def start_monitoring(self) -> None:
        """Start the monitoring thread."""
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            logger.warning("Monitoring already active")
            return
        
        self.monitoring_active = True
        self.monitoring_thread = threading.Thread(target=self._monitoring_loop)
        self.monitoring_thread.daemon = True
        self.monitoring_thread.start()
        logger.info("Started cluster monitoring")
    
    def stop_monitoring(self) -> None:
        """Stop the monitoring thread."""
        self.monitoring_active = False
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=10)
            logger.info("Stopped cluster monitoring")
    
    def _monitoring_loop(self) -> None:
        """Main monitoring loop that runs in a separate thread."""
        while self.monitoring_active:
            try:
                self._collect_all_metrics()
                
                self._check_alert_conditions()
                
                self._cleanup_old_metrics()
                
                collection_interval = self.monitoring_config.get("collection_interval_seconds", 60)
                time.sleep(collection_interval)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(10)
    
    def _collect_all_metrics(self) -> None:
        """Collect metrics from all components."""
        timestamp = time.time()
        
        if self.monitoring_config["enabled_monitors"]["server"]:
            try:
                server_metrics = self._collect_server_metrics()
                server_metrics["timestamp"] = timestamp
                self.metrics_history["servers"].append(server_metrics)
            except Exception as e:
                logger.error(f"Error collecting server metrics: {e}")
        
        if self.monitoring_config["enabled_monitors"]["vm"]:
            try:
                vm_metrics = self._collect_vm_metrics()
                vm_metrics["timestamp"] = timestamp
                self.metrics_history["vms"].append(vm_metrics)
            except Exception as e:
                logger.error(f"Error collecting VM metrics: {e}")
        
        if self.monitoring_config["enabled_monitors"]["network"]:
            try:
                network_metrics = self._collect_network_metrics()
                network_metrics["timestamp"] = timestamp
                self.metrics_history["networks"].append(network_metrics)
            except Exception as e:
                logger.error(f"Error collecting network metrics: {e}")
        
        if self.monitoring_config["enabled_monitors"]["storage"]:
            try:
                storage_metrics = self._collect_storage_metrics()
                storage_metrics["timestamp"] = timestamp
                self.metrics_history["storage"].append(storage_metrics)
            except Exception as e:
                logger.error(f"Error collecting storage metrics: {e}")
    
    def _collect_server_metrics(self) -> Dict:
        """Collect metrics from all servers."""
        metrics = {
            "servers": [],
            "total_servers": 0,
            "online_servers": 0,
            "offline_servers": 0,
            "total_cpu_cores": 0,
            "total_memory_mb": 0,
            "total_disk_gb": 0,
            "used_cpu_cores": 0,
            "used_memory_mb": 0,
            "used_disk_gb": 0
        }
        
        for server in self.server_manager.list_servers():
            try:
                self.server_manager.update_server_status(server.id)
            except Exception as e:
                logger.error(f"Error updating status for server {server.id}: {e}")
        
        for server in self.server_manager.list_servers():
            metrics["total_servers"] += 1
            
            if server.status == "online":
                metrics["online_servers"] += 1
                metrics["total_cpu_cores"] += server.cpu_cores
                metrics["total_memory_mb"] += server.memory_mb
                metrics["total_disk_gb"] += server.disk_gb
                
                if server.metrics_history:
                    latest_metrics = server.metrics_history[-1]
                    used_cpu = server.cpu_cores * (latest_metrics.cpu_usage / 100)
                    metrics["used_cpu_cores"] += used_cpu
                    metrics["used_memory_mb"] += latest_metrics.memory_used
                    metrics["used_disk_gb"] += latest_metrics.disk_used
                    
                    metrics["servers"].append({
                        "id": server.id,
                        "name": server.name,
                        "status": server.status,
                        "cpu_cores": server.cpu_cores,
                        "memory_mb": server.memory_mb,
                        "disk_gb": server.disk_gb,
                        "cpu_usage": latest_metrics.cpu_usage,
                        "memory_usage": latest_metrics.memory_used,
                        "disk_usage": latest_metrics.disk_used,
                        "network_rx": latest_metrics.network_rx,
                        "network_tx": latest_metrics.network_tx
                    })
            else:
                metrics["offline_servers"] += 1
                metrics["servers"].append({
                    "id": server.id,
                    "name": server.name,
                    "status": server.status,
                    "cpu_cores": server.cpu_cores,
                    "memory_mb": server.memory_mb,
                    "disk_gb": server.disk_gb
                })
        
        return metrics
    
    def _collect_vm_metrics(self) -> Dict:
        """Collect metrics from all VMs."""
        metrics = {
            "vms": [],
            "total_vms": 0,
            "running_vms": 0,
            "stopped_vms": 0,
            "error_vms": 0,
            "total_allocated_cpu": 0,
            "total_allocated_memory": 0
        }
        
        vms = self.vm_manager.list_vms()
        metrics["total_vms"] = len(vms)
        
        for vm in vms:
            try:
                vm_status = self.vm_manager.get_vm_status(vm.id)
                
                if vm_status == "running":
                    metrics["running_vms"] += 1
                    vm_metrics = self.vm_manager.get_vm_metrics(vm.id)
                    
                    vm_data = {
                        "id": vm.id,
                        "name": vm.name,
                        "status": vm_status,
                        "cpu_cores": vm.config.cpu_cores,
                        "memory_mb": vm.config.memory_mb,
                        "cpu_usage": vm_metrics.get("cpu_usage", 0),
                        "memory_usage": vm_metrics.get("memory_usage", 0),
                        "disk_usage": vm_metrics.get("disk_usage", {}),
                        "network_usage": vm_metrics.get("network_usage", {})
                    }
                    
                    metrics["vms"].append(vm_data)
                    metrics["total_allocated_cpu"] += vm.config.cpu_cores
                    metrics["total_allocated_memory"] += vm.config.memory_mb
                elif vm_status == "stopped":
                    metrics["stopped_vms"] += 1
                    metrics["vms"].append({
                        "id": vm.id,
                        "name": vm.name,
                        "status": vm_status,
                        "cpu_cores": vm.config.cpu_cores,
                        "memory_mb": vm.config.memory_mb
                    })
                    metrics["total_allocated_cpu"] += vm.config.cpu_cores
                    metrics["total_allocated_memory"] += vm.config.memory_mb
                else:
                    metrics["error_vms"] += 1
                    metrics["vms"].append({
                        "id": vm.id,
                        "name": vm.name,
                        "status": vm_status,
                        "error_message": vm.error_message
                    })
            except Exception as e:
                logger.error(f"Error collecting metrics for VM {vm.id}: {e}")
                metrics["error_vms"] += 1
        
        return metrics
    
    def _collect_network_metrics(self) -> Dict:
        """Collect network metrics."""
        try:
            return self.network_manager.get_network_metrics()
        except Exception as e:
            logger.error(f"Error collecting network metrics: {e}")
            return {"error": str(e)}
    
    def _collect_storage_metrics(self) -> Dict:
        """Collect storage metrics."""
        try:
            return self.storage_manager.get_storage_metrics()
        except Exception as e:
            logger.error(f"Error collecting storage metrics: {e}")
            return {"error": str(e)}
    
    def _check_alert_conditions(self) -> None:
        """Check for conditions that should trigger alerts."""
        if self.monitoring_config["enabled_monitors"]["server"]:
            self._check_server_alerts()
        
        if self.monitoring_config["enabled_monitors"]["vm"]:
            self._check_vm_alerts()
        
        if self.monitoring_config["enabled_monitors"]["network"]:
            self._check_network_alerts()
        
        if self.monitoring_config["enabled_monitors"]["storage"]:
            self._check_storage_alerts()
    
    def _check_server_alerts(self) -> None:
        """Check for server alert conditions."""
        cpu_threshold = self.monitoring_config["alert_thresholds"]["server_cpu_usage"]
        memory_threshold = self.monitoring_config["alert_thresholds"]["server_memory_usage"]
        disk_threshold = self.monitoring_config["alert_thresholds"]["server_disk_usage"]
        
        # Check each server
        for server in self.server_manager.list_servers():
            # Skip offline servers
            if server.status != "online" or not server.metrics_history:
                continue
            
            latest_metrics = server.metrics_history[-1]
            
            # Check CPU usage
            if latest_metrics.cpu_usage >= cpu_threshold:
                self._create_alert(
                    title=f"High CPU usage on server {server.name}",
                    message=f"CPU usage is {latest_metrics.cpu_usage:.1f}%, which exceeds the threshold of {cpu_threshold}%",
                    severity=Alert.SEVERITY_WARNING if latest_metrics.cpu_usage < 95 else Alert.SEVERITY_ERROR,
                    resource_type="server",
                    resource_id=server.id
                )
            
            # Check memory usage
            memory_usage_pct = (latest_metrics.memory_used / server.memory_mb) * 100
            if memory_usage_pct >= memory_threshold:
                self._create_alert(
                    title=f"High memory usage on server {server.name}",
                    message=f"Memory usage is {memory_usage_pct:.1f}%, which exceeds the threshold of {memory_threshold}%",
                    severity=Alert.SEVERITY_WARNING if memory_usage_pct < 95 else Alert.SEVERITY_ERROR,
                    resource_type="server",
                    resource_id=server.id
                )
            
            # Check disk usage
            disk_usage_pct = (latest_metrics.disk_used / server.disk_gb) * 100
            if disk_usage_pct >= disk_threshold:
                self._create_alert(
                    title=f"High disk usage on server {server.name}",
                    message=f"Disk usage is {disk_usage_pct:.1f}%, which exceeds the threshold of {disk_threshold}%",
                    severity=Alert.SEVERITY_WARNING if disk_usage_pct < 95 else Alert.SEVERITY_ERROR,
                    resource_type="server",
                    resource_id=server.id
                )
    
    def _check_vm_alerts(self) -> None:
        """Check for VM alert conditions."""
        # Get thresholds
        cpu_threshold = self.monitoring_config["alert_thresholds"]["vm_cpu_usage"]
        memory_threshold = self.monitoring_config["alert_thresholds"]["vm_memory_usage"]
        disk_threshold = self.monitoring_config["alert_thresholds"]["vm_disk_usage"]
        
        # Check each VM
        for vm in self.vm_manager.list_vms():
            # Skip non-running VMs
            status = self.vm_manager.get_vm_status(vm.id)
            if status != "running":
                continue
            
            try:
                vm_metrics = self.vm_manager.get_vm_metrics(vm.id)
                
                # Check CPU usage
                cpu_usage = vm_metrics.get("cpu_usage", 0)
                if cpu_usage >= cpu_threshold:
                    self._create_alert(
                        title=f"High CPU usage on VM {vm.name}",
                        message=f"CPU usage is {cpu_usage:.1f}%, which exceeds the threshold of {cpu_threshold}%",
                        severity=Alert.SEVERITY_WARNING if cpu_usage < 95 else Alert.SEVERITY_ERROR,
                        resource_type="vm",
                        resource_id=vm.id
                    )
                
                # Check memory usage
                memory_usage = vm_metrics.get("memory_usage", 0)
                if memory_usage >= memory_threshold:
                    self._create_alert(
                        title=f"High memory usage on VM {vm.name}",
                        message=f"Memory usage is {memory_usage:.1f}%, which exceeds the threshold of {memory_threshold}%",
                        severity=Alert.SEVERITY_WARNING if memory_usage < 95 else Alert.SEVERITY_ERROR,
                        resource_type="vm",
                        resource_id=vm.id
                    )
                
                # Check disk usage
                for disk_name, disk_usage in vm_metrics.get("disk_usage", {}).items():
                    if disk_usage >= disk_threshold:
                        self._create_alert(
                            title=f"High disk usage on VM {vm.name}",
                            message=f"Disk {disk_name} usage is {disk_usage:.1f}%, which exceeds the threshold of {disk_threshold}%",
                            severity=Alert.SEVERITY_WARNING if disk_usage < 95 else Alert.SEVERITY_ERROR,
                            resource_type="vm",
                            resource_id=vm.id
                        )
            except Exception as e:
                logger.error(f"Error checking alerts for VM {vm.id}: {e}")
    
    def _check_network_alerts(self) -> None:
        """Check for network alert conditions."""
        # This is a placeholder - in a real implementation, this would check network bandwidth, latency, etc.
        pass
    
    def _check_storage_alerts(self) -> None:
        """Check for storage alert conditions."""
        # Get thresholds
        storage_threshold = self.monitoring_config["alert_thresholds"]["storage_usage"]
        
        # Get storage metrics
        storage_metrics = self.storage_manager.get_storage_metrics()
        
        # Check server storage usage
        for server_metric in storage_metrics.get("server_storage_metrics", []):
            disk_usage_pct = (server_metric["used_disk_gb"] / server_metric["total_disk_gb"]) * 100
            if disk_usage_pct >= storage_threshold:
                self._create_alert(
                    title=f"High storage usage on server {server_metric['server_name']}",
                    message=f"Storage usage is {disk_usage_pct:.1f}%, which exceeds the threshold of {storage_threshold}%",
                    severity=Alert.SEVERITY_WARNING if disk_usage_pct < 95 else Alert.SEVERITY_ERROR,
                    resource_type="storage",
                    resource_id=server_metric["server_id"]
                )
    
    def _create_alert(
        self,
        title: str,
        message: str,
        severity: str,
        resource_type: str,
        resource_id: str
    ) -> Alert:
        """Create a new alert."""
        import uuid
        
        # Check if a similar unresolved alert exists for the same resource
        for alert in self.alerts.values():
            if (
                alert.resource_type == resource_type and
                alert.resource_id == resource_id and
                alert.title == title and
                not alert.resolved
            ):
                # Similar alert already exists and is not resolved
                return alert
        
        # Create new alert
        alert_id = str(uuid.uuid4())[:8]
        alert = Alert(
            id=alert_id,
            title=title,
            message=message,
            severity=severity,
            resource_type=resource_type,
            resource_id=resource_id
        )
        
        # Store alert
        self.alerts[alert_id] = alert
        self._save_alerts()
        
        # Notify callbacks
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")
        
        logger.info(f"Created new alert: {title}")
        
        return alert
    
    def register_alert_callback(self, callback: Callable[[Alert], None]) -> None:
        """Register a callback function to be called when a new alert is created."""
        self.alert_callbacks.append(callback)
    
    def acknowledge_alert(self, alert_id: str) -> None:
        """Acknowledge an alert."""
        if alert_id not in self.alerts:
            raise ClusterMonitoringError(f"Alert {alert_id} not found")
        
        self.alerts[alert_id].acknowledged = True
        self._save_alerts()
    
    def resolve_alert(self, alert_id: str) -> None:
        """Resolve an alert."""
        if alert_id not in self.alerts:
            raise ClusterMonitoringError(f"Alert {alert_id} not found")
        
        self.alerts[alert_id].resolved = True
        self.alerts[alert_id].resolved_at = time.time()
        self._save_alerts()
    
    def list_alerts(self, include_resolved: bool = False) -> List[Dict]:
        """List all alerts, optionally including resolved alerts."""
        alerts = []
        for alert in self.alerts.values():
            if include_resolved or not alert.resolved:
                alerts.append(alert.to_dict())
        
        # Sort by timestamp, newest first
        alerts.sort(key=lambda a: a["timestamp"], reverse=True)
        
        return alerts
    
    def get_alert(self, alert_id: str) -> Dict:
        """Get an alert by ID."""
        if alert_id not in self.alerts:
            raise ClusterMonitoringError(f"Alert {alert_id} not found")
        
        return self.alerts[alert_id].to_dict()
    
    def _cleanup_old_metrics(self) -> None:
        """Clean up old metrics data beyond the retention period."""
        retention_days = self.monitoring_config.get("metrics_retention_days", 7)
        cutoff_time = time.time() - (retention_days * 86400)
        
        # Clean up metrics history
        for metric_type in self.metrics_history:
            self.metrics_history[metric_type] = [
                m for m in self.metrics_history[metric_type]
                if m.get("timestamp", 0) > cutoff_time
            ]
        
        # Clean up resolved alerts
        alerts_to_remove = []
        for alert_id, alert in self.alerts.items():
            if alert.resolved and alert.resolved_at and alert.resolved_at < cutoff_time:
                alerts_to_remove.append(alert_id)
        
        for alert_id in alerts_to_remove:
            del self.alerts[alert_id]
        
        if alerts_to_remove:
            self._save_alerts()
    
    def get_metrics(
        self,
        resource_type: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None
    ) -> List[Dict]:
        """Get metrics for a specific resource type within a time range."""
        if resource_type not in self.metrics_history:
            raise ClusterMonitoringError(f"Invalid resource type: {resource_type}")
        
        end_time = end_time or time.time()
        start_time = start_time or (end_time - 86400)  # Default to last 24 hours
        
        # Filter metrics by time range
        filtered_metrics = [
            m for m in self.metrics_history[resource_type]
            if start_time <= m.get("timestamp", 0) <= end_time
        ]
        
        # Sort by timestamp
        filtered_metrics.sort(key=lambda m: m.get("timestamp", 0))
        
        return filtered_metrics
    
    def get_server_logs(self, server_id: str, lines: int = 100) -> List[str]:
        """Get logs from a server."""
        try:
            # Get the server
            server = self.server_manager.get_server(server_id)
            
            # Get logs (e.g., /var/log/syslog)
            result = self.server_manager.execute_command(server_id, f"tail -n {lines} /var/log/syslog")
            
            if result["exit_code"] == 0:
                return result["output"].split("\n")
            else:
                raise ClusterMonitoringError(f"Error getting logs from server: {result['error']}")
        except Exception as e:
            logger.error(f"Error getting logs from server {server_id}: {e}")
            raise ClusterMonitoringError(f"Failed to get logs: {str(e)}")
    
    def get_vm_logs(self, vm_id: str, lines: int = 100) -> List[str]:
        """Get logs for a VM."""
        try:
            return self.vm_manager.get_vm_logs(vm_id, lines)
        except Exception as e:
            logger.error(f"Error getting logs for VM {vm_id}: {e}")
            raise ClusterMonitoringError(f"Failed to get VM logs: {str(e)}")
    
    def export_metrics_to_json(self, output_path: str) -> None:
        """Export all metrics to a JSON file."""
        try:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, "w") as f:
                json.dump(self.metrics_history, f, indent=2)
            
            logger.info(f"Exported metrics to {output_path}")
        except Exception as e:
            logger.error(f"Error exporting metrics: {e}")
            raise ClusterMonitoringError(f"Failed to export metrics: {str(e)}")
    
    def get_cluster_health(self) -> Dict:
        """Get overall cluster health status."""
        health = {
            "status": "healthy",  # healthy, degraded, critical
            "servers": {
                "total": 0,
                "online": 0,
                "offline": 0,
                "issues": []
            },
            "vms": {
                "total": 0,
                "running": 0,
                "stopped": 0,
                "error": 0,
                "issues": []
            },
            "storage": {
                "volumes": 0,
                "usage_percent": 0,
                "issues": []
            },
            "networks": {
                "vpcs": 0,
                "issues": []
            },
            "alerts": {
                "critical": 0,
                "error": 0,
                "warning": 0,
                "info": 0
            },
            "timestamp": time.time()
        }
        
        # Get server health
        for server in self.server_manager.list_servers():
            health["servers"]["total"] += 1
            if server.status == "online":
                health["servers"]["online"] += 1
            else:
                health["servers"]["offline"] += 1
                health["servers"]["issues"].append({
                    "server_id": server.id,
                    "server_name": server.name,
                    "issue": f"Server is {server.status}"
                })
        
        # Get VM health
        vms = self.vm_manager.list_vms()
        health["vms"]["total"] = len(vms)
        
        for vm in vms:
            status = self.vm_manager.get_vm_status(vm.id)
            if status == "running":
                health["vms"]["running"] += 1
            elif status == "stopped":
                health["vms"]["stopped"] += 1
            else:
                health["vms"]["error"] += 1
                health["vms"]["issues"].append({
                    "vm_id": vm.id,
                    "vm_name": vm.name,
                    "issue": f"VM is in {status} state"
                })
        
        # Get storage health
        storage_metrics = self.storage_manager.get_storage_metrics()
        health["storage"]["volumes"] = storage_metrics.get("total_volumes", 0)
        
        total_disk = 0
        used_disk = 0
        for server_metric in storage_metrics.get("server_storage_metrics", []):
            total_disk += server_metric["total_disk_gb"]
            used_disk += server_metric["used_disk_gb"]
        
        if total_disk > 0:
            health["storage"]["usage_percent"] = (used_disk / total_disk) * 100
            
            if health["storage"]["usage_percent"] >= 90:
                health["storage"]["issues"].append({
                    "issue": f"Cluster storage usage is high: {health['storage']['usage_percent']:.1f}%"
                })
        
        # Get network health
        network_metrics = self.network_manager.get_network_metrics()
        health["networks"]["vpcs"] = len(self.network_manager.list_overlay_networks())
        
        # Get alert counts
        for alert in self.alerts.values():
            if not alert.resolved:
                health["alerts"][alert.severity] += 1
        
        # Determine overall health status
        if health["alerts"]["critical"] > 0:
            health["status"] = "critical"
        elif health["alerts"]["error"] > 0 or health["servers"]["offline"] > 0:
            health["status"] = "degraded"
        
        return health 