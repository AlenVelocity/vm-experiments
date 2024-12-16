#!/bin/bash

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[✓]${NC} $1"
}

error() {
    echo -e "${RED}[✗] Error: $1${NC}"
    exit 1
}

check_homebrew() {
    if ! command -v brew &> /dev/null; then
        echo "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> /Users/$USER/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    log "Homebrew installed and configured"
}

install_utm() {
    brew install --cask utm || error "Failed to install UTM"
    log "UTM installed successfully"
}

download_ubuntu_iso() {
    mkdir -p ~/Downloads/ubuntu-kvm
    cd ~/Downloads/ubuntu-kvm

    UBUNTU_ISO_URL="https://cdimage.ubuntu.com/releases/20.04/release/ubuntu-20.04.5-live-server-arm64.iso"
    
    echo "Downloading Ubuntu ISO from: $UBUNTU_ISO_URL"
    curl -L -o ubuntu-20.04.5-live-server-arm64.iso "$UBUNTU_ISO_URL" || error "Failed to download Ubuntu ISO"
    
    log "Ubuntu 20.04.5 ARM64 Server ISO downloaded"
}

setup_python_env() {
    brew install pkg-config libvirt
    
    brew install python
    
    python3 -m venv ~/kvmenv
    source ~/kvmenv/bin/activate
    
    pip install libvirt-python requests
    
    log "Python virtual environment created"
}

create_kvm_script() {
    cat > ~/kvm_cloud_manager.py << 'EOL'
import libvirt
import uuid
import subprocess
import json
import os
from typing import Dict, List

class KVMCloudManager:
    def __init__(self, uri='qemu:///system'):
        try:
            self.conn = libvirt.open(uri)
            if not self.conn:
                raise Exception("Failed to open connection to KVM hypervisor")
        except Exception as e:
            print(f"Libvirt connection error: {e}")
            raise

    def create_vm(self, 
                   name: str, 
                   memory_mb: int = 4096, 
                   vcpus: int = 2, 
                   disk_gb: int = 50) -> Dict[str, str]:
        vm_uuid = str(uuid.uuid4())
        disk_path = f'/tmp/{name}.qcow2'
        
        subprocess.run([
            'qemu-img', 'create', 
            '-f', 'qcow2', 
            disk_path, 
            f'{disk_gb}G'
        ], check=True)

        vm_xml = f'''
        <domain type='kvm'>
          <name>{name}</name>
          <uuid>{vm_uuid}</uuid>
          <memory unit='MiB'>{memory_mb}</memory>
          <vcpu placement='static'>{vcpus}</vcpu>
          <os>
            <type arch='aarch64'>hvm</type>
            <boot dev='hd'/>
          </os>
          <devices>
            <disk type='file' device='disk'>
              <driver name='qemu' type='qcow2'/>
              <source file='{disk_path}'/>
              <target dev='vda' bus='virtio'/>
            </disk>
            <interface type='network'>
              <source network='default'/>
              <model type='virtio'/>
            </interface>
          </devices>
        </domain>
        '''
        
        try:
            domain = self.conn.createXML(vm_xml, 0)
            return {
                "name": name,
                "uuid": vm_uuid,
                "memory": f"{memory_mb} MB",
                "vcpus": vcpus,
                "disk_path": disk_path
            }
        except Exception as e:
            print(f"VM creation error: {e}")
            return {}

    def list_vms(self) -> List[Dict[str, str]]:
        domains = self.conn.listAllDomains()
        return [
            {
                "name": domain.name(),
                "uuid": domain.UUIDString(),
                "state": domain.state()[0]
            } for domain in domains
        ]

def main():
    try:
        cloud_manager = KVMCloudManager()
        
        test_vms = [
            {"name": "test-vm-1", "memory": 4096, "vcpus": 2},
            {"name": "test-vm-2", "memory": 2048, "vcpus": 1}
        ]
        
        created_vms = []
        for vm_config in test_vms:
            vm = cloud_manager.create_vm(**vm_config)
            created_vms.append(vm)
        
        print("Created VMs:")
        print(json.dumps(created_vms, indent=2))
        
        print("\nCurrent VMs:")
        current_vms = cloud_manager.list_vms()
        print(json.dumps(current_vms, indent=2))
    
    except Exception as e:
        print(f"Cloud infrastructure creation failed: {e}")

if __name__ == '__main__':
    main()
EOL

    chmod +x ~/kvm_cloud_manager.py
    log "KVM Cloud Manager script created"
}

alternative_ubuntu_download() {
    ALTERNATE_URLS=(
        "https://cdimage.ubuntu.com/releases/20.04/release/ubuntu-20.04.5-live-server-arm64.iso"
        "https://old-releases.ubuntu.com/releases/20.04/ubuntu-20.04.5-live-server-arm64.iso"
    )

    mkdir -p ~/Downloads/ubuntu-kvm
    cd ~/Downloads/ubuntu-kvm

    for url in "${ALTERNATE_URLS[@]}"; do
        echo "Trying to download from: $url"
        if curl -L -o ubuntu-20.04-arm64-server.iso "$url"; then
            log "Successfully downloaded Ubuntu ISO"
            return 0
        else
            echo "Download failed from $url"
        fi
    done

    error "Could not download Ubuntu ISO from any source"
}

main_setup() {
    echo -e "${YELLOW}Starting M1 Mac KVM Cloud Infrastructure Setup${NC}"
    
    check_homebrew
    
    install_utm
    
    if ! download_ubuntu_iso; then
        echo "Primary download failed. Trying alternative method."
        alternative_ubuntu_download
    fi
    
    setup_python_env
    
    create_kvm_script
    
    echo -e "${GREEN}
    =============================================
    🚀 KVM Cloud Infrastructure Setup Complete! 
    =============================================
    
    Next steps:
    1. Open UTM and create a new VM using the downloaded ISO
    2. Activate Python venv: source ~/kvmenv/bin/activate
    3. Run KVM script: python ~/kvm_cloud_manager.py
    ${NC}"
}

main_setup