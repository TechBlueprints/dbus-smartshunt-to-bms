#!/bin/bash

# Disable dbus-smartshunt-to-bms service

SERVICE_LINK="/service/dbus-smartshunt-to-bms"

echo "=== Disabling dbus-smartshunt-to-bms service ==="

if [ -L "$SERVICE_LINK" ]; then
    echo "Stopping service..."
    svc -d "$SERVICE_LINK"
    sleep 2
    
    echo "Removing service link..."
    rm "$SERVICE_LINK"
    
    echo "Service disabled!"
else
    echo "Service link not found. Already disabled?"
fi

echo ""

