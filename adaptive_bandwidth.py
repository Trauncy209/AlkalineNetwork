#!/usr/bin/env python3
"""
Alkaline Network - Adaptive Bandwidth Controller
=================================================

Automatically adjusts Wi-Fi HaLow bandwidth (1/2/4/8 MHz) based on 
signal strength to optimize the speed vs range tradeoff.

How it works:
  - Monitors RSSI (signal strength) of connected peers
  - If signal is strong and stable → increase bandwidth for more speed
  - If signal is weak or unstable → decrease bandwidth for reliability
  - Uses hysteresis to prevent rapid switching

Bandwidth vs Performance:
  | Bandwidth | Speed         | Range    | Best For                    |
  |-----------|---------------|----------|-----------------------------|
  | 8 MHz     | 15-32 Mbps    | <300m    | Close customers, max speed  |
  | 4 MHz     | 8-15 Mbps     | 300-600m | Default, balanced           |
  | 2 MHz     | 2-6 Mbps      | 600-900m | Extended range              |
  | 1 MHz     | 150Kbps-1Mbps | 900m-1km+| Maximum range, minimal speed|

Usage:
  python adaptive_bandwidth.py --interface halow0 --monitor
  python adaptive_bandwidth.py --interface halow0 --set 4

Requirements:
  - Root access (for iw commands)
  - Wi-Fi HaLow interface (halow0 or similar)

Author: AlkalineTech
License: MIT
"""

import os
import sys
import time
import json
import logging
import argparse
import subprocess
import threading
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Callable
from datetime import datetime, timedelta
from collections import deque

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("alkaline.bandwidth")

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_DIR = Path("/etc/alkaline") if os.name != 'nt' else SCRIPT_DIR
STATE_FILE = SCRIPT_DIR / "bandwidth_state.json"
LOG_FILE = SCRIPT_DIR / "bandwidth.log"

# Bandwidth options (MHz)
BANDWIDTHS = [1, 2, 4, 8]
DEFAULT_BANDWIDTH = 4

# RSSI thresholds (dBm) - higher (less negative) = better signal
# These define when to switch between bandwidths
RSSI_THRESHOLDS = {
    8: -55,   # Need excellent signal for 8 MHz
    4: -65,   # Good signal for 4 MHz
    2: -75,   # Moderate signal for 2 MHz
    1: -85,   # Weak signal, use 1 MHz for max range
}

# Hysteresis settings - prevent rapid switching
UPGRADE_DELAY_SECONDS = 300      # 5 minutes of good signal before upgrading
DOWNGRADE_DELAY_SECONDS = 60     # 1 minute of bad signal before downgrading
RSSI_HYSTERESIS_DB = 5           # Need 5dB better than threshold to upgrade
SAMPLE_INTERVAL_SECONDS = 5      # Check signal every 5 seconds
HISTORY_SIZE = 60                # Keep 60 samples (5 min at 5s intervals)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class BandwidthState:
    """Persistent state for bandwidth controller."""
    current_bandwidth: int = DEFAULT_BANDWIDTH
    last_change: float = 0.0
    last_change_reason: str = ""
    change_count: int = 0
    
    # Stats
    total_upgrades: int = 0
    total_downgrades: int = 0
    time_at_bandwidth: Dict[int, float] = field(default_factory=lambda: {1: 0, 2: 0, 4: 0, 8: 0})
    
    def save(self):
        """Save state to file."""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(asdict(self), f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save state: {e}")
    
    @classmethod
    def load(cls) -> 'BandwidthState':
        """Load state from file."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                return cls(**data)
            except Exception as e:
                logger.warning(f"Could not load state: {e}")
        return cls()


@dataclass
class PeerInfo:
    """Information about a connected peer."""
    mac_address: str
    rssi: int  # dBm
    tx_rate: float  # Mbps
    rx_rate: float  # Mbps
    last_seen: float


# =============================================================================
# SIGNAL MONITORING
# =============================================================================

class SignalMonitor:
    """
    Monitors signal strength of Wi-Fi HaLow connections.
    
    Uses 'iw' commands to get station info from the wireless interface.
    On Windows, simulates data for testing.
    """
    
    def __init__(self, interface: str = "halow0"):
        self.interface = interface
        self.rssi_history: deque = deque(maxlen=HISTORY_SIZE)
        self.peers: Dict[str, PeerInfo] = {}
        self._simulated_rssi = -60  # For Windows testing
    
    def get_interface_info(self) -> Optional[dict]:
        """Get information about the wireless interface."""
        if os.name == 'nt':
            # Windows simulation
            return {'bandwidth': BandwidthState.load().current_bandwidth, 'tx_power': 28}
        
        try:
            result = subprocess.run(
                ["iw", "dev", self.interface, "info"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None
            
            info = {}
            for line in result.stdout.split('\n'):
                line = line.strip()
                if 'channel' in line.lower():
                    # Parse: channel 1 (902.5 MHz), width: 4 MHz
                    parts = line.split(',')
                    for part in parts:
                        if 'width' in part.lower():
                            import re
                            match = re.search(r'(\d+)\s*MHz', part)
                            if match:
                                info['bandwidth'] = int(match.group(1))
                if 'txpower' in line.lower():
                    import re
                    match = re.search(r'([\d.]+)\s*dBm', line)
                    if match:
                        info['tx_power'] = float(match.group(1))
            
            return info
            
        except Exception as e:
            logger.error(f"Failed to get interface info: {e}")
            return None
    
    def get_station_info(self) -> List[PeerInfo]:
        """Get information about connected stations."""
        if os.name == 'nt':
            # Windows simulation - return fake peer data
            return [PeerInfo(
                mac_address="AA:BB:CC:DD:EE:FF",
                rssi=self._simulated_rssi,
                tx_rate=15.0,
                rx_rate=12.0,
                last_seen=time.time()
            )]
        
        peers = []
        
        try:
            result = subprocess.run(
                ["iw", "dev", self.interface, "station", "dump"],
                capture_output=True, text=True, timeout=5
            )
            
            if result.returncode != 0:
                return peers
            
            current_mac = None
            current_rssi = -100
            current_tx = 0.0
            current_rx = 0.0
            
            for line in result.stdout.split('\n'):
                line = line.strip()
                
                if line.startswith('Station'):
                    if current_mac:
                        peers.append(PeerInfo(
                            mac_address=current_mac,
                            rssi=current_rssi,
                            tx_rate=current_tx,
                            rx_rate=current_rx,
                            last_seen=time.time()
                        ))
                    
                    parts = line.split()
                    if len(parts) >= 2:
                        current_mac = parts[1]
                        current_rssi = -100
                        current_tx = 0.0
                        current_rx = 0.0
                
                elif 'signal:' in line.lower():
                    import re
                    match = re.search(r'signal:\s*([-\d]+)', line)
                    if match:
                        current_rssi = int(match.group(1))
                
                elif 'tx bitrate:' in line.lower():
                    import re
                    match = re.search(r'tx bitrate:\s*([\d.]+)', line)
                    if match:
                        current_tx = float(match.group(1))
                
                elif 'rx bitrate:' in line.lower():
                    import re
                    match = re.search(r'rx bitrate:\s*([\d.]+)', line)
                    if match:
                        current_rx = float(match.group(1))
            
            if current_mac:
                peers.append(PeerInfo(
                    mac_address=current_mac,
                    rssi=current_rssi,
                    tx_rate=current_tx,
                    rx_rate=current_rx,
                    last_seen=time.time()
                ))
            
            for peer in peers:
                self.peers[peer.mac_address] = peer
            
            return peers
            
        except Exception as e:
            logger.error(f"Failed to get station info: {e}")
            return peers
    
    def set_simulated_rssi(self, rssi: int):
        """Set simulated RSSI for Windows testing."""
        self._simulated_rssi = rssi
    
    def get_average_rssi(self) -> Optional[int]:
        """Get average RSSI across all connected peers."""
        peers = self.get_station_info()
        if not peers:
            return None
        
        avg_rssi = sum(p.rssi for p in peers) / len(peers)
        return int(avg_rssi)
    
    def get_worst_rssi(self) -> Optional[int]:
        """Get worst (lowest) RSSI among connected peers."""
        peers = self.get_station_info()
        if not peers:
            return None
        
        return min(p.rssi for p in peers)
    
    def sample_rssi(self) -> Optional[int]:
        """Take an RSSI sample and add to history."""
        rssi = self.get_worst_rssi()
        if rssi is not None:
            self.rssi_history.append((time.time(), rssi))
        return rssi
    
    def get_rssi_trend(self, window_seconds: float = 60) -> Optional[float]:
        """
        Calculate RSSI trend over the given window.
        
        Returns:
          - Positive value: signal improving
          - Negative value: signal degrading
          - None: not enough data
        """
        if len(self.rssi_history) < 5:
            return None
        
        now = time.time()
        cutoff = now - window_seconds
        
        recent = [(t, r) for t, r in self.rssi_history if t > cutoff]
        if len(recent) < 3:
            return None
        
        n = len(recent)
        sum_t = sum(t for t, _ in recent)
        sum_r = sum(r for _, r in recent)
        sum_tr = sum(t * r for t, r in recent)
        sum_t2 = sum(t * t for t, _ in recent)
        
        denominator = n * sum_t2 - sum_t * sum_t
        if denominator == 0:
            return 0.0
        
        slope = (n * sum_tr - sum_t * sum_r) / denominator
        return slope


# =============================================================================
# BANDWIDTH CONTROLLER
# =============================================================================

class AdaptiveBandwidthController:
    """
    Automatically adjusts bandwidth based on signal conditions.
    
    Rules:
      - Only upgrade after sustained good signal (5 minutes)
      - Downgrade faster if signal degrades (1 minute)
      - Use hysteresis to prevent oscillation
      - Log all changes for debugging
    """
    
    def __init__(self, interface: str = "halow0"):
        self.interface = interface
        self.monitor = SignalMonitor(interface)
        self.state = BandwidthState.load()
        self._running = False
        self._callbacks: List[Callable] = []
        
        # Tracking for hysteresis
        self._upgrade_candidate: Optional[int] = None
        self._upgrade_since: float = 0.0
        self._downgrade_candidate: Optional[int] = None
        self._downgrade_since: float = 0.0
    
    def add_callback(self, callback: Callable):
        """Add callback to be called on bandwidth changes."""
        self._callbacks.append(callback)
    
    def get_current_bandwidth(self) -> int:
        """Get current bandwidth from interface or state."""
        info = self.monitor.get_interface_info()
        if info and 'bandwidth' in info:
            return info['bandwidth']
        return self.state.current_bandwidth
    
    def set_bandwidth(self, bandwidth: int, reason: str = "") -> bool:
        """
        Set the bandwidth on the Wi-Fi HaLow interface.
        """
        if bandwidth not in BANDWIDTHS:
            logger.error(f"Invalid bandwidth: {bandwidth}")
            return False
        
        old_bw = self.state.current_bandwidth
        
        if bandwidth == old_bw:
            return True
        
        logger.info(f"Changing bandwidth: {old_bw} MHz → {bandwidth} MHz (reason: {reason})")
        
        try:
            if os.name != 'nt':
                # Linux: Try OpenWrt UCI approach first
                result = subprocess.run(
                    ["uci", "set", f"wireless.halow.htmode=HT{bandwidth}"],
                    capture_output=True, text=True
                )
                
                if result.returncode == 0:
                    subprocess.run(["uci", "commit", "wireless"], capture_output=True)
                    subprocess.run(["wifi", "reload"], capture_output=True)
                    logger.info(f"Bandwidth changed via UCI")
                else:
                    # Fallback: try iw directly
                    subprocess.run(
                        ["iw", "dev", self.interface, "set", "channel", "1", f"{bandwidth}MHz"],
                        capture_output=True, text=True
                    )
            
            # Update state
            now = time.time()
            if bandwidth > old_bw:
                self.state.total_upgrades += 1
            else:
                self.state.total_downgrades += 1
            
            self.state.current_bandwidth = bandwidth
            self.state.last_change = now
            self.state.last_change_reason = reason
            self.state.change_count += 1
            self.state.save()
            
            # Log to file
            self._log_change(old_bw, bandwidth, reason)
            
            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(old_bw, bandwidth, reason)
                except:
                    pass
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to set bandwidth: {e}")
            return False
    
    def _log_change(self, old_bw: int, new_bw: int, reason: str):
        """Log bandwidth change to file."""
        try:
            with open(LOG_FILE, 'a') as f:
                timestamp = datetime.now().isoformat()
                rssi = self.monitor.get_worst_rssi() or 0
                f.write(f"{timestamp},{old_bw},{new_bw},{rssi},{reason}\n")
        except Exception as e:
            logger.warning(f"Could not write to log: {e}")
    
    def recommend_bandwidth(self, rssi: int) -> int:
        """
        Recommend optimal bandwidth for given RSSI.
        """
        for bw in reversed(BANDWIDTHS):  # 8, 4, 2, 1
            if rssi >= RSSI_THRESHOLDS[bw]:
                return bw
        return 1
    
    def check_and_adjust(self) -> Optional[int]:
        """
        Check signal and adjust bandwidth if needed.
        
        Returns new bandwidth if changed, None otherwise.
        """
        rssi = self.monitor.sample_rssi()
        if rssi is None:
            logger.debug("No RSSI data available")
            return None
        
        current_bw = self.get_current_bandwidth()
        recommended_bw = self.recommend_bandwidth(rssi)
        now = time.time()
        
        logger.debug(f"RSSI: {rssi} dBm, Current: {current_bw} MHz, Recommended: {recommended_bw} MHz")
        
        # === DOWNGRADE LOGIC (faster) ===
        if recommended_bw < current_bw:
            if self._downgrade_candidate != recommended_bw:
                self._downgrade_candidate = recommended_bw
                self._downgrade_since = now
                return None
            
            time_waiting = now - self._downgrade_since
            if time_waiting >= DOWNGRADE_DELAY_SECONDS:
                reason = f"RSSI {rssi} dBm below threshold for {current_bw} MHz (waited {int(time_waiting)}s)"
                if self.set_bandwidth(recommended_bw, reason):
                    self._downgrade_candidate = None
                    self._upgrade_candidate = None
                    return recommended_bw
            
            return None
        
        self._downgrade_candidate = None
        
        # === UPGRADE LOGIC (slower, with hysteresis) ===
        if recommended_bw > current_bw:
            threshold_for_upgrade = RSSI_THRESHOLDS[recommended_bw] + RSSI_HYSTERESIS_DB
            
            if rssi < threshold_for_upgrade:
                self._upgrade_candidate = None
                return None
            
            if self._upgrade_candidate != recommended_bw:
                self._upgrade_candidate = recommended_bw
                self._upgrade_since = now
                return None
            
            time_waiting = now - self._upgrade_since
            if time_waiting >= UPGRADE_DELAY_SECONDS:
                trend = self.monitor.get_rssi_trend(60)
                if trend is not None and trend < -0.01:
                    return None
                
                reason = f"RSSI {rssi} dBm stable above {threshold_for_upgrade} dBm for {int(time_waiting)}s"
                if self.set_bandwidth(recommended_bw, reason):
                    self._upgrade_candidate = None
                    return recommended_bw
            
            return None
        
        self._upgrade_candidate = None
        return None
    
    def run_monitor(self):
        """Run the bandwidth monitoring loop."""
        logger.info(f"Starting adaptive bandwidth controller on {self.interface}")
        logger.info(f"Current bandwidth: {self.get_current_bandwidth()} MHz")
        
        self._running = True
        last_log_time = 0
        
        while self._running:
            try:
                self.check_and_adjust()
                
                now = time.time()
                if now - last_log_time > 60:
                    rssi = self.monitor.get_worst_rssi()
                    bw = self.get_current_bandwidth()
                    peers = len(self.monitor.peers)
                    logger.info(f"Status: {bw} MHz, RSSI: {rssi} dBm, Peers: {peers}")
                    last_log_time = now
                
                time.sleep(SAMPLE_INTERVAL_SECONDS)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                time.sleep(SAMPLE_INTERVAL_SECONDS)
        
        logger.info("Bandwidth controller stopped")
    
    def stop(self):
        """Stop the monitoring loop."""
        self._running = False
    
    def get_status(self) -> dict:
        """Get current status."""
        rssi = self.monitor.get_worst_rssi()
        return {
            "interface": self.interface,
            "current_bandwidth": self.get_current_bandwidth(),
            "current_rssi": rssi,
            "recommended_bandwidth": self.recommend_bandwidth(rssi or -100),
            "peers": len(self.monitor.peers),
            "state": asdict(self.state),
            "upgrade_pending": self._upgrade_candidate,
            "downgrade_pending": self._downgrade_candidate,
            "thresholds": RSSI_THRESHOLDS,
        }


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Alkaline Network - Adaptive Bandwidth Controller"
    )
    
    parser.add_argument("--interface", "-i", default="halow0",
                       help="Wi-Fi HaLow interface name")
    parser.add_argument("--monitor", "-m", action="store_true",
                       help="Run adaptive bandwidth monitoring")
    parser.add_argument("--set", "-s", type=int, choices=BANDWIDTHS,
                       help="Manually set bandwidth (MHz)")
    parser.add_argument("--status", action="store_true",
                       help="Show current status")
    parser.add_argument("--thresholds", action="store_true",
                       help="Show RSSI thresholds")
    parser.add_argument("--debug", "-d", action="store_true",
                       help="Enable debug logging")
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    controller = AdaptiveBandwidthController(args.interface)
    
    if args.thresholds:
        print("\nRSSI Thresholds for Bandwidth Selection:")
        print("=" * 50)
        for bw in reversed(BANDWIDTHS):
            threshold = RSSI_THRESHOLDS[bw]
            upgrade_threshold = threshold + RSSI_HYSTERESIS_DB
            print(f"  {bw} MHz: RSSI >= {threshold} dBm (upgrade needs >= {upgrade_threshold} dBm)")
        print()
        print(f"Upgrade delay:   {UPGRADE_DELAY_SECONDS}s of sustained good signal")
        print(f"Downgrade delay: {DOWNGRADE_DELAY_SECONDS}s of bad signal")
        return
    
    if args.status:
        status = controller.get_status()
        print("\nAdaptive Bandwidth Status:")
        print("=" * 50)
        print(f"  Interface:   {status['interface']}")
        print(f"  Bandwidth:   {status['current_bandwidth']} MHz")
        print(f"  RSSI:        {status['current_rssi']} dBm")
        print(f"  Recommended: {status['recommended_bandwidth']} MHz")
        print(f"  Peers:       {status['peers']}")
        print(f"  Changes:     {status['state']['change_count']} total")
        print(f"  Upgrades:    {status['state']['total_upgrades']}")
        print(f"  Downgrades:  {status['state']['total_downgrades']}")
        return
    
    if args.set:
        success = controller.set_bandwidth(args.set, "Manual override")
        if success:
            print(f"Bandwidth set to {args.set} MHz")
        else:
            print("Failed to set bandwidth")
            sys.exit(1)
        return
    
    if args.monitor:
        try:
            controller.run_monitor()
        except KeyboardInterrupt:
            controller.stop()
        return
    
    parser.print_help()


if __name__ == "__main__":
    main()
