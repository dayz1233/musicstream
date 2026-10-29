#!/bin/bash
echo "========================================"
echo "  MusicStream - Simple Startup"
echo "========================================"
echo
if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Install it first."
    exit 1
fi
echo "Installing dependencies..."
pip3 install -r requirements.txt --quiet
echo
echo "Starting server on http://0.0.0.0:8000"
echo "Open http://localhost:8000"
python3 main.py
