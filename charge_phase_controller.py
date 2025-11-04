"""
Charge Phase Controller
Implements dynamic CVL based on charge phase detection with complete Victron algorithm support

This module detects the current charge phase and calculates the appropriate CVL to publish to DVCC,
mimicking the behavior of Victron charging devices (MPPTs, Orion XS, etc.)

Supported phases:
- Bulk: Fast charge at maximum current to absorption voltage
- Absorption: Hold at absorption voltage with timer and tail current monitoring
- Float: Maintenance charge at float voltage
- Storage: Extended float at reduced voltage (optional, for long-term storage)
- Equalization: High voltage reconditioning (optional, primarily for lead-acid)
- Re-bulk: Return to bulk if voltage drops during float/storage
"""

import time
import logging

class ChargePhaseController:
    """
    Controls charge phases and calculates dynamic CVL
    
    Implements the complete Victron charge cycle:
    - Bulk: Charge at max current until absorption voltage
    - Absorption: Hold at absorption voltage with timer
    - Float: Maintain at float voltage
    - Storage: Reduced voltage for long-term storage (optional)
    - Equalization: High voltage reconditioning (optional, lead-acid)
    - Re-bulk: Return to bulk if voltage drops during float/storage
    """
    
    def __init__(self, mppt_config, shunt_config, max_cvl=14.60):
        """
        Initialize charge phase controller
        
        Args:
            mppt_config: MPPTConfig or OrionXSConfig object with system charge parameters
            shunt_config: SmartShuntConfig object with battery-specific parameters
            max_cvl: Maximum safe voltage (safety ceiling)
        """
        # From MPPT/Orion XS (system-wide charge voltages)
        self.absorption_v = mppt_config.absorption_voltage      # e.g., 14.20V
        self.float_v = mppt_config.float_voltage                # e.g., 13.80V
        self.rebulk_offset = getattr(mppt_config, 'rebulk_offset', 0.80)
        self.absorption_time_limit = getattr(mppt_config, 'absorption_time', 120)  # minutes
        
        # Optional phases (may be None if not configured)
        self.storage_v = getattr(mppt_config, 'storage_voltage', None)  # e.g., 13.50V
        self.equalization_v = getattr(mppt_config, 'equalization_voltage', None)  # e.g., 15.50V (lead-acid)
        self.equalization_duration = getattr(mppt_config, 'equalization_duration', None)  # minutes
        self.auto_equalization = getattr(mppt_config, 'auto_equalization', False)
        
        # From SmartShunt (battery-specific thresholds)
        self.tail_current = shunt_config.tail_current           # e.g., 12A
        
        # Calculated values
        self.rebulk_v = self.float_v - self.rebulk_offset if self.float_v and self.rebulk_offset else None
        self.max_cvl = max_cvl                                  # Safety ceiling
        
        # State tracking
        self.current_phase = "idle"
        self.phase_start_time = None
        self.below_tail_start_time = None
        self.last_full_charge_time = None  # For storage phase transition
        
        # Configurable timing parameters
        self.tail_timer_duration = 180      # 3 minutes (seconds)
        self.rebulk_timer_duration = 30     # 30 seconds to confirm voltage drop
        self.storage_delay = 24 * 3600      # 24 hours in float before storage (seconds)
        self.below_rebulk_start_time = None
        
        # Current averaging for noise reduction
        self.current_history = []
        self.current_avg_window = 30        # 30 seconds
        
        # Log configuration
        logging.info("=== Charge Phase Controller Initialized ===")
        logging.info(f"  Absorption Voltage: {self.absorption_v:.2f}V")
        logging.info(f"  Float Voltage: {self.float_v:.2f}V")
        logging.info(f"  Re-bulk Threshold: {self.rebulk_v:.2f}V" if self.rebulk_v else "  Re-bulk: Not configured")
        if self.storage_v:
            logging.info(f"  Storage Voltage: {self.storage_v:.2f}V (after {self.storage_delay/3600:.0f}h in float)")
        if self.equalization_v:
            logging.info(f"  Equalization Voltage: {self.equalization_v:.2f}V (duration: {self.equalization_duration} min)")
            logging.info(f"  Auto Equalization: {'Enabled' if self.auto_equalization else 'Disabled'}")
        logging.info(f"  Tail Current: {self.tail_current:.1f}A")
        logging.info(f"  Absorption Time Limit: {self.absorption_time_limit} min")
        logging.info(f"  Max CVL (Safety Ceiling): {self.max_cvl:.2f}V")
    
    def update(self, voltage, current, timestamp=None):
        """
        Update charge phase based on current battery state
        
        Args:
            voltage: Battery voltage (V)
            current: Battery current (A, positive = charging)
            timestamp: Current time (seconds since epoch), default = now
        
        Returns:
            str: Current phase ("idle", "bulk", "absorption", "float", "storage", "equalization")
        """
        if timestamp is None:
            timestamp = time.time()
        
        # Update current history for averaging
        self._update_current_history(current, timestamp)
        avg_current = self._get_average_current()
        
        # Initialize phase_start_time if needed
        if self.phase_start_time is None:
            self.phase_start_time = timestamp
        
        # State machine
        old_phase = self.current_phase
        
        if self.current_phase == "idle":
            self._update_idle_phase(voltage, avg_current, timestamp)
        
        elif self.current_phase == "bulk":
            self._update_bulk_phase(voltage, avg_current, timestamp)
        
        elif self.current_phase == "absorption":
            self._update_absorption_phase(voltage, avg_current, timestamp)
        
        elif self.current_phase == "float":
            self._update_float_phase(voltage, avg_current, timestamp)
        
        elif self.current_phase == "storage":
            self._update_storage_phase(voltage, avg_current, timestamp)
        
        elif self.current_phase == "equalization":
            self._update_equalization_phase(voltage, avg_current, timestamp)
        
        # Log phase transitions
        if old_phase != self.current_phase:
            logging.info(f"Charge phase transition: {old_phase.upper()} → {self.current_phase.upper()}")
            logging.info(f"  Voltage: {voltage:.2f}V, Current: {avg_current:.1f}A")
        
        return self.current_phase
    
    def _update_idle_phase(self, voltage, current, timestamp):
        """Update state when in idle phase"""
        if current > 1.0:  # Charging started
            self.current_phase = "bulk"
            self.phase_start_time = timestamp
            self.below_tail_start_time = None
            self.below_rebulk_start_time = None
    
    def _update_bulk_phase(self, voltage, current, timestamp):
        """Update state when in bulk phase"""
        # Transition to absorption when voltage reaches absorption voltage
        if voltage >= (self.absorption_v - 0.1):
            self.current_phase = "absorption"
            self.phase_start_time = timestamp
            self.below_tail_start_time = None
        
        # Return to idle if charging stops
        elif abs(current) < 1.0:
            self.current_phase = "idle"
            self.phase_start_time = timestamp
    
    def _update_absorption_phase(self, voltage, current, timestamp):
        """Update state when in absorption phase"""
        time_in_absorption = timestamp - self.phase_start_time
        
        # Check if current is below tail threshold
        if current <= self.tail_current:
            if self.below_tail_start_time is None:
                self.below_tail_start_time = timestamp
                logging.info(f"Current below tail threshold ({current:.1f}A ≤ {self.tail_current:.1f}A), starting timer...")
            
            # Check if been below tail for required duration
            time_below_tail = timestamp - self.below_tail_start_time
            if time_below_tail >= self.tail_timer_duration:
                self.current_phase = "float"
                self.phase_start_time = timestamp
                self.below_tail_start_time = None
                self.last_full_charge_time = timestamp  # Mark time of full charge
                logging.info(f"Current below tail for {self.tail_timer_duration}s → FLOAT phase")
        else:
            # Current back above tail threshold, reset timer
            if self.below_tail_start_time is not None:
                logging.debug(f"Current back above tail ({current:.1f}A > {self.tail_current:.1f}A), resetting timer")
            self.below_tail_start_time = None
        
        # Safety: Force to float if absorption time limit exceeded
        if time_in_absorption >= (self.absorption_time_limit * 60):
            self.current_phase = "float"
            self.phase_start_time = timestamp
            self.below_tail_start_time = None
            self.last_full_charge_time = timestamp
            logging.warning(f"Absorption time limit ({self.absorption_time_limit} min) exceeded → FLOAT phase")
        
        # Return to idle if charging stops
        if abs(current) < 1.0:
            self.current_phase = "idle"
            self.phase_start_time = timestamp
            self.below_tail_start_time = None
    
    def _update_float_phase(self, voltage, current, timestamp):
        """Update state when in float phase"""
        time_in_float = timestamp - self.phase_start_time
        
        # Check for re-bulk condition (voltage dropped)
        if self.rebulk_v is not None and voltage < self.rebulk_v:
            if self.below_rebulk_start_time is None:
                self.below_rebulk_start_time = timestamp
                logging.info(f"Voltage below re-bulk threshold ({voltage:.2f}V < {self.rebulk_v:.2f}V), starting timer...")
            
            # Confirm voltage has been below re-bulk threshold for duration
            time_below_rebulk = timestamp - self.below_rebulk_start_time
            if time_below_rebulk >= self.rebulk_timer_duration:
                self.current_phase = "bulk"
                self.phase_start_time = timestamp
                self.below_tail_start_time = None
                self.below_rebulk_start_time = None
                logging.info(f"Voltage below re-bulk for {self.rebulk_timer_duration}s → RE-BULK phase")
        else:
            # Voltage back above re-bulk threshold, reset timer
            if self.below_rebulk_start_time is not None:
                logging.debug(f"Voltage back above re-bulk ({voltage:.2f}V ≥ {self.rebulk_v:.2f}V), resetting timer")
            self.below_rebulk_start_time = None
        
        # Transition to storage if configured and been in float long enough
        if self.storage_v and time_in_float >= self.storage_delay:
            self.current_phase = "storage"
            self.phase_start_time = timestamp
            logging.info(f"Float duration exceeded ({self.storage_delay/3600:.0f}h) → STORAGE phase")
        
        # Return to idle if charging stops
        if abs(current) < 1.0:
            self.current_phase = "idle"
            self.phase_start_time = timestamp
            self.below_rebulk_start_time = None
    
    def _update_storage_phase(self, voltage, current, timestamp):
        """Update state when in storage phase (extended float at reduced voltage)"""
        # Check for re-bulk condition (voltage dropped significantly)
        if self.rebulk_v is not None and voltage < self.rebulk_v:
            if self.below_rebulk_start_time is None:
                self.below_rebulk_start_time = timestamp
                logging.info(f"Voltage below re-bulk threshold in storage ({voltage:.2f}V < {self.rebulk_v:.2f}V)")
            
            time_below_rebulk = timestamp - self.below_rebulk_start_time
            if time_below_rebulk >= self.rebulk_timer_duration:
                self.current_phase = "bulk"
                self.phase_start_time = timestamp
                self.below_rebulk_start_time = None
                logging.info(f"Re-bulk triggered from storage → BULK phase")
        else:
            self.below_rebulk_start_time = None
        
        # Return to idle if charging stops
        if abs(current) < 1.0:
            self.current_phase = "idle"
            self.phase_start_time = timestamp
    
    def _update_equalization_phase(self, voltage, current, timestamp):
        """Update state when in equalization phase (high voltage reconditioning for lead-acid)"""
        time_in_equalization = timestamp - self.phase_start_time
        
        # Check if equalization duration exceeded
        if self.equalization_duration and time_in_equalization >= (self.equalization_duration * 60):
            self.current_phase = "float"
            self.phase_start_time = timestamp
            self.last_full_charge_time = timestamp
            logging.info(f"Equalization duration ({self.equalization_duration} min) completed → FLOAT phase")
        
        # Safety: Return to float if charging stops during equalization
        if abs(current) < 1.0:
            self.current_phase = "float"
            self.phase_start_time = timestamp
            logging.warning("Charging stopped during equalization → FLOAT phase")
    
    def _update_current_history(self, current, timestamp):
        """Update current history for averaging"""
        self.current_history.append((timestamp, current))
        # Remove old entries (older than window)
        cutoff_time = timestamp - self.current_avg_window
        self.current_history = [(t, i) for t, i in self.current_history if t >= cutoff_time]
    
    def _get_average_current(self):
        """Get averaged current over window"""
        if not self.current_history:
            return 0.0
        return sum(i for _, i in self.current_history) / len(self.current_history)
    
    def get_cvl(self):
        """
        Get CVL based on current phase
        
        Returns:
            float: CVL to publish to DVCC
        """
        if self.current_phase in ["bulk", "absorption"]:
            cvl = self.absorption_v  # Charge to absorption voltage
        elif self.current_phase == "float":
            cvl = self.float_v       # Maintain at float voltage
        elif self.current_phase == "storage":
            cvl = self.storage_v if self.storage_v else self.float_v  # Storage voltage or fall back to float
        elif self.current_phase == "equalization":
            cvl = self.equalization_v if self.equalization_v else self.absorption_v  # Equalization voltage or fall back
        else:  # idle
            cvl = self.absorption_v  # Ready for bulk if charging starts
        
        # Never exceed safety ceiling
        cvl = min(cvl, self.max_cvl)
        
        return cvl
    
    def get_phase(self):
        """Get current charge phase"""
        return self.current_phase
    
    def get_time_in_phase(self):
        """Get time spent in current phase (seconds)"""
        if self.phase_start_time is None:
            return 0
        return time.time() - self.phase_start_time
    
    def get_status(self):
        """
        Get detailed status information
        
        Returns:
            dict: Status information
        """
        return {
            'phase': self.current_phase,
            'cvl': self.get_cvl(),
            'time_in_phase': self.get_time_in_phase(),
            'absorption_v': self.absorption_v,
            'float_v': self.float_v,
            'storage_v': self.storage_v,
            'equalization_v': self.equalization_v,
            'rebulk_v': self.rebulk_v,
            'tail_current': self.tail_current,
            'max_cvl': self.max_cvl
        }
    
    def __str__(self):
        """String representation"""
        return (f"ChargePhaseController(phase={self.current_phase}, "
                f"cvl={self.get_cvl():.2f}V, "
                f"absorption={self.absorption_v:.2f}V, "
                f"float={self.float_v:.2f}V)")

