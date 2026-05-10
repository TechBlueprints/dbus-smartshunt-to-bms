# dbus-smartshunt-to-bms

**Victron Venus OS D-Bus Service: SmartShunt to Virtual BMS Converter**

Convert Victron SmartShunts into virtual Battery Management Systems (BMS) with dynamic charge control for Cerbo GX, Venus GX, and other Venus OS devices.

## ⚠️ EXPERIMENTAL - USE AT YOUR OWN RISK ⚠️

**THIS PROJECT IS RAGINGLY EXPERIMENTAL AND REALLY HASN'T BEEN TRIED OR RUN YET.**

**YOU SHOULD NOT USE THIS SOFTWARE.** If you choose to use it anyway, it is **DEFINITELY AT YOUR OWN RISK.**

## Status: Not Deployed

This was an exploratory / experimental project. **It has never been deployed
or run on any system.** It is not installed on any Cerbo GX, dev or
production. The repository is kept around as a reference for the design
ideas (virtual BMS shape, dynamic CVL, charge phase controller skeleton),
but the code itself is unfinished and known to contain syntax errors.

There is no active work on this project. If you are looking for the
actively-deployed SmartShunt aggregation service, see
[dbus-aggregate-smartshunts](https://github.com/TechBlueprints/dbus-aggregate-smartshunts).

This service directly controls battery charging behavior and publishes CVL (Charge Voltage Limit) values to your Victron Energy system. Incorrect charge parameters can:
- ❌ Damage your batteries
- ❌ Cause fires or explosions
- ❌ Void warranties
- ❌ Destroy expensive equipment

**By using this software, you accept full responsibility for any and all consequences, including but not limited to property damage, personal injury, or death.**

---

## Overview

A Venus OS service that converts Victron SmartShunt battery monitors into virtual Battery Management Systems (BMS) with DVCC charge control, dynamic CVL, and comprehensive battery protection.

**For:** Victron Energy systems (Cerbo GX, Venus GX, Raspberry Pi running Venus OS)  
**Monitors:** SmartShunt 500A, SmartShunt IP65  
**Supports:** LiFePO4, Lead-Acid, LTO batteries  
**Features:** Dynamic CVL, MPPT integration, temperature protection, low voltage disconnect

> **Note:** This project shares framework code with [dbus-aggregate-smartshunts](https://github.com/TechBlueprints/dbus-aggregate-smartshunts) and is derived from [dbus-aggregate-batteries](https://github.com/Dr-Gigavolt/dbus-aggregate-batteries) by Anton Labanc PhD.

## Purpose

**⚠️ EXPERIMENTAL SOFTWARE - READ THE WARNING ABOVE ⚠️**

If you have "dumb" batteries (no built-in BMS communication) monitored by SmartShunts, this service adds BMS functionality to enable DVCC charge control. Each SmartShunt gets converted into a virtual BMS device that can control charging.

**This is experimental software that directly controls your charging system. It may not work correctly and could damage your equipment.**

**Key Benefits:**
- 🎯 **Virtual BMS** - Converts Victron SmartShunt into BMS for DVCC control
- 🛡️ **Battery Protection** - Temperature limits, low voltage disconnect, alarm monitoring
- ⚡ **DVCC Integration** - Publishes CVL/CCL/DCL for Multi/Quattro/MPPT/Orion XS control
- 🔄 **Dynamic CVL** - Auto-discovers MPPT charge settings (absorption/float/re-bulk)
- 📊 **Full Passthrough** - All SmartShunt data (voltage, current, SoC, alarms, history)
- 🔧 **Per-Battery Control** - One virtual BMS per SmartShunt (no aggregation)
- 🔋 **Multi-Chemistry** - LiFePO4, Lead-Acid, LTO support

## Use Cases

**Use this project if:**
- ✅ You have batteries WITHOUT built-in BMS communication
- ✅ Each battery has a Victron SmartShunt for monitoring
- ✅ You want DVCC to control Victron Multi/Quattro/MPPT/Orion XS charging
- ✅ You need dynamic CVL with charge phase control (Bulk/Absorption/Float)
- ✅ You want battery protection (temperature, voltage, current limits)
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
5. **Dynamic CVL (optional)** - Automatically discovers charge settings from MPPTs/Orion XS and dynamically adjusts CVL based on charge phase (bulk/absorption/float)

### Dynamic Charge Control (Advanced Feature)

**⚠️ HIGHLY EXPERIMENTAL - This feature is completely untested and may behave unpredictably ⚠️**

The service automatically discovers all charging devices on your system (MPPTs, Orion XS, SmartChargers) and reads their charge algorithm settings:

- **Absorption Voltage** - Bulk charging target
- **Float Voltage** - Maintenance charging voltage
- **Tail Current** - When to transition from absorption to float
- **Re-bulk Offset** - When to restart bulk charging from float

Using these parameters, the virtual BMS implements a charge phase controller that mimics the behavior of a real BMS:

- **Bulk Phase**: Publishes absorption voltage as CVL, allowing maximum charge current
- **Absorption Phase**: Maintains absorption voltage, monitors tail current
- **Float Phase**: Lowers CVL to float voltage for maintenance
- **Re-bulk**: Returns to bulk if voltage drops below re-bulk threshold

This creates a sophisticated charge control system that protects your batteries while maximizing charge efficiency.

**Note**: Dynamic CVL requires at least one MPPT or Orion XS on your system with readable charge settings. If no charge sources are found, the service falls back to static CVL from your config.

## Installation

### ⚠️ WARNING: EXPERIMENTAL SOFTWARE ⚠️

**DO NOT INSTALL THIS ON A PRODUCTION SYSTEM.**

This software is completely untested and may cause serious damage to your batteries and equipment. Install only on a test system where battery damage is acceptable.

**YOU HAVE BEEN WARNED.**

---

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
# Battery voltage limits
MAX_CHARGE_VOLTAGE = 14.6      # Safety ceiling for CVL (V)
MIN_BATTERY_VOLTAGE = 10.8     # Low voltage disconnect (V)

# Battery current limits (per SmartShunt)
MAX_CHARGE_CURRENT = 50        # Maximum charge current (A)
MAX_DISCHARGE_CURRENT = 100    # Maximum discharge current (A)

# Temperature thresholds
TEMP_COLD_DANGER = 5.0         # Disable charging below this (°C)
TEMP_HOT_DANGER = 45.0         # Disable charging above this (°C)
```

**Important Configuration Notes:**

- **`MAX_CHARGE_VOLTAGE`**: Safety ceiling for CVL. If you have MPPTs or Orion XS devices, the service will automatically read their absorption/float voltages and use those for dynamic CVL. This value acts as a safety ceiling - the published CVL will never exceed this value.

- **`MIN_BATTERY_VOLTAGE`**: Low Voltage Disconnect (LVD) protection. Discharging is disabled (`/Io/AllowToDischarge = 0`) when voltage drops to or below this value. Critical for preventing over-discharge damage.
  - LiFePO4 12V: 10.8V (11.0V for extra safety margin)
  - LiFePO4 24V: 21.6V
  - LiFePO4 48V: 43.2V
  - Lead-Acid 12V: 11.5V

- **Current Limits**: These are per SmartShunt. If a SmartShunt monitors multiple batteries, set these to the total for all batteries on that shunt.

- **Temperature Limits**: Used to disable charging/discharging when temperatures are unsafe. Defaults are appropriate for LiFePO4.

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

### Dynamic CVL not working
- Check logs for "Charge Source Discovery" output
- Verify at least one MPPT or Orion XS is detected
- Confirm charge parameters were read successfully
- Look for "CVL updated" messages in logs during charging
- If no charge sources found, service will use static MAX_CHARGE_VOLTAGE from config

### CVL seems wrong
- Check your MPPT/Orion XS charge settings in VictronConnect
- Verify all chargers have consistent settings (absorption/float voltages should match)
- Service uses consensus values from all discovered chargers
- MAX_CHARGE_VOLTAGE in config acts as safety ceiling

## Uninstall

```bash
cd /data/apps/dbus-smartshunt-to-bms
./uninstall.sh
```

## License

MIT License - See LICENSE file

## Credits

- Based on [dbus-aggregate-batteries](https://github.com/Dr-Gigavolt/dbus-aggregate-batteries) by Anton Labanc PhD
- Inspired by [dbus-serialbattery](https://github.com/mr-manuel/venus-os_dbus-serialbattery) by mr-manuel (Louis van der Walt)
- Adapted and extended by Clinton Goudie-Nice
- Uses Victron's velib_python library

## Support

**⚠️ THIS IS EXPERIMENTAL SOFTWARE - SUPPORT IS LIMITED ⚠️**

- GitHub Issues: https://github.com/TechBlueprints/dbus-smartshunt-to-bms/issues
- Related Project: [dbus-aggregate-smartshunts](https://github.com/TechBlueprints/dbus-aggregate-smartshunts)

**Use this software at your own risk. No warranties or guarantees of any kind are provided.**
