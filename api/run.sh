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

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "Installing requirements..."
pip install -r requirements.txt

# Set environment variables
export FLASK_APP=app.api
export FLASK_ENV=development
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Check if libvirt is installed
if ! command -v virsh &> /dev/null; then
    echo "libvirt not found. Please install libvirt-dev package."
    exit 1
fi

# Check if qemu-img is installed
if ! command -v qemu-img &> /dev/null; then
    echo "qemu-img not found. Please install qemu-utils package."
    exit 1
fi

# Check if wget is installed
if ! command -v wget &> /dev/null; then
    echo "wget not found. Please install wget package."
    exit 1
fi

# Create necessary directories
mkdir -p data/vms
mkdir -p data/disks
mkdir -p data/networks
mkdir -p data/tmp

# Make sure image directory exists and is writable
if [ ! -d "data/vms" ]; then
  echo "Creating VM images directory..."
  mkdir -p data/vms
  chmod 755 data/vms
fi

# Check if port 5000 is already in use and kill the process if needed
echo "Checking if port 5000 is already in use..."
if lsof -Pi :5000 -sTCP:LISTEN -t >/dev/null ; then
    echo "Port 5000 is already in use. Stopping the process..."
    lsof -ti:5000 | xargs kill -9
    sleep 1
fi

# Start API server
echo "Starting API server..."
if [ "$1" == "test" ]; then
    # Start the server in the background for tests
    python -m flask run --host=0.0.0.0 --port=5000 &
    API_PID=$!
    echo "API server started in the background (PID: $API_PID)"
    
    # Wait for the server to start
    echo "Waiting for API server to start..."
    sleep 3
    
    # Run the tests
    echo "Running tests..."
    npm test
    
    # Kill the API server after tests
    echo "Stopping API server..."
    kill $API_PID
else
    # Start the server in the foreground
    python -m flask run --host=0.0.0.0 --port=5000
fi 