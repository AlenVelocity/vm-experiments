import libvirt
import uuid
import json
import subprocess
from typing import Dict, List, Optional

class CloudInfrastructureManager:
    def __init__(self, connection_uri='qemu:///system'):
        self.conn = libvirt.open(connection_uri)
        if not self.conn:
            raise Exception("Failed to open connection to KVM hypervisor")

    def create_virtual_machine(
        self, 
        name: str, 
        memory_mb: int = 2048, 
        vcpus: int = 2, 
        disk_gb: int = 20,
        image_path: str = '/var/lib/libvirt/images/base-image.qcow2'
    ) -> Dict[str, str]:
        vm_uuid = str(uuid.uuid4())
        xml_config = f'''
        <domain type='kvm'>
            <name>{name}</name>
            <uuid>{vm_uuid}</uuid>
            <memory unit='MiB'>{memory_mb}</memory>
            <vcpu placement='static'>{vcpus}</vcpu>
            <os>
                <type arch='x86_64'>hvm</type>
            </os>
            <devices>
                <disk type='file' device='disk'>
                    <driver name='qemu' type='qcow2'/>
                    <source file='{image_path}'/>
                    <target dev='vda' bus='virtio'/>
                </disk>
                <interface type='network'>
                    <source network='default'/>
                </interface>
            </devices>
        </domain>
        '''
        
        domain = self.conn.createXML(xml_config, 0)
        return {
            "name": name,
            "uuid": vm_uuid,
            "memory": f"{memory_mb} MB",
            "vcpus": vcpus
        }

    def list_instances(self) -> List[Dict[str, str]]:
        domains = self.conn.listAllDomains()
        return [
            {
                "name": domain.name(),
                "uuid": domain.UUIDString(),
                "state": domain.state()[0]
            } for domain in domains
        ]

    def delete_instance(self, name_or_uuid: str) -> bool:
        try:
            domain = (
                self.conn.lookupByName(name_or_uuid) 
                or self.conn.lookupByUUIDString(name_or_uuid)
            )
            domain.destroy()
            domain.undefine()
            return True
        except Exception as e:
            print(f"Error deleting instance: {e}")
            return False

    def __del__(self):
        if self.conn:
            self.conn.close()

def main():
    cloud_manager = CloudInfrastructureManager()
    
    new_vm = cloud_manager.create_virtual_machine(
        name='web-server-01', 
        memory_mb=4096, 
        vcpus=4
    )
    print("Created VM:", json.dumps(new_vm, indent=2))

    instances = cloud_manager.list_instances()
    print("Current Instances:", json.dumps(instances, indent=2))

if __name__ == '__main__':
    main()