#!/bin/bash

# Activate virtual environment if it exists
if [ -d "../venv" ]; then
    source ../venv/bin/activate
fi

# Set Python path to include the root directory
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Run the Flask application as a module
python -m app.api 