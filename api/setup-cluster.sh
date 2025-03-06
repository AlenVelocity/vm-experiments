#!/bin/bash

# Exit on error
set -e

# Print usage information
function usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Set up the cluster environment for multi-server VM management"
    echo
    echo "Options:"
    echo "  -h, --help              Show this help message"
    echo "  -p, --primary           Set up as the primary server"
    echo "  -s, --secondary         Set up as a secondary server"
    echo "  -a, --api-hostname      Hostname/IP of the primary API server"
    echo "  -k, --ssh-key           Path to SSH key for inter-server communication"
    echo
    echo "Examples:"
    echo "  $0 --primary                     # Set up as the primary server"
    echo "  $0 --secondary --api-hostname 192.168.1.10  # Set up as a secondary server"
}

# Default values
SERVER_TYPE="primary"
API_HOSTNAME="localhost"
SSH_KEY_PATH="$HOME/.ssh/id_rsa"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            exit 0
            ;;
        -p|--primary)
            SERVER_TYPE="primary"
            shift
            ;;
        -s|--secondary)
            SERVER_TYPE="secondary"
            shift
            ;;
        -a|--api-hostname)
            API_HOSTNAME="$2"
            shift 2
            ;;
        -k|--ssh-key)
            SSH_KEY_PATH="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

echo "===== VM Experiments Cluster Setup ====="
echo "Server Type: ${SERVER_TYPE}"
echo "API Hostname: ${API_HOSTNAME}"
echo "SSH Key Path: ${SSH_KEY_PATH}"
echo

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root or with sudo privileges"
    exit 1
fi

# Check if SSH key exists
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
    echo "SSH key not found at ${SSH_KEY_PATH}"
    echo "Generating new SSH key..."
    ssh-keygen -t rsa -b 4096 -f "${SSH_KEY_PATH}" -N ""
    echo "SSH key generated"
fi

# Update package lists
echo "Updating package lists..."
apt-get update

# Install required packages
echo "Installing required packages..."
apt-get install -y \
    qemu-kvm \
    libvirt-daemon-system \
    libvirt-clients \
    bridge-utils \
    virtinst \
    libguestfs-tools \
    python3-pip \
    python3-venv \
    nginx \
    fail2ban \
    ntp \
    iptables-persistent

# Ensure libvirt is running
echo "Ensuring libvirt service is running..."
systemctl enable libvirtd
systemctl start libvirtd

# Enable IP forwarding
echo "Enabling IP forwarding..."
cat > /etc/sysctl.d/99-ip-forward.conf << EOF
net.ipv4.ip_forward=1
EOF
sysctl -p /etc/sysctl.d/99-ip-forward.conf

# Set up NAT for outbound connections
echo "Setting up NAT for outbound connections..."
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
netfilter-persistent save

# Create vm-experiments directories
echo "Creating vm-experiments directories..."
mkdir -p /var/lib/libvirt/images/vm-experiments
mkdir -p /var/lib/libvirt/qemu/channel/target
chown -R libvirt-qemu:kvm /var/lib/libvirt/images/vm-experiments
chmod -R 777 /var/lib/libvirt/images/vm-experiments

# Create data directory
mkdir -p /home/ubuntu/vm-experiments/data/vms
mkdir -p /home/ubuntu/vm-experiments/data/disks
mkdir -p /home/ubuntu/vm-experiments/data/networks
mkdir -p /home/ubuntu/vm-experiments/data/tmp

# Set permissions
chmod -R 777 /home/ubuntu/vm-experiments/data

# Set up server-specific configurations
if [[ "${SERVER_TYPE}" == "primary" ]]; then
    echo "Setting up primary server..."
    
    # Generate SSH key for accessing secondary servers
    if [[ ! -f /home/ubuntu/.ssh/id_rsa ]]; then
        echo "Generating SSH key for primary server..."
        sudo -u ubuntu ssh-keygen -t rsa -b 4096 -f /home/ubuntu/.ssh/id_rsa -N ""
    fi
    
    # Display the public key for adding to secondary servers
    echo "Use the following public key to authorize the primary server on secondary servers:"
    cat /home/ubuntu/.ssh/id_rsa.pub
    
    # Set up nginx as a proxy to the API
    echo "Setting up nginx as a proxy to the API..."
    cat > /etc/nginx/sites-available/vm-api << EOF
server {
    listen 80;
    server_name ${API_HOSTNAME};

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    
    # Enable the nginx site
    ln -sf /etc/nginx/sites-available/vm-api /etc/nginx/sites-enabled/
    nginx -t && systemctl restart nginx
    
    # Set up systemd service for API
    echo "Setting up systemd service for API..."
    cat > /etc/systemd/system/vm-api.service << EOF
[Unit]
Description=VM API Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/vm-experiments/api
ExecStart=/home/ubuntu/vm-experiments/api/run.sh
Restart=always
Environment=PYTHONPATH=/home/ubuntu/vm-experiments

[Install]
WantedBy=multi-user.target
EOF
    
    # Enable and start the API service
    systemctl daemon-reload
    systemctl enable vm-api
    systemctl start vm-api
    
    echo "Primary server setup complete!"
    echo "API is accessible at http://${API_HOSTNAME}"
    
elif [[ "${SERVER_TYPE}" == "secondary" ]]; then
    echo "Setting up secondary server..."
    
    if [[ "${API_HOSTNAME}" == "localhost" ]]; then
        echo "Error: API hostname must be specified for secondary servers"
        exit 1
    fi
    
    # Create SSH authorized_keys if it doesn't exist
    sudo -u ubuntu mkdir -p /home/ubuntu/.ssh
    sudo -u ubuntu touch /home/ubuntu/.ssh/authorized_keys
    chmod 700 /home/ubuntu/.ssh
    chmod 600 /home/ubuntu/.ssh/authorized_keys
    
    echo "Please add the public key from the primary server to /home/ubuntu/.ssh/authorized_keys"
    echo "Run the following command on the primary server:"
    echo "  cat /home/ubuntu/.ssh/id_rsa.pub | ssh ubuntu@<this-server-ip> 'cat >> ~/.ssh/authorized_keys'"
    
    # Test connection to the API
    echo "Testing connection to the API..."
    if curl -s "http://${API_HOSTNAME}/api/health" > /dev/null; then
        echo "Successfully connected to the API"
    else
        echo "Failed to connect to the API. Make sure the primary server is set up and the API is running."
    fi
    
    echo "Secondary server setup complete!"
fi

# Final steps for both server types
echo "Running libvirt permissions setup script..."
cd /home/ubuntu/vm-experiments
if [[ -f "./api/setup-libvirt-permissions.sh" ]]; then
    chmod +x ./api/setup-libvirt-permissions.sh
    ./api/setup-libvirt-permissions.sh
else
    echo "Libvirt permissions setup script not found."
fi

echo "===== VM Experiments Cluster Setup Complete ====="
echo "Server Type: ${SERVER_TYPE}"
echo "API Hostname: ${API_HOSTNAME}"
echo "For more information, check the documentation or run with --help" 