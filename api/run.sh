#!/bin/bash

# Exit on error
set -e

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/upgrade pip
python -m pip install --upgrade pip

# Install requirements
echo "Installing requirements..."
pip install -r requirements.txt

# Set environment variables
export FLASK_APP=app.api
export FLASK_ENV=production
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Check if libvirt is installed
if ! command -v virsh &> /dev/null; then
    echo "libvirt not found. Please install libvirt-dev package."
    exit 1
fi

# Create necessary directories
mkdir -p data/vms
mkdir -p data/disks
mkdir -p data/networks

# Run the Flask application
echo "Starting API server..."
python -m flask run --host=0.0.0.0 --port=5000 