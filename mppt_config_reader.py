"""
MPPT/Charger Configuration Reader
Reads charge algorithm settings from Victron MPPTs via VE.Direct VregLink interface

Based on VE.Direct Protocol documentation and register addresses for Solar Charge Controllers
"""

import dbus
import struct
import logging

# VE.Direct register addresses for MPPT/Solar Charger configuration
# Verified on SmartSolar MPPT 100/50 - These are the correct addresses

# Battery voltage settings (0xEDF0 range) - All in 0.01V (centivolts)
VREG_FLOAT_VOLTAGE = 0xEDF6          # Float voltage in 0.01V (16-bit LE)
VREG_ABSORPTION_VOLTAGE = 0xEDF7     # Absorption voltage in 0.01V (16-bit LE)
VREG_EQUALIZATION_VOLTAGE = 0x2003   # Equalization/Max voltage in 0.01V (16-bit LE)

# Charge timing settings
VREG_ABSORPTION_TIME = 0xEDF3        # Max absorption time in minutes (16-bit LE)
VREG_REPEATED_ABSORPTION_TIME = 0xEDF2   # Repeated absorption time in days (16-bit LE)
VREG_REPEATED_ABSORPTION_INTERVAL = 0xEDF1  # Days between repeated absorption (16-bit LE)

# Storage phase settings
VREG_STORAGE_VOLTAGE = 0xEDF4        # Storage voltage in 0.01V (16-bit LE)

# Equalization/Recondition settings (primarily for lead-acid)
VREG_EQUALIZATION_VOLTAGE = 0xEDF8   # Equalization voltage in 0.01V (16-bit LE)
VREG_EQUALIZATION_DURATION = 0xEDFB  # Equalization duration in minutes (16-bit LE)
VREG_AUTO_EQUALIZATION = 0xEDFA      # Auto equalization enabled (0/1)
VREG_ABSORPTION_TIME_LIMIT = 0xEDF4  # Absorption time limit enabled (0/1)

# Current and tail current settings (verified on SmartSolar MPPT 100/50)
VREG_TAIL_CURRENT = 0xEDE7           # Tail current in 0.1A (16-bit LE) - absorption→float threshold
VREG_LOW_CURRENT_LEVEL = 0xEDE8      # Low current level in 0.1A (16-bit LE)
VREG_LOW_CURRENT_TIME = 0xEDE9       # Low current time in minutes (16-bit LE)

# Battery type and characteristics
VREG_BATTERY_TYPE = 0xEDFF           # Battery type (0=Lead Acid, 1=LiFePO4, etc.)
VREG_BATTERY_VOLTAGE = 0xEDFB        # Battery voltage setting (12V/24V/48V)

# Temperature compensation
VREG_TEMP_COMPENSATION = 0xEDF5      # Temperature compensation in mV/°C (16-bit signed)

# Adaptive charge settings (newer MPPTs)
VREG_ADAPTIVE_MODE = 0xEDF9          # Adaptive absorption time enabled (0/1)
VREG_AUTO_EQUALIZE = 0xEDFA          # Auto equalization enabled (0/1)

# Re-bulk voltage offset (verified on SmartSolar MPPT 100/50)
VREG_REBULK_OFFSET = 0xED2E          # Re-bulk voltage offset in 0.01V (16-bit LE) - triggers return to bulk

class MPPTConfig:
    """Represents charge algorithm configuration from a Victron MPPT Solar Charger"""
    
    def __init__(self, service_name):
        """
        Initialize MPPT configuration reader
        
        Args:
            service_name: D-Bus service name (e.g. 'com.victronenergy.solarcharger.ttyUSB1')
        """
        self.service_name = service_name
        
        # Voltage settings (in volts)
        self.absorption_voltage = None
        self.float_voltage = None
        self.equalization_voltage = None
        self.rebulk_offset = None
        
        # Timing settings
        self.absorption_time = None
        self.absorption_time_limit_enabled = None
        self.low_current_time = None
        self.repeated_absorption_time = None   # days
        self.repeated_absorption_interval = None  # days
        
        # Current settings (in amps)
        self.tail_current = None
        self.low_current_level = None
        
        # Storage phase
        self.storage_voltage = None  # volts
        
        # Equalization/Recondition (lead-acid batteries)
        self.equalization_voltage = None  # volts
        self.equalization_duration = None  # minutes
        self.auto_equalization = None  # boolean
        
        # Battery characteristics
        self.battery_type = None
        self.battery_voltage_setting = None
        self.temp_compensation = None
        
        # Adaptive features
        self.adaptive_mode = None
        self.auto_equalize = None
    
    def read_vreg(self, bus, vreg):
        """
        Read a vreg register from the MPPT
        
        Args:
            bus: D-Bus system bus
            vreg: Register address (16-bit)
            
        Returns:
            bytes: Raw data or None if error
        """
        try:
            path = "/Devices/0/VregLink"
            vreglink = bus.get_object(self.service_name, path)
            iface = dbus.Interface(vreglink, "com.victronenergy.VregLink")
            error, data = iface.GetVreg(vreg)
            if error == 0:
                return bytes(data)
            else:
                logging.debug(f"Vreg {vreg:04x} returned error {error}")
                return None
        except Exception as e:
            logging.debug(f"Error reading vreg {vreg:04x} from {self.service_name}: {e}")
            return None
    
    def read_all(self, bus):
        """
        Read all charge configuration settings from the MPPT
        
        Args:
            bus: D-Bus system bus
            
        Returns:
            bool: True if at least one value was read successfully
        """
        success_count = 0
        
        try:
            # Absorption voltage (V) - 0.01V scaling
            data = self.read_vreg(bus, VREG_ABSORPTION_VOLTAGE)
            if data and len(data) >= 2:
                self.absorption_voltage = struct.unpack('<H', data[:2])[0] / 100.0
                success_count += 1
            
            # Float voltage (V) - 0.01V scaling
            data = self.read_vreg(bus, VREG_FLOAT_VOLTAGE)
            if data and len(data) >= 2:
                self.float_voltage = struct.unpack('<H', data[:2])[0] / 100.0
                success_count += 1
            
            # Equalization voltage (V) - 0.01V scaling
            data = self.read_vreg(bus, VREG_EQUALIZATION_VOLTAGE)
            if data and len(data) >= 2:
                self.equalization_voltage = struct.unpack('<H', data[:2])[0] / 100.0
                success_count += 1
            
            # Absorption time (minutes)
            data = self.read_vreg(bus, VREG_ABSORPTION_TIME)
            if data and len(data) >= 2:
                self.absorption_time = struct.unpack('<H', data[:2])[0]
                success_count += 1
            
            # Absorption time limit enabled
            data = self.read_vreg(bus, VREG_ABSORPTION_TIME_LIMIT)
            if data and len(data) >= 2:
                self.absorption_time_limit_enabled = struct.unpack('<H', data[:2])[0] == 1
                success_count += 1
            
            # Tail current (A) - 0.1A scaling (verified: 250 = 25.0A)
            data = self.read_vreg(bus, VREG_TAIL_CURRENT)
            if data and len(data) >= 2:
                raw_value = struct.unpack('<H', data[:2])[0]
                self.tail_current = raw_value / 10.0  # Convert to amps
                success_count += 1
            
            # Low current level (A) - 0.1A scaling
            data = self.read_vreg(bus, VREG_LOW_CURRENT_LEVEL)
            if data and len(data) >= 2:
                self.low_current_level = struct.unpack('<H', data[:2])[0] / 10.0
                success_count += 1
            
            # Low current time (minutes)
            data = self.read_vreg(bus, VREG_LOW_CURRENT_TIME)
            if data and len(data) >= 2:
                self.low_current_time = struct.unpack('<H', data[:2])[0]
                success_count += 1
            
            # Storage voltage (V) - 0.01V scaling
            data = self.read_vreg(bus, VREG_STORAGE_VOLTAGE)
            if data and len(data) >= 2:
                raw = struct.unpack('<H', data[:2])[0]
                if raw != 0 and raw != 65535:  # Not disabled
                    self.storage_voltage = raw / 100.0
                    success_count += 1
            
            # Repeated absorption settings
            data = self.read_vreg(bus, VREG_REPEATED_ABSORPTION_TIME)
            if data and len(data) >= 2:
                raw = struct.unpack('<H', data[:2])[0]
                if raw != 0 and raw != 65535:
                    self.repeated_absorption_time = raw  # days
                    success_count += 1
            
            data = self.read_vreg(bus, VREG_REPEATED_ABSORPTION_INTERVAL)
            if data and len(data) >= 2:
                raw = struct.unpack('<H', data[:2])[0]
                if raw != 0 and raw != 65535:
                    self.repeated_absorption_interval = raw  # days
                    success_count += 1
            
            # Equalization settings (lead-acid batteries)
            data = self.read_vreg(bus, VREG_EQUALIZATION_VOLTAGE)
            if data and len(data) >= 2:
                raw = struct.unpack('<H', data[:2])[0]
                if raw != 0 and raw != 65535:
                    self.equalization_voltage = raw / 100.0
                    success_count += 1
            
            data = self.read_vreg(bus, VREG_EQUALIZATION_DURATION)
            if data and len(data) >= 2:
                raw = struct.unpack('<H', data[:2])[0]
                if raw != 0 and raw != 65535:
                    self.equalization_duration = raw  # minutes
                    success_count += 1
            
            data = self.read_vreg(bus, VREG_AUTO_EQUALIZATION)
            if data and len(data) >= 2:
                raw = struct.unpack('<H', data[:2])[0]
                if raw != 65535:
                    self.auto_equalization = bool(raw)
                    success_count += 1
            
            # Battery type
            data = self.read_vreg(bus, VREG_BATTERY_TYPE)
            if data and len(data) >= 2:
                self.battery_type = struct.unpack('<H', data[:2])[0]
                success_count += 1
            
            # Battery voltage setting
            data = self.read_vreg(bus, VREG_BATTERY_VOLTAGE)
            if data and len(data) >= 2:
                self.battery_voltage_setting = struct.unpack('<H', data[:2])[0]
                success_count += 1
            
            # Temperature compensation (mV/°C) - signed
            data = self.read_vreg(bus, VREG_TEMP_COMPENSATION)
            if data and len(data) >= 2:
                self.temp_compensation = struct.unpack('<h', data[:2])[0]  # Signed
                success_count += 1
            
            # Adaptive mode
            data = self.read_vreg(bus, VREG_ADAPTIVE_MODE)
            if data and len(data) >= 2:
                self.adaptive_mode = struct.unpack('<H', data[:2])[0] == 1
                success_count += 1
            
            # Auto equalize
            data = self.read_vreg(bus, VREG_AUTO_EQUALIZE)
            if data and len(data) >= 2:
                self.auto_equalize = struct.unpack('<H', data[:2])[0] == 1
                success_count += 1
            
            # Re-bulk voltage offset (V) - 0.01V scaling (verified: 80 = 0.80V)
            data = self.read_vreg(bus, VREG_REBULK_OFFSET)
            if data and len(data) >= 2:
                raw_value = struct.unpack('<H', data[:2])[0]
                self.rebulk_offset = raw_value / 100.0  # Convert to volts
                success_count += 1
            
            return success_count > 0
        
        except Exception as e:
            logging.error(f"Error reading MPPT config from {self.service_name}: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False
    
    def log_all_settings(self):
        """Log all readable charge configuration settings from MPPT"""
        logging.info(f"=== MPPT Charge Configuration: {self.service_name} ===")
        
        battery_types = {
            0: "Lead Acid",
            1: "LiFePO4",
            2: "Li-ion",
            3: "Gel",
            4: "AGM"
        }
        
        # Critical charge voltages
        if self.absorption_voltage is not None:
            logging.info(f"  Absorption Voltage: {self.absorption_voltage:.2f} V")
        if self.float_voltage is not None:
            logging.info(f"  Float Voltage: {self.float_voltage:.2f} V")
        if self.equalization_voltage is not None:
            logging.info(f"  Equalization Voltage: {self.equalization_voltage:.2f} V")
        
        # Charge timing
        if self.absorption_time is not None:
            logging.info(f"  Absorption Time: {self.absorption_time} min")
        if self.absorption_time_limit_enabled is not None:
            logging.info(f"  Absorption Time Limit: {'Enabled' if self.absorption_time_limit_enabled else 'Disabled'}")
        if self.adaptive_mode is not None:
            logging.info(f"  Adaptive Absorption: {'Enabled' if self.adaptive_mode else 'Disabled'}")
        
        # Current thresholds
        if self.tail_current is not None:
            logging.info(f"  Tail Current: {self.tail_current:.1f} A")
        if self.low_current_level is not None:
            logging.info(f"  Low Current Level: {self.low_current_level:.1f} A")
        if self.low_current_time is not None:
            logging.info(f"  Low Current Time: {self.low_current_time} min")
        
        # Battery characteristics
        if self.battery_type is not None:
            type_str = battery_types.get(self.battery_type, f"Unknown ({self.battery_type})")
            logging.info(f"  Battery Type: {type_str}")
        if self.battery_voltage_setting is not None:
            logging.info(f"  Battery Voltage Setting: {self.battery_voltage_setting} V")
        if self.temp_compensation is not None:
            logging.info(f"  Temperature Compensation: {self.temp_compensation} mV/°C")
        
        # Re-bulk settings
        if self.rebulk_offset is not None:
            rebulk_v = self.float_voltage - self.rebulk_offset if self.float_voltage else None
            logging.info(f"  Re-bulk Voltage Offset: {self.rebulk_offset:.2f} V")
            if rebulk_v:
                logging.info(f"  Re-bulk Trigger Voltage: {rebulk_v:.2f} V")
        
        # Additional features
        if self.auto_equalize is not None:
            logging.info(f"  Auto Equalization: {'Enabled' if self.auto_equalize else 'Disabled'}")
        
        logging.info(f"=== End Configuration ===")
    
    def __str__(self):
        """String representation of configuration"""
        lines = [f"MPPT Config ({self.service_name}):"]
        if self.absorption_voltage is not None:
            lines.append(f"  Absorption: {self.absorption_voltage:.2f} V")
        if self.float_voltage is not None:
            lines.append(f"  Float: {self.float_voltage:.2f} V")
        if self.tail_current is not None:
            lines.append(f"  Tail Current: {self.tail_current:.1f} A")
        if self.absorption_time is not None:
            lines.append(f"  Absorption Time: {self.absorption_time} min")
        return "\n".join(lines)


def discover_charge_sources(bus):
    """
    Discover all MPPT/charger services and read their charge configurations
    
    Args:
        bus: D-Bus system bus
        
    Returns:
        list: List of MPPTConfig objects with successfully read configurations
    """
    configs = []
    
    try:
        # Get list of all services
        proxy = bus.get_object('org.freedesktop.DBus', '/org/freedesktop/DBus')
        dbus_interface = dbus.Interface(proxy, 'org.freedesktop.DBus')
        services = dbus_interface.ListNames()
        
        # Find all solarcharger services
        charger_services = [s for s in services if 'solarcharger' in s]
        
        for service in charger_services:
            logging.info(f"Reading charge configuration from {service}...")
            config = MPPTConfig(service)
            if config.read_all(bus):
                configs.append(config)
                config.log_all_settings()
            else:
                logging.warning(f"Could not read configuration from {service}")
        
        # Could also check for vebus (Multiplus) and other charger types
        # but they may not expose VregLink interface
        
    except Exception as e:
        logging.error(f"Error discovering charge sources: {e}")
        import traceback
        logging.error(traceback.format_exc())
    
    return configs


def get_consensus_charge_voltages(configs):
    """
    Get consensus charge voltages from multiple MPPTs/chargers
    Returns the most conservative (lowest) values if they differ
    
    Args:
        configs: List of MPPTConfig objects
        
    Returns:
        tuple: (absorption_v, float_v, tail_current_a) or (None, None, None)
    """
    if not configs:
        return None, None, None
    
    absorption_voltages = [c.absorption_voltage for c in configs if c.absorption_voltage is not None]
    float_voltages = [c.float_voltage for c in configs if c.float_voltage is not None]
    tail_currents = [c.tail_current for c in configs if c.tail_current is not None]
    
    # Use minimum (most conservative) if values differ
    absorption_v = min(absorption_voltages) if absorption_voltages else None
    float_v = min(float_voltages) if float_voltages else None
    tail_current_a = min(tail_currents) if tail_currents else None
    
    # Log if there are discrepancies
    if absorption_voltages and max(absorption_voltages) - min(absorption_voltages) > 0.1:
        logging.warning(f"Absorption voltages differ across chargers: {absorption_voltages}. Using minimum: {absorption_v:.2f}V")
    if float_voltages and max(float_voltages) - min(float_voltages) > 0.1:
        logging.warning(f"Float voltages differ across chargers: {float_voltages}. Using minimum: {float_v:.2f}V")
    
    return absorption_v, float_v, tail_current_a

