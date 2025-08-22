#!/bin/bash

# Azure Web App startup script for ScaleVid Platform

echo "Starting ScaleVid Platform deployment..."

# Create necessary directories
mkdir -p uploads
mkdir -p assets

# Initialize database if it doesn't exist
if [ ! -f video_platform.db ]; then
    echo "Creating new database..."
    touch video_platform.db
fi

# Set environment variables for production
export DASH_DEBUG=False
export DASH_HOST=0.0.0.0
export DASH_PORT=8000

echo "Starting Dash application with Gunicorn..."

# Start the application with Gunicorn
exec gunicorn --bind=0.0.0.0:$PORT --timeout 600 --workers=1 --threads=8 app:server