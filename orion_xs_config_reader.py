"""
Orion XS Configuration Reader
Reads charge algorithm settings from Victron Orion XS DC-DC chargers via VE.Direct VregLink interface

Orion XS has advanced features:
- Adaptive absorption time
- Engine running detection
- Li-ion specific charge profiles
"""

import dbus
import struct
import logging

# VE.Direct register addresses for Orion XS configuration
# Orion XS uses different register layout than MPPT

# Battery voltage settings (Orion XS specific addresses)
VREG_ABSORPTION_VOLTAGE = 0xEDF7     # Absorption voltage in 0.01V (16-bit LE)
VREG_FLOAT_VOLTAGE = 0xEDF6          # Float voltage in 0.01V (16-bit LE)
VREG_MAX_CHARGE_VOLTAGE = 0x2003     # Max charge voltage in 0.01V (16-bit LE)

# Charge timing settings (Orion XS specific)
VREG_ABSORPTION_TIME = 0xEDF3        # Max absorption time in minutes (16-bit LE)
VREG_ADAPTIVE_MODE = 0xEDF9          # Adaptive absorption enabled (0/1)
VREG_REPEATED_ABSORPTION_TIME = 0xEDF2   # Repeated absorption time in days (16-bit LE)
VREG_REPEATED_ABSORPTION_INTERVAL = 0xEDF1  # Days between repeated absorption (16-bit LE)

# Storage phase settings
VREG_STORAGE_VOLTAGE = 0xEDF4        # Storage voltage in 0.01V (16-bit LE)

# Equalization/Recondition settings (primarily for lead-acid)
VREG_EQUALIZATION_VOLTAGE = 0xEDF8   # Equalization voltage in 0.01V (16-bit LE)
VREG_EQUALIZATION_DURATION = 0xEDFB  # Equalization duration in minutes (16-bit LE)
VREG_AUTO_EQUALIZATION = 0xEDFA      # Auto equalization enabled (0/1)

# Current settings (verified on Orion XS 12/12-10A)
VREG_OUTPUT_CURRENT_LIMIT = 0xED26   # Output current limit in 0.1A (16-bit LE) - 100 = 10.0A
VREG_INPUT_CURRENT_LIMIT = 0xEDBE    # Input current limit in 0.1A (16-bit LE) - 130 = 13.0A

# Battery type
VREG_BATTERY_TYPE = 0xEDFF           # Battery type preset

# Engine detection (Orion XS specific feature)
VREG_ENGINE_SHUTDOWN_VOLTAGE = 0xEDFC  # Voltage below which to shut down

class OrionXSConfig:
    """Represents charge algorithm configuration from an Orion XS DC-DC charger"""
    
    def __init__(self, service_name):
        """
        Initialize Orion XS configuration reader
        
        Args:
            service_name: D-Bus service name (e.g. 'com.victronenergy.charger.ttyUSB2')
        """
        self.service_name = service_name
        
        # Voltage settings (in volts)
        self.absorption_voltage = None
        self.float_voltage = None
        self.max_charge_voltage = None
        self.engine_shutdown_voltage = None
        
        # Timing settings
        self.absorption_time = None
        self.adaptive_mode = None
        self.repeated_absorption_time = None   # days
        self.repeated_absorption_interval = None  # days
        
        # Current settings (in amps)
        self.output_current_limit = None  # Output to battery
        self.input_current_limit = None   # Input from alternator/source
        
        # Storage phase
        self.storage_voltage = None  # volts
        
        # Equalization/Recondition (lead-acid batteries)
        self.equalization_voltage = None  # volts
        self.equalization_duration = None  # minutes
        self.auto_equalization = None  # boolean
        
        # Battery characteristics
        self.battery_type = None
    
    def read_vreg(self, bus, vreg):
        """
        Read a vreg register from the Orion XS
        
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
        Read all charge configuration settings from the Orion XS
        
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
            
            # Max charge voltage (V) - 0.01V scaling
            data = self.read_vreg(bus, VREG_MAX_CHARGE_VOLTAGE)
            if data and len(data) >= 2:
                self.max_charge_voltage = struct.unpack('<H', data[:2])[0] / 100.0
                success_count += 1
            
            # Absorption time (minutes)
            data = self.read_vreg(bus, VREG_ABSORPTION_TIME)
            if data and len(data) >= 2:
                self.absorption_time = struct.unpack('<H', data[:2])[0]
                success_count += 1
            
            # Adaptive mode
            data = self.read_vreg(bus, VREG_ADAPTIVE_MODE)
            if data and len(data) >= 2:
                self.adaptive_mode = struct.unpack('<H', data[:2])[0] == 1
                success_count += 1
            
            # Try to read output current from D-Bus first (more reliable)
            try:
                obj = bus.get_object(self.service_name, "/Settings/ChargeCurrentLimit")
                self.output_current_limit = float(obj.GetValue())
                success_count += 1
            except:
                # Fall back to VregLink
                data = self.read_vreg(bus, VREG_OUTPUT_CURRENT_LIMIT)
                if data and len(data) >= 2:
                    self.output_current_limit = struct.unpack('<H', data[:2])[0] / 10.0
                    success_count += 1
            
            # Input current limit (A) - 0.1A scaling
            data = self.read_vreg(bus, VREG_INPUT_CURRENT_LIMIT)
            if data and len(data) >= 2:
                self.input_current_limit = struct.unpack('<H', data[:2])[0] / 10.0
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
            
            # Engine shutdown voltage (V) - 0.01V scaling
            data = self.read_vreg(bus, VREG_ENGINE_SHUTDOWN_VOLTAGE)
            if data and len(data) >= 2:
                self.engine_shutdown_voltage = struct.unpack('<H', data[:2])[0] / 100.0
                success_count += 1
            
            return success_count > 0
        
        except Exception as e:
            logging.error(f"Error reading Orion XS config from {self.service_name}: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False
    
    def log_all_settings(self):
        """Log all readable charge configuration settings from Orion XS"""
        logging.info(f"=== Orion XS Charge Configuration: {self.service_name} ===")
        
        battery_types = {
            0: "Lead Acid",
            1: "LiFePO4",
            2: "Li-ion"
        }
        
        # Critical charge voltages
        if self.absorption_voltage is not None:
            logging.info(f"  Absorption Voltage: {self.absorption_voltage:.2f} V")
        if self.float_voltage is not None:
            logging.info(f"  Float Voltage: {self.float_voltage:.2f} V")
        if self.max_charge_voltage is not None:
            logging.info(f"  Max Charge Voltage: {self.max_charge_voltage:.2f} V")
        
        # Charge timing
        if self.absorption_time is not None:
            logging.info(f"  Absorption Time: {self.absorption_time} min")
        if self.adaptive_mode is not None:
            logging.info(f"  Adaptive Absorption: {'Enabled' if self.adaptive_mode else 'Disabled'}")
        
        # Current limits
        if self.output_current_limit is not None:
            logging.info(f"  Output Current Limit: {self.output_current_limit:.1f} A")
        if self.input_current_limit is not None:
            logging.info(f"  Input Current Limit: {self.input_current_limit:.1f} A")
        
        # Battery characteristics
        if self.battery_type is not None:
            type_str = battery_types.get(self.battery_type, f"Unknown ({self.battery_type})")
            logging.info(f"  Battery Type: {type_str}")
        
        # Engine detection
        if self.engine_shutdown_voltage is not None:
            logging.info(f"  Engine Shutdown Voltage: {self.engine_shutdown_voltage:.2f} V")
        
        logging.info(f"=== End Configuration ===")
    
    def __str__(self):
        """String representation of configuration"""
        lines = [f"Orion XS Config ({self.service_name}):"]
        if self.absorption_voltage is not None:
            lines.append(f"  Absorption: {self.absorption_voltage:.2f} V")
        if self.float_voltage is not None:
            lines.append(f"  Float: {self.float_voltage:.2f} V")
        if self.output_current_limit is not None:
            lines.append(f"  Current Limit: {self.output_current_limit:.1f} A")
        return "\n".join(lines)

