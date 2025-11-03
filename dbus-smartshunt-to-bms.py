#!/usr/bin/env python3

"""
Service to convert Victron SmartShunts into virtual Battery Management Systems (BMS).

For each SmartShunt, creates a virtual BMS that:
- Passes through all SmartShunt data (voltage, current, SoC, alarms, etc.)
- Adds BMS functionality (CVL/CCL/DCL, AllowToCharge/Discharge)
- Enables DVCC charge control for "dumb" batteries

Author: Based on dbus-aggregate-batteries by Dr-Gigavolt
License: MIT
"""

from gi.repository import GLib
import logging
import sys
import os
import platform
import dbus
import time as tt

# Add ext folder to sys.path
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext", "velib_python"))

from vedbus import VeDbusService
from dbusmonitor import DbusMonitor

VERSION = "1.0.0"


class SystemBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SYSTEM)


class SessionBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SESSION)


def get_bus():
    return SessionBus() if "DBUS_SESSION_BUS_ADDRESS" in os.environ else SystemBus()


class SmartShuntBMS:
    """
    Creates a virtual BMS for a single SmartShunt.
    Passes through SmartShunt data and adds BMS charge control capabilities.
    """
    
    def __init__(self, config, source_service, device_instance):
        """
        Args:
            config: Configuration dictionary
            source_service: D-Bus service name of the source SmartShunt (e.g., 'com.victronenergy.battery.ttyS5')
            device_instance: Device instance ID from the source SmartShunt
        """
        self.config = config
        self.source_service = source_service
        self.device_instance = device_instance
        self._dbusConn = get_bus()
        self._updating = False
        
        # Create unique service name based on device instance
        servicename = f"com.victronenergy.battery.smartshunt_bms_{device_instance}"
        
        logging.info(f"### Creating virtual BMS for {source_service} (instance {device_instance})")
        logging.info(f"### Service name: {servicename}")
        
        self._dbusservice = VeDbusService(servicename, self._dbusConn, register=False)
        
        # Create management objects
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path("/Mgmt/ProcessVersion", "Python " + platform.python_version())
        self._dbusservice.add_path("/Mgmt/Connection", f"SmartShunt BMS Converter ({source_service})")
        
        # Device identification - use BMS ProductId (0xBA77)
        self._dbusservice.add_path("/DeviceInstance", device_instance + 100)  # Offset to avoid conflicts
        self._dbusservice.add_path("/ProductId", 0xBA77,  # BMS
            gettextcallback=lambda a, x: f"0x{x:X}" if x and isinstance(x, int) else "")
        
        # Custom name (will be updated from source SmartShunt)
        device_name = config.get('DEVICE_NAME', f"SmartShunt BMS {device_instance}")
        self._dbusservice.add_path("/ProductName", device_name)
        
        # These will be mirrored from source SmartShunt
        self._dbusservice.add_path("/FirmwareVersion", VERSION)
        self._dbusservice.add_path("/HardwareVersion", [],
            gettextcallback=lambda a, x: "")
        self._dbusservice.add_path("/Serial", "SSBMS_" + str(device_instance))
        self._dbusservice.add_path("/CustomName", device_name)
        self._dbusservice.add_path("/Connected", 1)
        
        # Create all SmartShunt data paths (pass-through)
        self._create_smartshunt_paths()
        
        # Add BMS-specific paths for charge control
        self._create_bms_paths()
        
        # Create /Devices/0/* for the virtual BMS itself
        self._create_device_info()
        
        # Register the service
        self._dbusservice.register()
        logging.info(f"✓ Virtual BMS registered: {servicename}")
        
        # Initialize DBus monitor to watch the source SmartShunt
        self._init_dbusmonitor()
    
    def _create_smartshunt_paths(self):
        """Create all standard SmartShunt paths that will be passed through"""
        
        # DC measurements
        self._dbusservice.add_path("/Dc/0/Voltage", None,
            gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None else "")
        self._dbusservice.add_path("/Dc/0/Current", None,
            gettextcallback=lambda a, x: "{:.2f}A".format(x) if x is not None else "")
        self._dbusservice.add_path("/Dc/0/Power", None,
            gettextcallback=lambda a, x: "{:.0f}W".format(x) if x is not None else "")
        self._dbusservice.add_path("/Dc/0/Temperature", None,
            gettextcallback=lambda a, x: "{:.0f}C".format(x) if x is not None else "")
        self._dbusservice.add_path("/Dc/0/MidVoltage", None,
            gettextcallback=lambda a, x: "" if x is None or x == [] else "{:.2f}V".format(x))
        self._dbusservice.add_path("/Dc/0/MidVoltageDeviation", None,
            gettextcallback=lambda a, x: "" if x is None or x == [] else "{:.1f}%".format(x))
        self._dbusservice.add_path("/Dc/1/Voltage", None,
            gettextcallback=lambda a, x: "" if x is None or x == [] else "{:.2f}V".format(x))
        
        # State of Charge and capacity
        self._dbusservice.add_path("/Soc", None,
            gettextcallback=lambda a, x: "{:.1f}%".format(x) if x is not None else "")
        self._dbusservice.add_path("/ConsumedAmphours", None,
            gettextcallback=lambda a, x: "{:.1f}Ah".format(x) if x is not None else "")
        self._dbusservice.add_path("/TimeToGo", None, writeable=True,
            gettextcallback=lambda a, x: "{:.0f}s".format(x) if x is not None and x != [] else "")
        
        # Alarms
        self._dbusservice.add_path("/Alarms/Alarm", None)
        self._dbusservice.add_path("/Alarms/LowVoltage", None)
        self._dbusservice.add_path("/Alarms/HighVoltage", None)
        self._dbusservice.add_path("/Alarms/LowSoc", None)
        self._dbusservice.add_path("/Alarms/HighTemperature", None)
        self._dbusservice.add_path("/Alarms/LowTemperature", None)
        self._dbusservice.add_path("/Alarms/MidVoltage", None)
        self._dbusservice.add_path("/Alarms/LowStarterVoltage", None)
        self._dbusservice.add_path("/Alarms/HighStarterVoltage", None)
        
        # History data
        self._dbusservice.add_path("/History/ChargeCycles", None)
        self._dbusservice.add_path("/History/TotalAhDrawn", None,
            gettextcallback=lambda a, x: "{:.1f}Ah".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/MinimumVoltage", None,
            gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/MaximumVoltage", None,
            gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/TimeSinceLastFullCharge", None,
            gettextcallback=lambda a, x: "{:.0f}s".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/AutomaticSyncs", None)
        self._dbusservice.add_path("/History/LowVoltageAlarms", None)
        self._dbusservice.add_path("/History/HighVoltageAlarms", None)
        self._dbusservice.add_path("/History/LastDischarge", None,
            gettextcallback=lambda a, x: "{:.1f}Ah".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/AverageDischarge", None,
            gettextcallback=lambda a, x: "{:.1f}Ah".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/ChargedEnergy", None,
            gettextcallback=lambda a, x: "{:.2f}kWh".format(x/1000) if x is not None else "")
        self._dbusservice.add_path("/History/DischargedEnergy", None,
            gettextcallback=lambda a, x: "{:.2f}kWh".format(x/1000) if x is not None else "")
        self._dbusservice.add_path("/History/FullDischarges", None)
        self._dbusservice.add_path("/History/DeepestDischarge", None,
            gettextcallback=lambda a, x: "{:.1f}Ah".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/MinimumStarterVoltage", None,
            gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None and x != 0 else "")
        self._dbusservice.add_path("/History/MaximumStarterVoltage", None,
            gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None and x != 0 else "")
        self._dbusservice.add_path("/History/LowStarterVoltageAlarms", [],
            gettextcallback=lambda a, x: "" if x is None or x == [] else str(x))
        self._dbusservice.add_path("/History/HighStarterVoltageAlarms", [],
            gettextcallback=lambda a, x: "" if x is None or x == [] else str(x))
        
        # Settings
        self._dbusservice.add_path("/Settings/HasMidVoltage", 0)
        self._dbusservice.add_path("/Settings/HasStarterVoltage", 0)
        self._dbusservice.add_path("/Settings/HasTemperature", 1)
        self._dbusservice.add_path("/Settings/RelayMode", [],
            gettextcallback=lambda a, x: "")
        
        # Relay
        self._dbusservice.add_path("/Relay/0/State", [],
            gettextcallback=lambda a, x: "")
        
        # Group ID
        self._dbusservice.add_path("/GroupId", [],
            gettextcallback=lambda a, x: "")
        
        # VE.Direct communication error counters
        self._dbusservice.add_path("/VEDirect/HexChecksumErrors", None)
        self._dbusservice.add_path("/VEDirect/HexInvalidCharacterErrors", None)
        self._dbusservice.add_path("/VEDirect/HexUnfinishedErrors", None)
        self._dbusservice.add_path("/VEDirect/TextChecksumErrors", None)
        self._dbusservice.add_path("/VEDirect/TextParseError", None)
        self._dbusservice.add_path("/VEDirect/TextUnfinishedErrors", None)
    
    def _create_bms_paths(self):
        """Create BMS-specific paths for charge control"""
        
        # BMS charge limits (DVCC will read these)
        cvl = self.config.get('MAX_CHARGE_VOLTAGE')
        ccl = self.config.get('MAX_CHARGE_CURRENT')
        dcl = self.config.get('MAX_DISCHARGE_CURRENT')
        
        if cvl:
            self._dbusservice.add_path("/Info/MaxChargeVoltage", cvl,
                gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None else "")
        if ccl:
            self._dbusservice.add_path("/Info/MaxChargeCurrent", ccl,
                gettextcallback=lambda a, x: "{:.1f}A".format(x) if x is not None else "")
        if dcl:
            self._dbusservice.add_path("/Info/MaxDischargeCurrent", dcl,
                gettextcallback=lambda a, x: "{:.1f}A".format(x) if x is not None else "")
        
        # Capacity (BMS-specific, not present on physical SmartShunts)
        # We'll try to read this from the SmartShunt's configuration
        self._dbusservice.add_path("/Capacity", None,
            gettextcallback=lambda a, x: "{:.0f}Ah".format(x) if x is not None else "")
        self._dbusservice.add_path("/InstalledCapacity", None,
            gettextcallback=lambda a, x: "{:.0f}Ah".format(x) if x is not None else "")
        
        # Charge/Discharge control flags
        self._dbusservice.add_path("/Io/AllowToCharge", 1)
        self._dbusservice.add_path("/Io/AllowToDischarge", 1)
    
    def _create_device_info(self):
        """Create /Devices/0/* for the virtual BMS itself"""
        device_name = self.config.get('DEVICE_NAME', f"SmartShunt BMS {self.device_instance}")
        
        self._dbusservice.add_path("/Devices/0/CustomName", device_name)
        self._dbusservice.add_path("/Devices/0/DeviceInstance", self.device_instance + 100)
        self._dbusservice.add_path("/Devices/0/FirmwareVersion", VERSION)
        self._dbusservice.add_path("/Devices/0/ProductId", 0xBA77,
            gettextcallback=lambda a, x: f"0x{x:X}" if x and isinstance(x, int) else "")
        self._dbusservice.add_path("/Devices/0/ProductName", "Virtual BMS (SmartShunt)")
        self._dbusservice.add_path("/Devices/0/ServiceName", f"com.victronenergy.battery.smartshunt_bms_{self.device_instance}")
        self._dbusservice.add_path("/Devices/0/VregLink", [],
            gettextcallback=lambda a, x: "")
    
    def _init_dbusmonitor(self):
        """Initialize DBus monitor to watch the source SmartShunt"""
        
        dummy = {"code": None, "whenToLog": "configChange", "accessLevel": None}
        
        # Monitor all paths from the source SmartShunt
        monitorlist = {
            self.source_service: {
                "/ProductName": dummy,
                "/ProductId": dummy,
                "/CustomName": dummy,
                "/Serial": dummy,
                "/FirmwareVersion": dummy,
                "/HardwareVersion": dummy,
                "/Dc/0/Voltage": dummy,
                "/Dc/0/Current": dummy,
                "/Dc/0/Power": dummy,
                "/Dc/0/Temperature": dummy,
                "/Dc/0/MidVoltage": dummy,
                "/Dc/0/MidVoltageDeviation": dummy,
                "/Dc/1/Voltage": dummy,
                "/Soc": dummy,
                "/ConsumedAmphours": dummy,
                "/TimeToGo": dummy,
                "/Connected": dummy,
                "/Alarms/Alarm": dummy,
                "/Alarms/LowVoltage": dummy,
                "/Alarms/HighVoltage": dummy,
                "/Alarms/LowSoc": dummy,
                "/Alarms/HighTemperature": dummy,
                "/Alarms/LowTemperature": dummy,
                "/Alarms/MidVoltage": dummy,
                "/Alarms/LowStarterVoltage": dummy,
                "/Alarms/HighStarterVoltage": dummy,
                "/History/ChargeCycles": dummy,
                "/History/TotalAhDrawn": dummy,
                "/History/MinimumVoltage": dummy,
                "/History/MaximumVoltage": dummy,
                "/History/TimeSinceLastFullCharge": dummy,
                "/History/AutomaticSyncs": dummy,
                "/History/LowVoltageAlarms": dummy,
                "/History/HighVoltageAlarms": dummy,
                "/History/LastDischarge": dummy,
                "/History/AverageDischarge": dummy,
                "/History/ChargedEnergy": dummy,
                "/History/DischargedEnergy": dummy,
                "/History/FullDischarges": dummy,
                "/History/DeepestDischarge": dummy,
                "/History/MinimumStarterVoltage": dummy,
                "/History/MaximumStarterVoltage": dummy,
                "/History/LowStarterVoltageAlarms": dummy,
                "/History/HighStarterVoltageAlarms": dummy,
                "/Relay/0/State": dummy,
                "/Settings/HasMidVoltage": dummy,
                "/Settings/HasStarterVoltage": dummy,
                "/Settings/HasTemperature": dummy,
                "/Settings/RelayMode": dummy,
                "/GroupId": dummy,
                "/VEDirect/HexChecksumErrors": dummy,
                "/VEDirect/HexInvalidCharacterErrors": dummy,
                "/VEDirect/HexUnfinishedErrors": dummy,
                "/VEDirect/TextChecksumErrors": dummy,
                "/VEDirect/TextParseError": dummy,
                "/VEDirect/TextUnfinishedErrors": dummy,
            }
        }
        
        self._dbusmonitor = DbusMonitor(monitorlist, self._dbus_value_changed)
        logging.info(f"✓ Monitoring {self.source_service}")
    
    def _dbus_value_changed(self, dbusServiceName, dbusPath, options, changes, deviceInstance):
        """Called when a monitored D-Bus value changes"""
        
        if self._updating:
            return
        
        # Trigger update on any change
        GLib.idle_add(self._update)
    
    def _update(self):
        """Update virtual BMS with data from source SmartShunt"""
        
        if self._updating:
            return True
        
        self._updating = True
        
        try:
            bus = self._dbusservice
            monitor = self._dbusmonitor
            service = self.source_service
            
            # Helper to get value from source SmartShunt
            def get_value(path, default=None):
                try:
                    val = monitor.get_value(service, path)
                    return val if val is not None else default
                except:
                    return default
            
            # Pass through all SmartShunt data
            bus["/Dc/0/Voltage"] = get_value("/Dc/0/Voltage")
            bus["/Dc/0/Current"] = get_value("/Dc/0/Current")
            bus["/Dc/0/Power"] = get_value("/Dc/0/Power")
            bus["/Dc/0/Temperature"] = get_value("/Dc/0/Temperature")
            bus["/Dc/0/MidVoltage"] = get_value("/Dc/0/MidVoltage")
            bus["/Dc/0/MidVoltageDeviation"] = get_value("/Dc/0/MidVoltageDeviation")
            bus["/Dc/1/Voltage"] = get_value("/Dc/1/Voltage")
            
            bus["/Soc"] = get_value("/Soc")
            bus["/ConsumedAmphours"] = get_value("/ConsumedAmphours")
            
            ttg = get_value("/TimeToGo")
            bus["/TimeToGo"] = ttg if ttg is not None else []
            
            # Pass through alarms
            bus["/Alarms/Alarm"] = get_value("/Alarms/Alarm", 0)
            bus["/Alarms/LowVoltage"] = get_value("/Alarms/LowVoltage", 0)
            bus["/Alarms/HighVoltage"] = get_value("/Alarms/HighVoltage", 0)
            bus["/Alarms/LowSoc"] = get_value("/Alarms/LowSoc", 0)
            bus["/Alarms/HighTemperature"] = get_value("/Alarms/HighTemperature", 0)
            bus["/Alarms/LowTemperature"] = get_value("/Alarms/LowTemperature", 0)
            bus["/Alarms/MidVoltage"] = get_value("/Alarms/MidVoltage", 0)
            bus["/Alarms/LowStarterVoltage"] = get_value("/Alarms/LowStarterVoltage", 0)
            bus["/Alarms/HighStarterVoltage"] = get_value("/Alarms/HighStarterVoltage", 0)
            
            # Pass through history
            bus["/History/ChargeCycles"] = get_value("/History/ChargeCycles")
            bus["/History/TotalAhDrawn"] = get_value("/History/TotalAhDrawn")
            bus["/History/MinimumVoltage"] = get_value("/History/MinimumVoltage")
            bus["/History/MaximumVoltage"] = get_value("/History/MaximumVoltage")
            bus["/History/TimeSinceLastFullCharge"] = get_value("/History/TimeSinceLastFullCharge")
            bus["/History/AutomaticSyncs"] = get_value("/History/AutomaticSyncs")
            bus["/History/LowVoltageAlarms"] = get_value("/History/LowVoltageAlarms")
            bus["/History/HighVoltageAlarms"] = get_value("/History/HighVoltageAlarms")
            bus["/History/LastDischarge"] = get_value("/History/LastDischarge")
            bus["/History/AverageDischarge"] = get_value("/History/AverageDischarge")
            bus["/History/ChargedEnergy"] = get_value("/History/ChargedEnergy")
            bus["/History/DischargedEnergy"] = get_value("/History/DischargedEnergy")
            bus["/History/FullDischarges"] = get_value("/History/FullDischarges")
            bus["/History/DeepestDischarge"] = get_value("/History/DeepestDischarge")
            bus["/History/MinimumStarterVoltage"] = get_value("/History/MinimumStarterVoltage")
            bus["/History/MaximumStarterVoltage"] = get_value("/History/MaximumStarterVoltage")
            
            # Pass through settings
            bus["/Settings/HasMidVoltage"] = get_value("/Settings/HasMidVoltage", 0)
            bus["/Settings/HasStarterVoltage"] = get_value("/Settings/HasStarterVoltage", 0)
            bus["/Settings/HasTemperature"] = get_value("/Settings/HasTemperature", 1)
            
            # Pass through VE.Direct errors
            bus["/VEDirect/HexChecksumErrors"] = get_value("/VEDirect/HexChecksumErrors", 0)
            bus["/VEDirect/HexInvalidCharacterErrors"] = get_value("/VEDirect/HexInvalidCharacterErrors", 0)
            bus["/VEDirect/HexUnfinishedErrors"] = get_value("/VEDirect/HexUnfinishedErrors", 0)
            bus["/VEDirect/TextChecksumErrors"] = get_value("/VEDirect/TextChecksumErrors", 0)
            bus["/VEDirect/TextParseError"] = get_value("/VEDirect/TextParseError", 0)
            bus["/VEDirect/TextUnfinishedErrors"] = get_value("/VEDirect/TextUnfinishedErrors", 0)
            
            # BMS-specific: Update AllowToCharge/AllowToDischarge based on alarms and temperature
            allow_charge = 1
            allow_discharge = 1
            
            # Don't allow charging if there are high voltage or high temperature alarms
            if get_value("/Alarms/HighVoltage", 0) > 0:
                allow_charge = 0
                logging.debug(f"{service}: Charging disabled (high voltage alarm)")
            
            if get_value("/Alarms/HighTemperature", 0) > 0:
                allow_charge = 0
                logging.debug(f"{service}: Charging disabled (high temperature alarm)")
            
            # Check temperature thresholds
            temp = get_value("/Dc/0/Temperature")
            if temp is not None:
                if temp >= self.config.get('TEMP_HOT_DANGER', 45.0):
                    allow_charge = 0
                    logging.debug(f"{service}: Charging disabled (temperature {temp}°C >= {self.config.get('TEMP_HOT_DANGER')}°C)")
                elif temp <= self.config.get('TEMP_COLD_DANGER', 0.0):
                    allow_charge = 0
                    logging.debug(f"{service}: Charging disabled (temperature {temp}°C <= {self.config.get('TEMP_COLD_DANGER')}°C)")
            
            # Don't allow discharging if there are low voltage or low temperature alarms
            if get_value("/Alarms/LowVoltage", 0) > 0:
                allow_discharge = 0
                logging.debug(f"{service}: Discharging disabled (low voltage alarm)")
            
            if get_value("/Alarms/LowTemperature", 0) > 0:
                allow_discharge = 0
                logging.debug(f"{service}: Discharging disabled (low temperature alarm)")
            
            bus["/Io/AllowToCharge"] = allow_charge
            bus["/Io/AllowToDischarge"] = allow_discharge
            
        except Exception as e:
            logging.error(f"Error updating {self.source_service}: {e}")
            import traceback
            logging.error(traceback.format_exc())
        finally:
            self._updating = False
        
        return True


class SmartShuntToBMSManager:
    """
    Main service manager that discovers SmartShunts and creates virtual BMS for each one.
    """
    
    def __init__(self, config):
        self.config = config
        self._dbusConn = get_bus()
        self._bms_services = {}  # Map of device_instance -> SmartShuntBMS
        
        logging.info("### SmartShunt to BMS Converter ###")
        logging.info(f"Version: {VERSION}")
        
        # Initial discovery
        GLib.timeout_add(1000, self._discover_smartshunts)
        
        # Periodic re-discovery (every 30 seconds)
        GLib.timeout_add(30000, self._discover_smartshunts)
    
    def _discover_smartshunts(self):
        """Discover SmartShunts and create virtual BMS for each"""
        
        try:
            bus = dbus.SystemBus()
            
            # Find all SmartShunt services
            smartshunts = []
            for service_name in bus.list_names():
                if service_name.startswith('com.victronenergy.battery.'):
                    # Skip excluded services
                    if service_name in self.config.get('EXCLUDE_SHUNTS', []):
                        continue
                    
                    # Skip our own virtual BMS services
                    if 'smartshunt_bms_' in service_name:
                        continue
                    
                    try:
                        # Check if it's a SmartShunt (ProductId 0xA389)
                        obj = bus.get_object(service_name, '/ProductId')
                        iface = dbus.Interface(obj, 'com.victronenergy.BusItem')
                        product_id = iface.GetValue()
                        
                        if product_id == 0xA389:  # SmartShunt
                            # Skip virtual aggregates (check for /Devices/0/Virtual flag)
                            try:
                                obj = bus.get_object(service_name, '/Devices/0/Virtual')
                                iface = dbus.Interface(obj, 'com.victronenergy.BusItem')
                                is_virtual = iface.GetValue()
                                if is_virtual:
                                    logging.info(f"Skipping virtual aggregate: {service_name}")
                                    continue
                            except:
                                # Path doesn't exist - this is a real physical SmartShunt
                                pass
                            
                            # Get device instance
                            obj = bus.get_object(service_name, '/DeviceInstance')
                            device_instance = int(obj.Get('com.victronenergy.BusItem', 'Value', dbus_interface='org.freedesktop.DBus.Properties'))
                            
                            # Get custom name for logging
                            try:
                                obj = bus.get_object(service_name, '/CustomName')
                                custom_name = str(obj.Get('com.victronenergy.BusItem', 'Value', dbus_interface='org.freedesktop.DBus.Properties'))
                            except:
                                custom_name = service_name
                            
                            smartshunts.append((service_name, device_instance, custom_name))
                    except:
                        pass
            
            if not smartshunts:
                logging.warning("No SmartShunts found!")
                return True
            
            logging.info(f"Found {len(smartshunts)} SmartShunt(s)")
            
            # Create or update virtual BMS for each SmartShunt
            current_instances = set()
            for service_name, device_instance, custom_name in smartshunts:
                current_instances.add(device_instance)
                
                if device_instance not in self._bms_services:
                    # Create new virtual BMS
                    logging.info(f"Creating virtual BMS for: {custom_name} ({service_name}, instance {device_instance})")
                    try:
                        bms = SmartShuntBMS(self.config, service_name, device_instance)
                        self._bms_services[device_instance] = bms
                        logging.info(f"✓ Virtual BMS created for instance {device_instance}")
                    except Exception as e:
                        logging.error(f"Failed to create virtual BMS for {service_name}: {e}")
                        import traceback
                        logging.error(traceback.format_exc())
            
            # Remove BMS for SmartShunts that disappeared
            for device_instance in list(self._bms_services.keys()):
                if device_instance not in current_instances:
                    logging.info(f"SmartShunt instance {device_instance} disappeared, removing virtual BMS")
                    del self._bms_services[device_instance]
            
        except Exception as e:
            logging.error(f"Error discovering SmartShunts: {e}")
            import traceback
            logging.error(traceback.format_exc())
        
        return True  # Continue periodic discovery


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s:%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Import settings
    try:
        import settings
    except Exception as e:
        logging.error("Error loading settings: " + str(e))
        sys.exit(1)
    
    # Collect settings into config dict
    config = {
        'MAX_CHARGE_VOLTAGE': settings.MAX_CHARGE_VOLTAGE if hasattr(settings, 'MAX_CHARGE_VOLTAGE') and settings.MAX_CHARGE_VOLTAGE else None,
        'MAX_CHARGE_CURRENT': settings.MAX_CHARGE_CURRENT if hasattr(settings, 'MAX_CHARGE_CURRENT') and settings.MAX_CHARGE_CURRENT else None,
        'MAX_DISCHARGE_CURRENT': settings.MAX_DISCHARGE_CURRENT if hasattr(settings, 'MAX_DISCHARGE_CURRENT') and settings.MAX_DISCHARGE_CURRENT else None,
        'TEMP_COLD_DANGER': settings.TEMP_COLD_DANGER if hasattr(settings, 'TEMP_COLD_DANGER') else 5.0,
        'TEMP_HOT_DANGER': settings.TEMP_HOT_DANGER if hasattr(settings, 'TEMP_HOT_DANGER') else 35.0,
        'DEVICE_NAME': settings.DEVICE_NAME if hasattr(settings, 'DEVICE_NAME') else '',
        'EXCLUDE_SHUNTS': [s.strip() for s in settings.EXCLUDE_SHUNTS.split(',')] if hasattr(settings, 'EXCLUDE_SHUNTS') and settings.EXCLUDE_SHUNTS else [],
    }
    
    logging.info("Configuration:")
    logging.info(f"  MAX_CHARGE_VOLTAGE: {config['MAX_CHARGE_VOLTAGE']}V" if config['MAX_CHARGE_VOLTAGE'] else "  MAX_CHARGE_VOLTAGE: Not set")
    logging.info(f"  MAX_CHARGE_CURRENT: {config['MAX_CHARGE_CURRENT']}A" if config['MAX_CHARGE_CURRENT'] else "  MAX_CHARGE_CURRENT: Not set")
    logging.info(f"  MAX_DISCHARGE_CURRENT: {config['MAX_DISCHARGE_CURRENT']}A" if config['MAX_DISCHARGE_CURRENT'] else "  MAX_DISCHARGE_CURRENT: Not set")
    logging.info(f"  TEMP_COLD_DANGER: {config['TEMP_COLD_DANGER']}°C")
    logging.info(f"  TEMP_HOT_DANGER: {config['TEMP_HOT_DANGER']}°C")
    if config['EXCLUDE_SHUNTS']:
        logging.info(f"  EXCLUDE_SHUNTS: {', '.join(config['EXCLUDE_SHUNTS'])}")
    
    # Create manager
    manager = SmartShuntToBMSManager(config)
    
    # Start GLib main loop
    logging.info("Starting main loop...")
    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()
