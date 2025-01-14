import logging
import libvirt
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

class MigrationError(Exception):
    """Base exception for migration-related errors"""
    pass

class MigrationType(Enum):
    """Types of migration supported"""
    DIRECT = "direct"
    PEER_TO_PEER = "p2p"
    TUNNELED = "tunneled"
    OFFLINE = "offline"

class MigrationStatus(Enum):
    """Status of a migration operation"""
    PREPARING = "preparing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class MigrationConfig:
    """Configuration for VM migration"""
    vm_name: str
    destination_uri: str
    migration_type: MigrationType = MigrationType.DIRECT
    bandwidth: Optional[int] = None
    max_downtime: Optional[int] = None
    compressed: bool = True
    auto_converge: bool = True
    persistent: bool = True
    undefine_source: bool = False
    offline: bool = False
    timeout: Optional[int] = None
    listen_address: Optional[str] = None
    graphics_uri: Optional[str] = None
    disks_to_migrate: Optional[List[str]] = None

@dataclass
class MigrationStats:
    """Statistics about an ongoing or completed migration"""
    status: MigrationStatus
    progress: float
    data_total: int
    data_processed: int
    data_remaining: int
    downtime: int
    speed: int
    comp_cache: Optional[int] = None
    comp_bytes: Optional[int] = None
    comp_pages: Optional[int] = None
    comp_cache_misses: Optional[int] = None

class MigrationManager:
    def __init__(self, conn: libvirt.virConnect):
        self.conn = conn
        self.active_migrations: Dict[str, MigrationStats] = {}

    def _get_domain(self, vm_name: str) -> libvirt.virDomain:
        """Get libvirt domain object for a VM."""
        try:
            domain = self.conn.lookupByName(vm_name)
            if not domain:
                raise MigrationError(f"VM {vm_name} not found")
            return domain
        except libvirt.libvirtError as e:
            raise MigrationError(f"Failed to get VM {vm_name}: {e}")

    def _prepare_migration_flags(self, config: MigrationConfig) -> int:
        """Prepare migration flags based on configuration."""
        flags = 0

        if not config.offline:
            flags |= libvirt.VIR_MIGRATE_LIVE

        if config.persistent:
            flags |= libvirt.VIR_MIGRATE_PERSIST_DEST

        if config.undefine_source:
            flags |= libvirt.VIR_MIGRATE_UNDEFINE_SOURCE

        if config.compressed:
            flags |= libvirt.VIR_MIGRATE_COMPRESSED

        if config.auto_converge:
            flags |= libvirt.VIR_MIGRATE_AUTO_CONVERGE

        if config.migration_type == MigrationType.PEER_TO_PEER:
            flags |= libvirt.VIR_MIGRATE_PEER2PEER
        elif config.migration_type == MigrationType.TUNNELED:
            flags |= libvirt.VIR_MIGRATE_TUNNELLED
        
        flags |= libvirt.VIR_MIGRATE_CHANGE_PROTECTION

        return flags

    def start_migration(self, config: MigrationConfig) -> None:
        """Start migration of a VM to another host."""
        try:
            domain = self._get_domain(config.vm_name)
            flags = self._prepare_migration_flags(config)

            self.active_migrations[config.vm_name] = MigrationStats(
                status=MigrationStatus.PREPARING,
                progress=0.0,
                data_total=0,
                data_processed=0,
                data_remaining=0,
                downtime=0,
                speed=0
            )

            if config.bandwidth:
                domain.migrateSetMaxSpeed(config.bandwidth)
            if config.max_downtime:
                domain.migrateSetMaxDowntime(config.max_downtime, 0)

            logger.info(f"Starting {config.migration_type.value} migration of VM {config.vm_name} to {config.destination_uri}")
            self.active_migrations[config.vm_name].status = MigrationStatus.IN_PROGRESS

            params = {}
            if config.listen_address:
                params['listen_address'] = config.listen_address
            if config.graphics_uri:
                params['graphics_uri'] = config.graphics_uri
            if config.disks_to_migrate:
                params['migrate_disks'] = config.disks_to_migrate

            if config.migration_type in [MigrationType.DIRECT, MigrationType.OFFLINE]:
                dest_conn = libvirt.open(config.destination_uri)
                if not dest_conn:
                    raise MigrationError(f"Failed to connect to destination host {config.destination_uri}")
                
                if config.timeout:
                    domain.migrateToURI3(
                        config.destination_uri,
                        params,
                        flags,
                        config.timeout * 1000
                    )
                else:
                    domain.migrateToURI3(config.destination_uri, params, flags)

            elif config.migration_type == MigrationType.PEER_TO_PEER:
                domain.migrate3(None, params, flags)

            elif config.migration_type == MigrationType.TUNNELED:
                tunnel_uri = f"qemu+ssh://{config.destination_uri}/system"
                domain.migrateToURI3(tunnel_uri, params, flags)

            self.active_migrations[config.vm_name].status = MigrationStatus.COMPLETED
            logger.info(f"Successfully migrated VM {config.vm_name}")

        except libvirt.libvirtError as e:
            logger.error(f"Migration failed for VM {config.vm_name}: {e}")
            if config.vm_name in self.active_migrations:
                self.active_migrations[config.vm_name].status = MigrationStatus.FAILED
            raise MigrationError(f"Migration failed: {e}")

    def cancel_migration(self, vm_name: str) -> None:
        """Cancel an ongoing migration."""
        try:
            domain = self._get_domain(vm_name)
            if vm_name not in self.active_migrations:
                raise MigrationError(f"No active migration for VM {vm_name}")

            domain.abortJob()
            self.active_migrations[vm_name].status = MigrationStatus.CANCELLED
            logger.info(f"Cancelled migration for VM {vm_name}")

        except libvirt.libvirtError as e:
            logger.error(f"Failed to cancel migration for VM {vm_name}: {e}")
            raise MigrationError(f"Failed to cancel migration: {e}")

    def get_migration_status(self, vm_name: str) -> Optional[MigrationStats]:
        """Get current migration status and statistics."""
        if vm_name not in self.active_migrations:
            return None

        try:
            domain = self._get_domain(vm_name)
            if self.active_migrations[vm_name].status == MigrationStatus.IN_PROGRESS:
                info = domain.migrateGetInfo()
                
                stats = self.active_migrations[vm_name]
                stats.data_processed = info[0]
                stats.data_remaining = info[1]
                stats.data_total = info[0] + info[1]
                stats.speed = info[2]
                stats.downtime = info[3]
                
                try:
                    comp_stats = domain.migrateGetCompressionCache()
                    stats.comp_cache = comp_stats[0]
                    stats.comp_bytes = comp_stats[1]
                    stats.comp_pages = comp_stats[2]
                    stats.comp_cache_misses = comp_stats[3]
                except libvirt.libvirtError:
                    pass

                if stats.data_total > 0:
                    stats.progress = (stats.data_processed / stats.data_total) * 100

            return self.active_migrations[vm_name]

        except libvirt.libvirtError as e:
            logger.error(f"Failed to get migration status for VM {vm_name}: {e}")
            raise MigrationError(f"Failed to get migration status: {e}")

    def set_migration_speed(self, vm_name: str, bandwidth: int) -> None:
        """Set migration bandwidth limit in MiB/s."""
        try:
            domain = self._get_domain(vm_name)
            domain.migrateSetMaxSpeed(bandwidth)
            logger.info(f"Set migration speed for VM {vm_name} to {bandwidth} MiB/s")
        except libvirt.libvirtError as e:
            raise MigrationError(f"Failed to set migration speed: {e}")

    def set_migration_downtime(self, vm_name: str, downtime_ms: int) -> None:
        """Set maximum allowed downtime for migration in milliseconds."""
        try:
            domain = self._get_domain(vm_name)
            domain.migrateSetMaxDowntime(downtime_ms, 0)
            logger.info(f"Set migration downtime for VM {vm_name} to {downtime_ms}ms")
        except libvirt.libvirtError as e:
            raise MigrationError(f"Failed to set migration downtime: {e}")

    def set_compression_cache(self, vm_name: str, cache_size: int) -> None:
        """Set size of compression cache in bytes."""
        try:
            domain = self._get_domain(vm_name)
            domain.migrateSetCompressionCache(cache_size)
            logger.info(f"Set compression cache size for VM {vm_name} to {cache_size} bytes")
        except libvirt.libvirtError as e:
            raise MigrationError(f"Failed to set compression cache size: {e}")

    def list_migrations(self) -> List[Dict]:
        """List all active and recent migrations."""
        return [
            {
                "vm_name": vm_name,
                "status": stats.status.value,
                "progress": stats.progress,
                "speed": stats.speed,
                "downtime": stats.downtime,
                "compression_stats": {
                    "cache_size": stats.comp_cache,
                    "compressed_bytes": stats.comp_bytes,
                    "compressed_pages": stats.comp_pages,
                    "cache_misses": stats.comp_cache_misses
                } if stats.comp_cache is not None else None
            }
            for vm_name, stats in self.active_migrations.items()
        ] 