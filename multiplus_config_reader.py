"""
Multiplus Configuration Reader
Reads charge algorithm settings from Victron Multiplus inverter/chargers

NOTE: Multiplus does NOT expose VregLink interface on D-Bus.
Configuration is stored internally and only accessible via:
1. VEConfigure software (MK2/MK3 USB interface)
2. Remote VEConfigure (via VRM portal)

This reader attempts to get whatever charge-related data IS available on D-Bus,
but full charge algorithm settings are not accessible programmatically.
"""

import dbus
import logging

class MultiplusConfig:
    """Represents available charge-related data from a Multiplus"""
    
    def __init__(self, service_name):
        """
        Initialize Multiplus configuration reader
        
        Args:
            service_name: D-Bus service name (e.g. 'com.victronenergy.vebus.ttyS4')
        """
        self.service_name = service_name
        
        # What we CAN read from D-Bus
        self.ac_input_current_limit = None
        self.inverter_output_voltage = None
        self.power_assist_enabled = None
        self.ups_function_enabled = None
        
        # What we CANNOT read (stored internally in Multiplus firmware)
        # These must be read via VEConfigure:
        # - absorption_voltage
        # - float_voltage
        # - charge_current_limit
        # - battery_type
        # - adaptive_mode
        # - tail_current
        
        # Battery operational limits (received FROM BMS, not Multiplus config)
        self.bms_max_charge_voltage = None
        self.bms_max_charge_current = None
        self.bms_max_discharge_current = None
    
    def read_dbus_path(self, bus, path):
        """
        Read a D-Bus path value
        
        Args:
            bus: D-Bus system bus
            path: Path to read
            
        Returns:
            Value or None if error
        """
        try:
            obj = bus.get_object(self.service_name, path)
            iface = dbus.Interface(obj, 'com.victronenergy.BusItem')
            return iface.GetValue()
        except Exception as e:
            logging.debug(f"Error reading {path} from {self.service_name}: {e}")
            return None
    
    def read_all(self, bus):
        """
        Read all available configuration from D-Bus
        
        Args:
            bus: D-Bus system bus
            
        Returns:
            bool: True if at least one value was read successfully
        """
        success_count = 0
        
        try:
            # AC input current limit
            value = self.read_dbus_path(bus, '/Ac/In/1/CurrentLimit')
            if value is not None:
                self.ac_input_current_limit = float(value)
                success_count += 1
            
            # Inverter output voltage
            value = self.read_dbus_path(bus, '/Devices/0/Settings/InverterOutputVoltage')
            if value is not None:
                self.inverter_output_voltage = float(value)
                success_count += 1
            
            # Power assist
            value = self.read_dbus_path(bus, '/Settings/PowerAssistEnabled')
            if value is not None:
                self.power_assist_enabled = bool(value)
                success_count += 1
            
            # UPS function
            value = self.read_dbus_path(bus, '/Settings/UpsFunction')
            if value is not None:
                self.ups_function_enabled = bool(value)
                success_count += 1
            
            # Battery operational limits (what BMS is telling Multiplus)
            value = self.read_dbus_path(bus, '/BatteryOperationalLimits/MaxChargeVoltage')
            if value is not None and value != []:
                self.bms_max_charge_voltage = float(value)
                success_count += 1
            
            value = self.read_dbus_path(bus, '/BatteryOperationalLimits/MaxChargeCurrent')
            if value is not None and value != []:
                self.bms_max_charge_current = float(value)
                success_count += 1
            
            value = self.read_dbus_path(bus, '/BatteryOperationalLimits/MaxDischargeCurrent')
            if value is not None and value != []:
                self.bms_max_discharge_current = float(value)
                success_count += 1
            
            return success_count > 0
        
        except Exception as e:
            logging.error(f"Error reading Multiplus config from {self.service_name}: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False
    
    def log_all_settings(self):
        """Log all readable configuration from Multiplus"""
        logging.info(f"=== Multiplus Configuration: {self.service_name} ===")
        logging.info("  NOTE: Battery charge voltages (absorption/float) are NOT accessible via D-Bus")
        logging.info("  Use VEConfigure or Remote VEConfigure to view/modify these settings")
        
        # Available settings
        if self.ac_input_current_limit is not None:
            logging.info(f"  AC Input Current Limit: {self.ac_input_current_limit:.1f} A")
        if self.inverter_output_voltage is not None:
            logging.info(f"  Inverter Output Voltage: {self.inverter_output_voltage:.0f} V")
        if self.power_assist_enabled is not None:
            logging.info(f"  Power Assist: {'Enabled' if self.power_assist_enabled else 'Disabled'}")
        if self.ups_function_enabled is not None:
            logging.info(f"  UPS Function: {'Enabled' if self.ups_function_enabled else 'Disabled'}")
        
        # BMS operational limits (what Multiplus is receiving)
        if self.bms_max_charge_voltage is not None:
            logging.info(f"  BMS Max Charge Voltage: {self.bms_max_charge_voltage:.2f} V (from BMS)")
        if self.bms_max_charge_current is not None:
            logging.info(f"  BMS Max Charge Current: {self.bms_max_charge_current:.1f} A (from BMS)")
        if self.bms_max_discharge_current is not None:
            logging.info(f"  BMS Max Discharge Current: {self.bms_max_discharge_current:.1f} A (from BMS)")
        
        logging.info(f"=== End Configuration ===")
    
    def __str__(self):
        """String representation of configuration"""
        lines = [f"Multiplus Config ({self.service_name}):"]
        lines.append("  (Battery charge voltages not accessible via D-Bus)")
        if self.ac_input_current_limit is not None:
            lines.append(f"  AC Input Limit: {self.ac_input_current_limit:.1f} A")
        if self.bms_max_charge_voltage is not None:
            lines.append(f"  BMS CVL: {self.bms_max_charge_voltage:.2f} V")
        return "\n".join(lines)

