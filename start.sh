#!/bin/bash
echo "Starting Discord Music Bot..."

# Install dependencies
pip install -r requirements.txt

# Create necessary directories
mkdir -p temp

# Set Python path (might help with audioop issue)
export PYTHONPATH=/opt/render/project/src:$PYTHONPATH

# Start the bot with explicit Python path
/opt/render/project/src/.venv/bin/python bot.py
