#!/bin/bash

echo "Starting build process..."

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

echo "Dependencies installed successfully"

# Create necessary directories
mkdir -p uploads
mkdir -p assets

echo "Build process completed"