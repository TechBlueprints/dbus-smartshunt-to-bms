"""
SmartCharger Configuration Reader
Reads charge algorithm settings from Victron AC battery chargers (Skylla, Centaur, Phoenix Smart, etc.)

SmartChargers are AC-powered battery chargers with similar charge algorithms to MPPTs
but with AC input instead of solar PV input.
"""

import dbus
import struct
import logging

# VE.Direct register addresses for SmartCharger configuration
# Similar to MPPT but may have different features

# Battery voltage settings - Same as MPPT
VREG_FLOAT_VOLTAGE = 0xEDF6          # Float voltage in 0.01V (16-bit LE)
VREG_ABSORPTION_VOLTAGE = 0xEDF7     # Absorption voltage in 0.01V (16-bit LE)
VREG_MAX_CHARGE_VOLTAGE = 0x2003     # Max charge voltage in 0.01V (16-bit LE)

# Charge timing settings
VREG_ABSORPTION_TIME = 0xEDF3        # Max absorption time in minutes (16-bit LE)
VREG_ADAPTIVE_MODE = 0xEDF9          # Adaptive absorption enabled (0/1)

# Current settings
VREG_CHARGE_CURRENT_LIMIT = 0xEDF5   # Charge current limit in 0.1A (16-bit LE)

# Battery type
VREG_BATTERY_TYPE = 0xEDFF           # Battery type preset

class SmartChargerConfig:
    """Represents charge algorithm configuration from a Victron AC battery charger"""
    
    def __init__(self, service_name):
        """
        Initialize SmartCharger configuration reader
        
        Args:
            service_name: D-Bus service name (e.g. 'com.victronenergy.charger.ttyUSB3')
        """
        self.service_name = service_name
        
        # Voltage settings (in volts)
        self.absorption_voltage = None
        self.float_voltage = None
        self.max_charge_voltage = None
        
        # Timing settings
        self.absorption_time = None
        self.adaptive_mode = None
        
        # Current settings (in amps)
        self.charge_current_limit = None
        
        # Battery characteristics
        self.battery_type = None
    
    def read_vreg(self, bus, vreg):
        """
        Read a vreg register from the SmartCharger
        
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
        Read all charge configuration settings from the SmartCharger
        
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
            
            # Charge current limit (A) - 0.1A scaling
            data = self.read_vreg(bus, VREG_CHARGE_CURRENT_LIMIT)
            if data and len(data) >= 2:
                self.charge_current_limit = struct.unpack('<H', data[:2])[0] / 10.0
                success_count += 1
            
            # Battery type
            data = self.read_vreg(bus, VREG_BATTERY_TYPE)
            if data and len(data) >= 2:
                self.battery_type = struct.unpack('<H', data[:2])[0]
                success_count += 1
            
            return success_count > 0
        
        except Exception as e:
            logging.error(f"Error reading SmartCharger config from {self.service_name}: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False
    
    def log_all_settings(self):
        """Log all readable charge configuration settings from SmartCharger"""
        logging.info(f"=== SmartCharger Configuration: {self.service_name} ===")
        
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
        if self.max_charge_voltage is not None:
            logging.info(f"  Max Charge Voltage: {self.max_charge_voltage:.2f} V")
        
        # Charge timing
        if self.absorption_time is not None:
            logging.info(f"  Absorption Time: {self.absorption_time} min")
        if self.adaptive_mode is not None:
            logging.info(f"  Adaptive Absorption: {'Enabled' if self.adaptive_mode else 'Disabled'}")
        
        # Current limit
        if self.charge_current_limit is not None:
            logging.info(f"  Charge Current Limit: {self.charge_current_limit:.1f} A")
        
        # Battery characteristics
        if self.battery_type is not None:
            type_str = battery_types.get(self.battery_type, f"Unknown ({self.battery_type})")
            logging.info(f"  Battery Type: {type_str}")
        
        logging.info(f"=== End Configuration ===")
    
    def __str__(self):
        """String representation of configuration"""
        lines = [f"SmartCharger Config ({self.service_name}):"]
        if self.absorption_voltage is not None:
            lines.append(f"  Absorption: {self.absorption_voltage:.2f} V")
        if self.float_voltage is not None:
            lines.append(f"  Float: {self.float_voltage:.2f} V")
        if self.charge_current_limit is not None:
            lines.append(f"  Current Limit: {self.charge_current_limit:.1f} A")
        return "\n".join(lines)

