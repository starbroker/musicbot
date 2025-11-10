#!/bin/bash
echo "Starting Discord Music Bot..."

# Install dependencies
pip install -r requirements.txt

# Create necessary directories
mkdir -p temp

# Start the bot
python bot.py