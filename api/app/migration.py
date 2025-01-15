import logging
import libvirt
from dataclasses import dataclass
from typing import Optional, Dict, List
from enum import Enum

logger = logging.getLogger(__name__)

class MigrationError(Exception):
    pass

class MigrationType(Enum):
    DIRECT = "direct"
    TUNNELED = "tunneled"

class MigrationStatus(Enum):
    PREPARING = "preparing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class MigrationConfig:
    vm_name: str
    destination_uri: str
    migration_type: MigrationType = MigrationType.DIRECT
    bandwidth: Optional[int] = None
    max_downtime: Optional[int] = None
    compressed: bool = True

@dataclass
class MigrationStats:
    status: MigrationStatus
    progress: float
    data_total: int
    data_processed: int
    data_remaining: int
    speed: int

class MigrationManager:
    def __init__(self, conn: libvirt.virConnect):
        self.conn = conn
        self.active_migrations: Dict[str, MigrationStats] = {}

    def _get_domain(self, vm_name: str) -> libvirt.virDomain:
        try:
            domain = self.conn.lookupByName(vm_name)
            if not domain:
                raise MigrationError(f"VM {vm_name} not found")
            return domain
        except libvirt.libvirtError as e:
            raise MigrationError(f"Failed to get VM {vm_name}: {e}")

    def start_migration(self, config: MigrationConfig) -> None:
        try:
            domain = self._get_domain(config.vm_name)
            if domain.state()[0] not in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
                raise MigrationError(f"VM {config.vm_name} must be running or paused to migrate")

            if config.vm_name in self.active_migrations:
                raise MigrationError(f"Migration already in progress for VM {config.vm_name}")

            flags = libvirt.VIR_MIGRATE_LIVE | libvirt.VIR_MIGRATE_PERSIST_DEST
            if config.compressed:
                flags |= libvirt.VIR_MIGRATE_COMPRESSED

            self.active_migrations[config.vm_name] = MigrationStats(
                status=MigrationStatus.PREPARING,
                progress=0.0,
                data_total=0,
                data_processed=0,
                data_remaining=0,
                speed=0
            )

            if config.bandwidth:
                domain.migrateSetMaxSpeed(config.bandwidth)
            if config.max_downtime:
                domain.migrateSetMaxDowntime(config.max_downtime, 0)

            logger.info(f"Starting {config.migration_type.value} migration of VM {config.vm_name}")
            self.active_migrations[config.vm_name].status = MigrationStatus.IN_PROGRESS

            if config.migration_type == MigrationType.DIRECT:
                dest_conn = libvirt.open(config.destination_uri)
                if not dest_conn:
                    raise MigrationError(f"Failed to connect to destination host {config.destination_uri}")
                try:
                    domain.migrateToURI3(config.destination_uri, {}, flags)
                finally:
                    dest_conn.close()
            else:
                tunnel_uri = f"qemu+ssh://{config.destination_uri}/system"
                domain.migrateToURI3(tunnel_uri, {}, flags)

            self.active_migrations[config.vm_name].status = MigrationStatus.COMPLETED
            logger.info(f"Successfully migrated VM {config.vm_name}")

        except Exception as e:
            logger.error(f"Migration failed for VM {config.vm_name}: {e}")
            if config.vm_name in self.active_migrations:
                self.active_migrations[config.vm_name].status = MigrationStatus.FAILED
            raise MigrationError(f"Migration failed: {e}")

    def cancel_migration(self, vm_name: str) -> None:
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
        if vm_name not in self.active_migrations:
            return None

        try:
            domain = self._get_domain(vm_name)
            job_info = domain.jobInfo()
            
            if job_info[0] == libvirt.VIR_DOMAIN_JOB_NONE:
                # Migration completed
                status = self.active_migrations[vm_name].status
                if status not in [MigrationStatus.COMPLETED, MigrationStatus.FAILED, MigrationStatus.CANCELLED]:
                    status = MigrationStatus.COMPLETED
                return MigrationStats(
                    status=status,
                    progress=100.0 if status == MigrationStatus.COMPLETED else 0.0,
                    data_total=0,
                    data_processed=0,
                    data_remaining=0,
                    speed=0
                )

            stats = self.active_migrations[vm_name]
            stats.data_processed = job_info[3]  # data processed
            stats.data_remaining = job_info[4]  # data remaining
            stats.data_total = job_info[3] + job_info[4]
            stats.speed = job_info[5]  # bandwidth

            if stats.data_total > 0:
                stats.progress = (stats.data_processed / stats.data_total) * 100.0

            return stats

        except libvirt.libvirtError as e:
            logger.error(f"Failed to get migration status for VM {vm_name}: {e}")
            raise MigrationError(f"Failed to get migration status: {e}")

    def list_migrations(self) -> List[Dict]:
        return [
            {
                "vm_name": vm_name,
                "status": stats.status.value,
                "progress": stats.progress,
                "speed": stats.speed
            }
            for vm_name, stats in self.active_migrations.items()
        ] 