"""
Charge Source Discovery
Unified module to discover and read charge algorithm settings from all Victron charging devices

Supports:
- MPPT Solar Chargers (via VregLink)
- Orion XS DC-DC Chargers (via VregLink)
- SmartChargers / AC Chargers (via VregLink)
- Multiplus Inverter/Chargers (limited D-Bus access only)

Purpose: Auto-detect charge voltages (absorption, float) from existing charging devices
so the virtual BMS can replicate their charge algorithm behavior.
"""

import dbus
import logging
from mppt_config_reader import MPPTConfig
from orion_xs_config_reader import OrionXSConfig
from smartcharger_config_reader import SmartChargerConfig
from multiplus_config_reader import MultiplusConfig

class ChargeSourceDiscovery:
    """Discovers and reads charge configuration from all available charging devices"""
    
    def __init__(self):
        self.mppts = []
        self.orion_xs_chargers = []
        self.smart_chargers = []
        self.multiplus_devices = []
        
        self.consensus_absorption_voltage = None
        self.consensus_float_voltage = None
        self.consensus_tail_current = None
    
    def discover_all(self, bus):
        """
        Discover all charging devices and read their configurations
        
        Args:
            bus: D-Bus system bus
            
        Returns:
            bool: True if at least one charging device was found and read successfully
        """
        logging.info("=== Discovering Charge Sources ===")
        
        try:
            # Get list of all D-Bus services
            proxy = bus.get_object('org.freedesktop.DBus', '/org/freedesktop/DBus')
            dbus_interface = dbus.Interface(proxy, 'org.freedesktop.DBus')
            services = dbus_interface.ListNames()
            
            # Discover MPPTs (solarcharger services)
            logging.info("\nScanning for MPPT Solar Chargers...")
            mppt_services = [s for s in services if 'solarcharger' in s and s.startswith('com.victronenergy')]
            for service in mppt_services:
                logging.info(f"  Found: {service}")
                config = MPPTConfig(service)
                if config.read_all(bus):
                    self.mppts.append(config)
                    config.log_all_settings()
                else:
                    logging.warning(f"  Could not read configuration from {service}")
            
            # Discover Orion XS and other DC-DC chargers
            # Note: Orion XS may appear as 'charger' or 'alternator' service
            logging.info("\nScanning for Orion XS / DC-DC Chargers...")
            charger_services = [s for s in services if s.startswith('com.victronenergy.charger') or s.startswith('com.victronenergy.alternator')]
            for service in charger_services:
                # Try to determine if it's an Orion XS by checking product name
                try:
                    obj = bus.get_object(service, '/ProductName')
                    iface = dbus.Interface(obj, 'com.victronenergy.BusItem')
                    product_name = str(iface.GetValue())
                    
                    if 'orion' in product_name.lower():
                        logging.info(f"  Found Orion XS: {service}")
                        config = OrionXSConfig(service)
                        if config.read_all(bus):
                            self.orion_xs_chargers.append(config)
                            config.log_all_settings()
                        else:
                            logging.warning(f"  Could not read configuration from {service}")
                    else:
                        # Assume it's a SmartCharger / AC charger
                        logging.info(f"  Found SmartCharger: {service} ({product_name})")
                        config = SmartChargerConfig(service)
                        if config.read_all(bus):
                            self.smart_chargers.append(config)
                            config.log_all_settings()
                        else:
                            logging.warning(f"  Could not read configuration from {service}")
                except:
                    # If we can't read product name, try SmartCharger config
                    logging.info(f"  Found: {service}")
                    config = SmartChargerConfig(service)
                    if config.read_all(bus):
                        self.smart_chargers.append(config)
                        config.log_all_settings()
            
            # Discover Multiplus devices
            logging.info("\nScanning for Multiplus / VEBus devices...")
            vebus_services = [s for s in services if s.startswith('com.victronenergy.vebus')]
            for service in vebus_services:
                logging.info(f"  Found: {service}")
                config = MultiplusConfig(service)
                if config.read_all(bus):
                    self.multiplus_devices.append(config)
                    config.log_all_settings()
                else:
                    logging.warning(f"  Could not read configuration from {service}")
            
            # Calculate consensus charge voltages
            self._calculate_consensus()
            
            total_devices = len(self.mppts) + len(self.orion_xs_chargers) + len(self.smart_chargers) + len(self.multiplus_devices)
            logging.info(f"\n=== Discovery Complete: {total_devices} charging device(s) found ===")
            
            return total_devices > 0
        
        except Exception as e:
            logging.error(f"Error during charge source discovery: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False
    
    def _calculate_consensus(self):
        """
        Calculate consensus charge voltages from all discovered devices
        Uses the most conservative (lowest) values if devices differ
        """
        logging.info("\n=== Calculating Consensus Charge Parameters ===")
        
        # Collect all absorption voltages
        absorption_voltages = []
        absorption_voltages.extend([m.absorption_voltage for m in self.mppts if m.absorption_voltage is not None])
        absorption_voltages.extend([o.absorption_voltage for o in self.orion_xs_chargers if o.absorption_voltage is not None])
        absorption_voltages.extend([s.absorption_voltage for s in self.smart_chargers if s.absorption_voltage is not None])
        
        # Collect all float voltages
        float_voltages = []
        float_voltages.extend([m.float_voltage for m in self.mppts if m.float_voltage is not None])
        float_voltages.extend([o.float_voltage for o in self.orion_xs_chargers if o.float_voltage is not None])
        float_voltages.extend([s.float_voltage for s in self.smart_chargers if s.float_voltage is not None])
        
        # Collect tail currents (from MPPTs)
        tail_currents = []
        tail_currents.extend([m.tail_current for m in self.mppts if m.tail_current is not None])
        
        # Use minimum (most conservative) if values differ
        if absorption_voltages:
            self.consensus_absorption_voltage = min(absorption_voltages)
            if max(absorption_voltages) - min(absorption_voltages) > 0.1:
                logging.warning(f"  ⚠️  Absorption voltages differ: {[f'{v:.2f}V' for v in absorption_voltages]}")
                logging.warning(f"      Using most conservative (minimum): {self.consensus_absorption_voltage:.2f}V")
            else:
                logging.info(f"  ✅ Consensus Absorption Voltage: {self.consensus_absorption_voltage:.2f}V")
        else:
            logging.warning("  ⚠️  No absorption voltage found from any device")
        
        if float_voltages:
            self.consensus_float_voltage = min(float_voltages)
            if max(float_voltages) - min(float_voltages) > 0.1:
                logging.warning(f"  ⚠️  Float voltages differ: {[f'{v:.2f}V' for v in float_voltages]}")
                logging.warning(f"      Using most conservative (minimum): {self.consensus_float_voltage:.2f}V")
            else:
                logging.info(f"  ✅ Consensus Float Voltage: {self.consensus_float_voltage:.2f}V")
        else:
            logging.warning("  ⚠️  No float voltage found from any device")
        
        if tail_currents:
            self.consensus_tail_current = min(tail_currents)
            if max(tail_currents) - min(tail_currents) > 1.0:
                logging.warning(f"  ⚠️  Tail currents differ: {[f'{i:.1f}A' for i in tail_currents]}")
                logging.warning(f"      Using most conservative (minimum): {self.consensus_tail_current:.1f}A")
            else:
                logging.info(f"  ✅ Consensus Tail Current: {self.consensus_tail_current:.1f}A")
        else:
            logging.info("  ℹ️  No tail current info (will use default or config)")
        
        logging.info("=== End Consensus Calculation ===\n")
    
    def get_charge_algorithm_params(self):
        """
        Get the consensus charge algorithm parameters
        
        Returns:
            dict: {
                'absorption_voltage': float or None,
                'float_voltage': float or None,
                'tail_current': float or None,
                'source_count': int
            }
        """
        total_sources = len(self.mppts) + len(self.orion_xs_chargers) + len(self.smart_chargers)
        
        return {
            'absorption_voltage': self.consensus_absorption_voltage,
            'float_voltage': self.consensus_float_voltage,
            'tail_current': self.consensus_tail_current,
            'source_count': total_sources,
            'mppt_count': len(self.mppts),
            'orion_xs_count': len(self.orion_xs_chargers),
            'smart_charger_count': len(self.smart_chargers),
            'multiplus_count': len(self.multiplus_devices)
        }
    
    def __str__(self):
        """String representation of discovered devices"""
        lines = ["Charge Source Discovery:"]
        lines.append(f"  MPPTs: {len(self.mppts)}")
        lines.append(f"  Orion XS: {len(self.orion_xs_chargers)}")
        lines.append(f"  SmartChargers: {len(self.smart_chargers)}")
        lines.append(f"  Multiplus: {len(self.multiplus_devices)}")
        if self.consensus_absorption_voltage:
            lines.append(f"  Absorption: {self.consensus_absorption_voltage:.2f}V")
        if self.consensus_float_voltage:
            lines.append(f"  Float: {self.consensus_float_voltage:.2f}V")
        return "\n".join(lines)


def discover_charge_sources():
    """
    Convenience function to discover all charge sources
    
    Returns:
        ChargeSourceDiscovery: Discovery object with all found devices and consensus params
    """
    bus = dbus.SystemBus()
    discovery = ChargeSourceDiscovery()
    discovery.discover_all(bus)
    return discovery


if __name__ == "__main__":
    # Test/demo code
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    
    print("=" * 70)
    print("Victron Charge Source Discovery")
    print("=" * 70)
    
    discovery = discover_charge_sources()
    
    print("\n" + "=" * 70)
    print("Summary:")
    print("=" * 70)
    print(discovery)
    
    params = discovery.get_charge_algorithm_params()
    print("\nCharge Algorithm Parameters:")
    print(f"  Absorption Voltage: {params['absorption_voltage']:.2f}V" if params['absorption_voltage'] else "  Absorption Voltage: Not found")
    print(f"  Float Voltage: {params['float_voltage']:.2f}V" if params['float_voltage'] else "  Float Voltage: Not found")
    print(f"  Tail Current: {params['tail_current']:.1f}A" if params['tail_current'] else "  Tail Current: Not configured")
    print(f"\nTotal charge sources: {params['source_count']}")

