# dbus-smartshunt-to-bms

A Victron Venus OS service that converts SmartShunts into virtual Battery Management Systems (BMS).

> **Note:** This project shares framework code with [dbus-aggregate-smartshunts](https://github.com/TechBlueprints/dbus-aggregate-smartshunts) and is derived from [dbus-aggregate-batteries](https://github.com/Dr-Gigavolt/dbus-aggregate-batteries) by Anton Labanc PhD.

## Purpose

If you have "dumb" batteries (no built-in BMS communication) monitored by SmartShunts, this service adds BMS functionality to enable DVCC charge control. Each SmartShunt gets converted into a virtual BMS device that can control charging.

**Key Benefits:**
- 🎯 **BMS functionality** - Adds charge control to SmartShunt-monitored batteries
- 🛡️ **Safety** - Provides `/Io/AllowToCharge` and `/Io/AllowToDischarge` flags based on alarms and temperature
- ⚡ **DVCC integration** - Publishes CVL/CCL/DCL for Multi/Quattro/MPPT control
- 🔧 **Zero aggregation** - One BMS service per SmartShunt (use dbus-aggregate-smartshunts for aggregation)
- 📊 **Pass-through monitoring** - All SmartShunt data passed through unchanged

## Use Cases

**Use this project if:**
- ✅ You have batteries WITHOUT built-in BMS communication
- ✅ Each battery has a SmartShunt for monitoring
- ✅ You want DVCC to control charging based on SmartShunt alarms/temperature
- ✅ You need each battery to appear as a BMS device in Venus OS

**Don't use this project if:**
- ❌ Your batteries already have BMS communication (use that instead)
- ❌ You want to aggregate multiple SmartShunts (use dbus-aggregate-smartshunts)
- ❌ You just need monitoring (SmartShunts already do that)

## How It Works

For each SmartShunt on your system, this service creates a virtual BMS that:

1. **Passes through all SmartShunt data** - voltage, current, SoC, alarms, history, etc.
2. **Adds BMS-specific paths** - `/Info/MaxChargeVoltage`, `/Info/MaxChargeCurrent`, `/Info/MaxDischargeCurrent`
3. **Adds charge control** - `/Io/AllowToCharge`, `/Io/AllowToDischarge` based on alarms and temperature
4. **Appears as BMS** - ProductId 0xBA77 so Venus OS treats it as a battery with BMS

## Installation

### Prerequisites

- Victron Cerbo GX or Venus GX running Venus OS
- 1+ SmartShunts connected and visible on D-Bus
- "Dumb" batteries (no built-in BMS communication)

### Steps

1. SSH into your Cerbo GX:
```bash
ssh root@<cerbo-ip>
```

2. Download and install:
```bash
cd /data/apps
git clone https://github.com/TechBlueprints/dbus-smartshunt-to-bms.git
cd dbus-smartshunt-to-bms
chmod +x *.sh
./install.sh
```

3. Configure (optional):
```bash
cp config.default.ini config.ini
nano config.ini  # Edit your settings
```

4. Enable and start:
```bash
./enable.sh
```

5. Check logs:
```bash
./get-logs.sh
```

## Configuration

Copy `config.default.ini` to `config.ini` and customize:

### Essential Settings

```ini
[DEFAULT]
# Battery specifications (total for all batteries connected to THIS SmartShunt)
MAX_CHARGE_VOLTAGE = 14.6      # Bulk charging voltage
MAX_CHARGE_CURRENT = 50        # Maximum charge current (A)
MAX_DISCHARGE_CURRENT = 100    # Maximum discharge current (A)

# Temperature thresholds
TEMP_COLD_DANGER = 5.0         # Report coldest temp below this (°C)
TEMP_HOT_DANGER = 35.0         # Report hottest temp above this (°C)
```

### Advanced Settings

```ini
# Device discovery
UPDATE_INTERVAL_FIND_DEVICES = 1       # Check for new devices every 1s (first 30s)
MAX_UPDATE_INTERVAL_FIND_DEVICES = 1800  # Max check interval: 30 minutes

# Exclusions (optional)
EXCLUDE_SHUNTS = com.victronenergy.battery.ttyUSB0  # Comma-separated list
```

See `config.default.ini` for all options.

## Architecture

```
Physical Setup:
  Battery 1 + SmartShunt 1  ──┐
  Battery 2 + SmartShunt 2  ──┼──> dbus-smartshunt-to-bms ──> Virtual BMS 1 (for SmartShunt 1)
  Battery 3 + SmartShunt 3  ──┘                            └──> Virtual BMS 2 (for SmartShunt 2)
                                                            └──> Virtual BMS 3 (for SmartShunt 3)
```

Each SmartShunt becomes a separate BMS. If you want them combined, use [dbus-aggregate-smartshunts](https://github.com/TechBlueprints/dbus-aggregate-smartshunts) instead.

## D-Bus Service Names

Physical SmartShunts:
- `com.victronenergy.battery.ttyS5`
- `com.victronenergy.battery.ttyS6`
- etc.

Virtual BMS services created by this tool:
- `com.victronenergy.battery.smartshunt_bms_278` (using device instance)
- `com.victronenergy.battery.smartshunt_bms_277`
- etc.

## Relationship with dbus-aggregate-smartshunts

These are companion projects with different purposes:

| Project | Purpose | Output | Use When |
|---------|---------|--------|----------|
| **dbus-smartshunt-to-bms** | Add BMS to each SmartShunt | One BMS per SmartShunt | Batteries need individual charge control |
| **dbus-aggregate-smartshunts** | Combine multiple SmartShunts | One aggregated monitor | Parallel batteries, unified monitoring |

**Can I use both?** Yes! Use this to convert SmartShunts to BMS, then use dbus-aggregate-smartshunts to combine them.

## Troubleshooting

### Service won't start
```bash
./get-logs.sh  # Check for errors
```

### BMS not appearing in Venus OS
- Check that SmartShunts are visible: `dbus -y com.victronenergy.battery.ttyS5`
- Verify service is running: `svstat /service/dbus-smartshunt-to-bms`
- Check ProductId is 0xBA77: `dbus -y com.victronenergy.battery.smartshunt_bms_278 /ProductId GetValue`

### DVCC not using limits
- Enable DVCC in Venus OS settings
- Ensure virtual BMS has highest DeviceInstance priority
- Check `/Info/MaxChargeVoltage` etc. are published

## Uninstall

```bash
cd /data/apps/dbus-smartshunt-to-bms
./uninstall.sh
```

## License

MIT License - See LICENSE file

## Credits

- Based on [dbus-aggregate-batteries](https://github.com/Dr-Gigavolt/dbus-aggregate-batteries) by Anton Labanc PhD
- Adapted and extended by TechBlueprints
- Uses Victron's velib_python library

## Support

- GitHub Issues: https://github.com/TechBlueprints/dbus-smartshunt-to-bms/issues
- Related Project: [dbus-aggregate-smartshunts](https://github.com/TechBlueprints/dbus-aggregate-smartshunts)
