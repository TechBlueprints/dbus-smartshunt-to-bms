#!/bin/bash

# Show logs from dbus-smartshunt-to-bms

LOG_DIR="/data/apps/dbus-smartshunt-to-bms/service/log"

if [ -d "$LOG_DIR" ]; then
    echo "=== dbus-smartshunt-to-bms logs ==="
    echo "Press Ctrl+C to exit"
    echo ""
    tail -f "$LOG_DIR/current" | tai64nlocal
else
    echo "Error: Log directory not found at $LOG_DIR"
    exit 1
fi

