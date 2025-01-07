#!/bin/bash

# Function to stop background processes on script exit
cleanup() {
    echo "Stopping servers..."
    kill $(jobs -p) 2>/dev/null
}

# Set up cleanup on script exit
trap cleanup EXIT

# Start the Flask API server
cd api
source venv/bin/activate
sudo python run.py &
cd ..

# Wait a bit for the API server to start
sleep 2

# Start the React frontend
cd frontend
npm start &

# Wait for all background processes
wait