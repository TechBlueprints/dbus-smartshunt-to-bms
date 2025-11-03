#!/bin/bash

# Restart dbus-smartshunt-to-bms service

SERVICE_LINK="/service/dbus-smartshunt-to-bms"

echo "=== Restarting dbus-smartshunt-to-bms ==="

if [ -L "$SERVICE_LINK" ]; then
    echo "Stopping service..."
    svc -d "$SERVICE_LINK"
    sleep 2
    
    echo "Starting service..."
    svc -u "$SERVICE_LINK"
    sleep 2
    
    svstat "$SERVICE_LINK"
    echo ""
    echo "Service restarted!"
else
    echo "Error: Service not enabled. Run enable.sh first."
    exit 1
fi

echo ""
echo "To check logs:"
echo "  tail -f /data/apps/dbus-smartshunt-to-bms/service/log/current"
echo ""

