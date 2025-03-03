import libvirt
import logging

logger = logging.getLogger(__name__)

def get_libvirt_connection():
    """Initialize and return a libvirt connection."""
    try:
        conn = libvirt.open('qemu:///system')
        if conn is None:
            raise Exception('Failed to connect to QEMU/KVM')
        return conn
    except libvirt.libvirtError as e:
        logger.error(f"Fai  led to connect to libvirt: {e}")
        raise Exception(f"Failed to connect to libvirt: {e}") 