#!/bin/bash

# Uninstall dbus-smartshunt-to-bms

SERVICE_LINK="/service/dbus-smartshunt-to-bms"
INSTALL_DIR="/data/apps/dbus-smartshunt-to-bms"

echo "=== Uninstalling dbus-smartshunt-to-bms ==="
echo ""
echo "This will:"
echo "  1. Stop and disable the service"
echo "  2. Remove $INSTALL_DIR"
echo ""
read -p "Are you sure? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Uninstall cancelled."
    exit 0
fi

# Disable service
if [ -L "$SERVICE_LINK" ]; then
    echo "Stopping service..."
    svc -d "$SERVICE_LINK"
    sleep 2
    
    echo "Removing service link..."
    rm "$SERVICE_LINK"
fi

# Remove installation directory
if [ -d "$INSTALL_DIR" ]; then
    echo "Removing installation directory..."
    rm -rf "$INSTALL_DIR"
fi

echo ""
echo "Uninstall complete!"
echo ""

