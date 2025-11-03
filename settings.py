#!/usr/bin/env python3

"""
Settings loader for dbus-smartshunt-to-bms
"""

import configparser
import os
import sys

# Load config files
config = configparser.ConfigParser()
config_files = [
    os.path.join(os.path.dirname(__file__), "config.default.ini"),
    os.path.join(os.path.dirname(__file__), "config.ini")
]

for config_file in config_files:
    if os.path.exists(config_file):
        config.read(config_file)
        print(f"Loaded config: {config_file}")

# Helper function to safely get float values
def get_float_from_config(section, key, default):
    try:
        value = config[section].get(key, "").strip()
        if value == "":
            return default
        return float(value)
    except (ValueError, KeyError):
        return default

# Helper function to safely get int values
def get_int_from_config(section, key, default):
    try:
        value = config[section].get(key, "").strip()
        if value == "":
            return default
        return int(value)
    except (ValueError, KeyError):
        return default

# Validate configuration
errors_in_config = []

# Battery charge limits (optional - leave empty for pass-through only)
MAX_CHARGE_VOLTAGE = get_float_from_config("DEFAULT", "MAX_CHARGE_VOLTAGE", None)
MAX_CHARGE_CURRENT = get_float_from_config("DEFAULT", "MAX_CHARGE_CURRENT", None)
MAX_DISCHARGE_CURRENT = get_float_from_config("DEFAULT", "MAX_DISCHARGE_CURRENT", None)

# Validate limits if provided
if MAX_CHARGE_VOLTAGE is not None and MAX_CHARGE_VOLTAGE <= 0:
    errors_in_config.append(f"MAX_CHARGE_VOLTAGE must be > 0 or left empty. Got: {MAX_CHARGE_VOLTAGE}")

if MAX_CHARGE_CURRENT is not None and MAX_CHARGE_CURRENT <= 0:
    errors_in_config.append(f"MAX_CHARGE_CURRENT must be > 0 or left empty. Got: {MAX_CHARGE_CURRENT}")

if MAX_DISCHARGE_CURRENT is not None and MAX_DISCHARGE_CURRENT <= 0:
    errors_in_config.append(f"MAX_DISCHARGE_CURRENT must be > 0 or left empty. Got: {MAX_DISCHARGE_CURRENT}")

# Temperature thresholds
TEMP_COLD_DANGER = get_float_from_config("DEFAULT", "TEMP_COLD_DANGER", 5.0)
TEMP_HOT_DANGER = get_float_from_config("DEFAULT", "TEMP_HOT_DANGER", 45.0)

if TEMP_COLD_DANGER >= TEMP_HOT_DANGER:
    errors_in_config.append(f"TEMP_COLD_DANGER ({TEMP_COLD_DANGER}) must be < TEMP_HOT_DANGER ({TEMP_HOT_DANGER})")

# Device naming
DEVICE_NAME = config["DEFAULT"].get("DEVICE_NAME", "").strip()

# Exclusions
EXCLUDE_SHUNTS = config["DEFAULT"].get("EXCLUDE_SHUNTS", "").strip()

# Status logging
STATUS_UPDATE_INTERVAL = get_int_from_config("DEFAULT", "STATUS_UPDATE_INTERVAL", 300)
if STATUS_UPDATE_INTERVAL < 0:
    errors_in_config.append(f"STATUS_UPDATE_INTERVAL must be >= 0. Got: {STATUS_UPDATE_INTERVAL}")

# Check for errors
if errors_in_config:
    print("\n" + "="*60)
    print("CONFIGURATION ERRORS:")
    print("="*60)
    for error in errors_in_config:
        print(f"  ❌ {error}")
    print("="*60)
    print("\nPlease fix the errors in config.ini and restart the service.")
    print("="*60 + "\n")
    sys.exit(1)

print("✓ Configuration loaded successfully")
