#!/bin/bash

# dbus-smartshunt-to-bms installation script for Venus OS
# Installs to /data/apps/dbus-smartshunt-to-bms

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/data/apps/dbus-smartshunt-to-bms"
SERVICE_TEMPLATE="/opt/victronenergy/service-templates/dbus-smartshunt-to-bms"

echo "=== dbus-smartshunt-to-bms Installation ==="
echo ""

# Check if running on Venus OS
if [ ! -d "/data/apps" ]; then
    echo "Error: /data/apps directory not found."
    echo "This script is designed for Venus OS."
    exit 1
fi

echo "Installing to: $INSTALL_DIR"

# Create installation directory
mkdir -p "$INSTALL_DIR"

# Copy files
echo "Copying files..."
cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/"

# Make Python script executable
chmod +x "$INSTALL_DIR/dbus-smartshunt-to-bms.py"

# Make shell scripts executable
chmod +x "$INSTALL_DIR"/*.sh
chmod +x "$INSTALL_DIR/service/run"
chmod +x "$INSTALL_DIR/service/log/run"

# Create service template for autostart
echo "Creating service template for autostart..."
mkdir -p "$SERVICE_TEMPLATE"
ln -sf "$INSTALL_DIR/service/run" "$SERVICE_TEMPLATE/run"
ln -sf "$INSTALL_DIR/service/log" "$SERVICE_TEMPLATE/log"

# Add to rc.local for autostart on boot
RC_LOCAL="/data/rc.local"
if [ ! -f "$RC_LOCAL" ]; then
    echo "#!/bin/bash" > "$RC_LOCAL"
    chmod +x "$RC_LOCAL"
fi

# Check if already in rc.local
if ! grep -q "dbus-smartshunt-to-bms/enable.sh" "$RC_LOCAL"; then
    echo "Adding to rc.local for autostart..."
    echo "bash $INSTALL_DIR/enable.sh > $INSTALL_DIR/startup.log 2>&1 &" >> "$RC_LOCAL"
fi

# Check if config.ini exists
if [ ! -f "$INSTALL_DIR/config.ini" ]; then
    echo ""
    echo "Warning: config.ini not found!"
    echo "Please create $INSTALL_DIR/config.ini with your settings."
    echo "You can copy config.default.ini as a starting point:"
    echo "  cp $INSTALL_DIR/config.default.ini $INSTALL_DIR/config.ini"
    echo ""
fi

echo ""
echo "Installation complete!"
echo ""
echo "Service template created at: $SERVICE_TEMPLATE"
echo "The service will now autostart on boot."
echo ""
echo "To enable the service now, run:"
echo "  $INSTALL_DIR/enable.sh"
echo ""
echo "To restart the service after config changes:"
echo "  $INSTALL_DIR/restart.sh"
echo ""

