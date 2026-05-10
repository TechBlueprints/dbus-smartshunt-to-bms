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

# Import charge discovery and control modules
from charge_source_discovery import ChargeSourceDiscovery
from charge_phase_controller import ChargePhaseController
from smartshunt_config import SmartShuntConfig

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
    
    def __init__(self, config, source_service, device_instance, charge_params):
        """
        Args:
            config: Configuration dictionary
            source_service: D-Bus service name of the source SmartShunt (e.g., 'com.victronenergy.battery.ttyS5')
            device_instance: Device instance ID from the source SmartShunt
            charge_params: Charge parameters from ChargeSourceDiscovery (absorption_v, float_v, etc.)
        """
        self.config = config
        self.source_service = source_service
        self.device_instance = device_instance
        self.charge_params = charge_params
        self._dbusConn = get_bus()
        self._updating = False
        self._charge_controller = None  # Will be initialized after reading SmartShunt config
        
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
        
        # BMS state and error management
        self._dbusservice.add_path("/State", 0, writeable=True)  # 0=Initializing, 9=Running, 14=Standby, 10=Error
        self._dbusservice.add_path("/ErrorCode", 0, writeable=True)  # Error code from BMS
        self._dbusservice.add_path("/ConnectionInformation", "")
        
        # Manufacturer info (derived from SmartShunt)
        self._dbusservice.add_path("/Manufacturer", "Victron Energy")
        self._dbusservice.add_path("/DeviceName", "SmartShunt Virtual BMS")
        
        # State of Health
        self._dbusservice.add_path("/Soh", None, writeable=True)  # State of Health (%)
        
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
        
        # Initialize charge phase controller (after DBus monitor is ready)
        self._init_charge_controller()
    
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
        
        # Starter battery voltage (Dc/1) - only if SmartShunt is configured for it
        self._dbusservice.add_path("/Dc/1/Voltage", None,
            gettextcallback=lambda a, x: "" if x is None or x == [] else "{:.2f}V".format(x))
        
        # State of Charge and capacity
        self._dbusservice.add_path("/Soc", None,
            gettextcallback=lambda a, x: "{:.1f}%".format(x) if x is not None else "")
        self._dbusservice.add_path("/ConsumedAmphours", None,
                                    gettextcallback=lambda a, x: "{:.1f}Ah".format(x) if x is not None else "")
        self._dbusservice.add_path("/TimeToGo", None, writeable=True,
                                    gettextcallback=lambda a, x: "{:.0f}s".format(x) if x is not None and x != [] else "")
        
        # Average current (calculated over time)
        self._dbusservice.add_path("/CurrentAvg", None,
                                    gettextcallback=lambda a, x: "{:.2f}A".format(x) if x is not None else "")
        
        # Balancing status (SmartShunts don't have cells, so always 0)
        self._dbusservice.add_path("/Balancing", 0, writeable=True)
        
        # Alarms - Standard SmartShunt alarms
        self._dbusservice.add_path("/Alarms/Alarm", None)
        self._dbusservice.add_path("/Alarms/LowVoltage", None)
        self._dbusservice.add_path("/Alarms/HighVoltage", None)
        self._dbusservice.add_path("/Alarms/LowSoc", None)
        self._dbusservice.add_path("/Alarms/HighTemperature", None)
        self._dbusservice.add_path("/Alarms/LowTemperature", None)
        self._dbusservice.add_path("/Alarms/LowStarterVoltage", None)
        self._dbusservice.add_path("/Alarms/HighStarterVoltage", None)
        
        # BMS-specific alarms (not directly from SmartShunt, but derived from conditions)
        self._dbusservice.add_path("/Alarms/LowCellVoltage", None, writeable=True)  # Derived from pack voltage
        self._dbusservice.add_path("/Alarms/HighCellVoltage", None, writeable=True)  # Derived from pack voltage
        self._dbusservice.add_path("/Alarms/HighChargeCurrent", None, writeable=True)
        self._dbusservice.add_path("/Alarms/HighDischargeCurrent", None, writeable=True)
        self._dbusservice.add_path("/Alarms/CellImbalance", 0, writeable=True)  # SmartShunts don't have cells
        self._dbusservice.add_path("/Alarms/InternalFailure", None, writeable=True)
        self._dbusservice.add_path("/Alarms/HighChargeTemperature", None, writeable=True)
        self._dbusservice.add_path("/Alarms/LowChargeTemperature", None, writeable=True)
        self._dbusservice.add_path("/Alarms/BmsCable", 0, writeable=True)  # 0=OK, 1=Warning, 2=Alarm
        self._dbusservice.add_path("/Alarms/HighInternalTemperature", 0, writeable=True)
        self._dbusservice.add_path("/Alarms/FuseBlown", 0, writeable=True)
        self._dbusservice.add_path("/Alarms/StateOfHealth", None, writeable=True)
        
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
        
        # Additional BMS history paths
        self._dbusservice.add_path("/History/MinimumCellVoltage", None,
                                    gettextcallback=lambda a, x: "{:.3f}V".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/MaximumCellVoltage", None,
                                    gettextcallback=lambda a, x: "{:.3f}V".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/MinimumTemperature", None,
                                    gettextcallback=lambda a, x: "{:.0f}C".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/MaximumTemperature", None,
                                    gettextcallback=lambda a, x: "{:.0f}C".format(x) if x is not None else "")
        self._dbusservice.add_path("/History/Clear", 0, writeable=True)  # Write 1 to clear
        self._dbusservice.add_path("/History/CanBeCleared", 1, writeable=True)
        
        # Settings
        self._dbusservice.add_path("/Settings/HasStarterVoltage", 0)
        self._dbusservice.add_path("/Settings/HasTemperature", 1)
        self._dbusservice.add_path("/Settings/RelayMode", [],
                                    gettextcallback=lambda a, x: "")
        
        # System/Cell information (SmartShunts monitor whole packs, not individual cells)
        # We treat the entire pack as a single "cell" for compatibility
        # This allows the Victron UI to display cell data even though SmartShunts don't have
        # visibility into individual cells. Pack voltage becomes "Cell 1" voltage.
        self._dbusservice.add_path("/System/NrOfCellsPerBattery", 1, writeable=True)  # Treat pack as 1 cell
        self._dbusservice.add_path("/System/NrOfModulesOnline", 1, writeable=True)
        self._dbusservice.add_path("/System/NrOfModulesOffline", 0, writeable=True)
        self._dbusservice.add_path("/System/NrOfModulesBlockingCharge", None, writeable=True)
        self._dbusservice.add_path("/System/NrOfModulesBlockingDischarge", None, writeable=True)
        
        # Cell voltage extremes (pack voltage = "cell 1" voltage)
        self._dbusservice.add_path("/System/MinCellVoltage", None, writeable=True,
                                    gettextcallback=lambda a, x: "{:.3f}V".format(x) if x is not None else "")
        self._dbusservice.add_path("/System/MaxCellVoltage", None, writeable=True,
                                    gettextcallback=lambda a, x: "{:.3f}V".format(x) if x is not None else "")
        self._dbusservice.add_path("/System/MinVoltageCellId", "1", writeable=True)  # Always cell 1
        self._dbusservice.add_path("/System/MaxVoltageCellId", "1", writeable=True)  # Always cell 1
        
        # Temperature extremes (SmartShunts have single temp sensor = "cell 1" temp)
        self._dbusservice.add_path("/System/MinCellTemperature", None, writeable=True)
        self._dbusservice.add_path("/System/MaxCellTemperature", None, writeable=True)
        self._dbusservice.add_path("/System/MinTemperatureCellId", "1", writeable=True)  # Always cell 1
        self._dbusservice.add_path("/System/MaxTemperatureCellId", "1", writeable=True)  # Always cell 1
        
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
        min_v = self.config.get('MIN_BATTERY_VOLTAGE')
        ccl = self.config.get('MAX_CHARGE_CURRENT')
        dcl = self.config.get('MAX_DISCHARGE_CURRENT')
        
        # Battery voltage thresholds
        if min_v:
            self._dbusservice.add_path("/Info/BatteryLowVoltage", min_v, writeable=True,
                gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None else "")
        else:
            self._dbusservice.add_path("/Info/BatteryLowVoltage", None, writeable=True,
                gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None else "")
        
        self._dbusservice.add_path("/Info/MaxChargeCellVoltage", None, writeable=True,
            gettextcallback=lambda a, x: "{:.3f}V".format(x) if x is not None else "")
        
        if cvl:
            self._dbusservice.add_path("/Info/MaxChargeVoltage", cvl, writeable=True,
                gettextcallback=lambda a, x: "{:.2f}V".format(x) if x is not None else "")
            # MaxChargeCellVoltage is the same as MaxChargeVoltage since pack = 1 cell
            self._dbusservice["/Info/MaxChargeCellVoltage"] = cvl
        if ccl:
            self._dbusservice.add_path("/Info/MaxChargeCurrent", ccl, writeable=True,
                gettextcallback=lambda a, x: "{:.1f}A".format(x) if x is not None else "")
        if dcl:
            self._dbusservice.add_path("/Info/MaxDischargeCurrent", dcl, writeable=True,
                gettextcallback=lambda a, x: "{:.1f}A".format(x) if x is not None else "")
        
        # Charge mode and limitation info (for dynamic CVL debugging)
        self._dbusservice.add_path("/Info/ChargeMode", None, writeable=True)
        self._dbusservice.add_path("/Info/ChargeLimitation", None, writeable=True)
        self._dbusservice.add_path("/Info/DischargeLimitation", None, writeable=True)
        
        # Capacity (BMS-specific, not present on physical SmartShunts)
        # We'll try to read this from the SmartShunt's configuration
        self._dbusservice.add_path("/Capacity", None, writeable=True,
            gettextcallback=lambda a, x: "{:.0f}Ah".format(x) if x is not None else "")
        self._dbusservice.add_path("/InstalledCapacity", None, writeable=True,
            gettextcallback=lambda a, x: "{:.0f}Ah".format(x) if x is not None else "")
        
        # Charge/Discharge control flags
        self._dbusservice.add_path("/Io/AllowToCharge", 1, writeable=True)
        self._dbusservice.add_path("/Io/AllowToDischarge", 1, writeable=True)
        self._dbusservice.add_path("/Io/AllowToBalance", 0, writeable=True)  # SmartShunts don't balance
    
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
    
    def _init_charge_controller(self):
        """
        Initialize charge phase controller with MPPT + SmartShunt configuration
        """
        try:
            # If no charge parameters discovered (no MPPTs), use static config
            if not self.charge_params or not self.charge_params.get('absorption_voltage'):
                logging.warning(f"{self.source_service}: No MPPT charge parameters found, using static CVL from config")
                self._charge_controller = None
            return
        
            # Read SmartShunt configuration
            bus = dbus.SystemBus()
            shunt_config = SmartShuntConfig(self.source_service)
            if not shunt_config.read_all(bus):
                logging.warning(f"{self.source_service}: Could not read SmartShunt config, using system-wide tail current")
                # Create a minimal config with system-wide tail current
                class MinimalConfig:
                    def __init__(self, tail_current):
                        self.tail_current = tail_current
                shunt_config = MinimalConfig(self.charge_params.get('tail_current', 25.0))
            
            # Create a minimal MPPT config object from charge_params
            class MPPTConfigData:
                def __init__(self, params):
                    self.absorption_voltage = params.get('absorption_voltage')
                    self.float_voltage = params.get('float_voltage')
                    self.rebulk_offset = 0.80  # Default if not provided
                    self.absorption_time = 120  # Default 2 hours
            
            mppt_config = MPPTConfigData(self.charge_params)
            
            # Get max CVL from config or use safe default
            max_cvl = self.config.get('MAX_CHARGE_VOLTAGE', 14.60)
                        
            # Create charge phase controller
            self._charge_controller = ChargePhaseController(mppt_config, shunt_config, max_cvl)
            
            logging.info(f"✓ Charge phase controller initialized for {self.source_service}")
            logging.info(f"  Dynamic CVL enabled: {mppt_config.absorption_voltage:.2f}V (absorption) / {mppt_config.float_voltage:.2f}V (float)")
            logging.info(f"  Tail current: {shunt_config.tail_current:.1f}A")
        
        except Exception as e:
            logging.error(f"Failed to initialize charge controller for {self.source_service}: {e}")
            import traceback
            logging.error(traceback.format_exc())
            self._charge_controller = None
    
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
            bus["/Dc/1/Voltage"] = get_value("/Dc/1/Voltage")  # Starter battery (if configured)
            
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
            bus["/Settings/HasStarterVoltage"] = get_value("/Settings/HasStarterVoltage", 0)
            bus["/Settings/HasTemperature"] = get_value("/Settings/HasTemperature", 1)
            
            # Pass through VE.Direct errors
            bus["/VEDirect/HexChecksumErrors"] = get_value("/VEDirect/HexChecksumErrors", 0)
            bus["/VEDirect/HexInvalidCharacterErrors"] = get_value("/VEDirect/HexInvalidCharacterErrors", 0)
            bus["/VEDirect/HexUnfinishedErrors"] = get_value("/VEDirect/HexUnfinishedErrors", 0)
            bus["/VEDirect/TextChecksumErrors"] = get_value("/VEDirect/TextChecksumErrors", 0)
            bus["/VEDirect/TextParseError"] = get_value("/VEDirect/TextParseError", 0)
            bus["/VEDirect/TextUnfinishedErrors"] = get_value("/VEDirect/TextUnfinishedErrors", 0)
            
            # Update BMS state management
            # State: 0=Initializing, 9=Running, 14=Standby, 10=Error
            current_state = bus["/State"]
            if current_state == 0:  # Initializing
                bus["/State"] = 9  # Running
            
            # Update BMS-specific paths
            voltage = get_value("/Dc/0/Voltage")
            current = get_value("/Dc/0/Current")
            temperature = get_value("/Dc/0/Temperature")
            
            # Average current calculation (simple moving average over last few readings)
            if current is not None:
                if not hasattr(self, '_current_history'):
                    self._current_history = []
                self._current_history.append(current)
                if len(self._current_history) > 30:  # Keep last 30 readings
                    self._current_history.pop(0)
                bus["/CurrentAvg"] = sum(self._current_history) / len(self._current_history)
            
            # System/Module status
            bus["/System/NrOfCellsPerBattery"] = 1  # Treat pack as single cell
            bus["/System/NrOfModulesOnline"] = 1
            bus["/System/NrOfModulesOffline"] = 0
            bus["/System/NrOfModulesBlockingCharge"] = 0
            bus["/System/NrOfModulesBlockingDischarge"] = 0
            
            # Cell voltage (pack voltage = "cell 1" voltage for single-cell representation)
            if voltage is not None:
                bus["/System/MinCellVoltage"] = voltage
                bus["/System/MaxCellVoltage"] = voltage
                bus["/System/MinVoltageCellId"] = "1"
                bus["/System/MaxVoltageCellId"] = "1"
            
            # Temperature extremes (SmartShunts have single sensor = "cell 1" temp)
            if temperature is not None:
                bus["/System/MinCellTemperature"] = temperature
                bus["/System/MaxCellTemperature"] = temperature
                bus["/System/MinTemperatureCellId"] = "1"
                bus["/System/MaxTemperatureCellId"] = "1"
            
            # Update history cell voltage extremes (pack voltage history)
            if voltage is not None:
                if not hasattr(self, '_hist_min_cell_v') or voltage < self._hist_min_cell_v:
                    self._hist_min_cell_v = voltage
                if not hasattr(self, '_hist_max_cell_v') or voltage > self._hist_max_cell_v:
                    self._hist_max_cell_v = voltage
                bus["/History/MinimumCellVoltage"] = self._hist_min_cell_v
                bus["/History/MaximumCellVoltage"] = self._hist_max_cell_v
            
            # BMS cable alarm (based on connection status)
            bus["/Alarms/BmsCable"] = 0  # 0=OK (SmartShunt is connected)
            
            # Additional BMS alarms (derived from conditions)
            # High/Low cell voltage alarms (derived from pack voltage and thresholds)
            if voltage is not None:
                low_v_threshold = self.config.get('TEMP_COLD_DANGER', 10.8)  # Use config or default
                high_v_threshold = self.config.get('MAX_CHARGE_VOLTAGE', 14.6)
                
                bus["/Alarms/LowCellVoltage"] = 2 if voltage < low_v_threshold else 0
                bus["/Alarms/HighCellVoltage"] = 2 if voltage > high_v_threshold else 0
            
            # Current alarms
                if current is not None:
                ccl = self.config.get('MAX_CHARGE_CURRENT', 100)
                dcl = self.config.get('MAX_DISCHARGE_CURRENT', 200)
                
                bus["/Alarms/HighChargeCurrent"] = 2 if current > ccl else 0
                bus["/Alarms/HighDischargeCurrent"] = 2 if current < -dcl else 0
            
            # Temperature alarms
            if temperature is not None:
                temp_hot = self.config.get('TEMP_HOT_DANGER', 45.0)
                temp_cold = self.config.get('TEMP_COLD_DANGER', 0.0)
                
                bus["/Alarms/HighChargeTemperature"] = 2 if temperature >= temp_hot else 0
                bus["/Alarms/LowChargeTemperature"] = 2 if temperature <= temp_cold else 0
                bus["/Alarms/HighTemperature"] = 2 if temperature >= temp_hot else 0
        
            # Update history extremes (track over time)
            if temperature is not None:
                if not hasattr(self, '_hist_min_temp') or temperature < self._hist_min_temp:
                    self._hist_min_temp = temperature
                if not hasattr(self, '_hist_max_temp') or temperature > self._hist_max_temp:
                    self._hist_max_temp = temperature
                bus["/History/MinimumTemperature"] = self._hist_min_temp
                bus["/History/MaximumTemperature"] = self._hist_max_temp
            
            # Update dynamic CVL based on charge phase (if charge controller is available)
            if self._charge_controller:
                voltage = get_value("/Dc/0/Voltage")
                current = get_value("/Dc/0/Current")
                
                if voltage is not None and current is not None:
                    # Update charge phase based on current battery state
                    phase = self._charge_controller.update(voltage, current)
                    cvl = self._charge_controller.get_cvl()
                    
                    # Update charge mode info for debugging
                    phase_names = {
                        "idle": "Idle",
                        "bulk": "Bulk",
                        "absorption": "Absorption",
                        "float": "Float",
                        "storage": "Storage",
                        "equalization": "Equalization"
                    }
                    bus["/Info/ChargeMode"] = phase_names.get(phase, "Unknown")
                    
                    # Update CVL on D-Bus
                    if bus["/Info/MaxChargeVoltage"] != cvl:
                        bus["/Info/MaxChargeVoltage"] = cvl
                        logging.info(f"{service}: CVL updated to {cvl:.2f}V (phase: {phase})")
            
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
            
            # Check MIN_BATTERY_VOLTAGE threshold if configured
            min_v = self.config.get('MIN_BATTERY_VOLTAGE')
            if min_v is not None and voltage is not None:
                if voltage <= min_v:
                    allow_discharge = 0
                    logging.debug(f"{service}: Discharging disabled (voltage {voltage:.2f}V <= MIN_BATTERY_VOLTAGE {min_v:.2f}V)")
            
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
        self._charge_params = None  # Will be populated by charge discovery
        
        logging.info("### SmartShunt to BMS Converter ###")
        logging.info(f"Version: {VERSION}")
            
        # Discover charge sources (MPPTs, chargers, etc.) - do this once at startup
        self._discover_charge_sources()
        
        # Initial SmartShunt discovery
        GLib.timeout_add(1000, self._discover_smartshunts)
        
        # Periodic re-discovery (every 30 seconds)
        GLib.timeout_add(30000, self._discover_smartshunts)
    
    def _discover_charge_sources(self):
        """Discover MPPTs and other charging devices to get charge algorithm parameters"""
        try:
            logging.info("\n=== Discovering Charge Sources ===")
            discovery = ChargeSourceDiscovery()
            discovery.discover_all(dbus.SystemBus())
            
            self._charge_params = discovery.get_charge_algorithm_params()
            
            if self._charge_params['source_count'] > 0:
                logging.info("✓ Charge source discovery complete")
                logging.info(f"  Found {self._charge_params['source_count']} charge source(s)")
                if self._charge_params['absorption_voltage']:
                    logging.info(f"  Absorption: {self._charge_params['absorption_voltage']:.2f}V")
                if self._charge_params['float_voltage']:
                    logging.info(f"  Float: {self._charge_params['float_voltage']:.2f}V")
                if self._charge_params['tail_current']:
                    logging.info(f"  Tail Current: {self._charge_params['tail_current']:.1f}A (system-wide)")
                logging.info("  Dynamic CVL will be enabled for virtual BMS")
            else:
                logging.warning("⚠️  No charge sources found (no MPPTs/chargers)")
                logging.warning("  Virtual BMS will use static CVL from configuration")
                self._charge_params = None
                
        except Exception as e:
            logging.error(f"Error discovering charge sources: {e}")
            import traceback
            logging.error(traceback.format_exc())
            self._charge_params = None
    
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
                        bms = SmartShuntBMS(self.config, service_name, device_instance, self._charge_params)
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
