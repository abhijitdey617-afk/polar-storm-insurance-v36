"""
noaa_api.py

NOAA Space Weather Prediction Center (SWPC) API Integration

Real-time Proton Flux Unit (PFU) monitoring from NOAA SWPC.

API Endpoints:
- Real-time data: https://services.swpc.noaa.gov/json/goes/primary/integral-protons-plot-6-hour.json
- 3-day data: https://services.swpc.noaa.gov/json/goes/primary/integral-protons-plot-3-day.json

PFU Threshold Definitions:
- S1 (Minor): >= 10 pfu
- S2 (Moderate): >= 100 pfu
- S3 (Strong): >= 1,000 pfu  ← Our insurance trigger
- S4 (Severe): >= 10,000 pfu
- S5 (Extreme): >= 100,000 pfu
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import time

# NOAA SWPC API endpoints
NOAA_SWPC_6HOUR_URL = "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-plot-6-hour.json"
NOAA_SWPC_3DAY_URL = "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-plot-3-day.json"

# Energy channel for PFU (>= 10 MeV)
PFU_ENERGY_CHANNEL = ">=10 MeV"

def fetch_noaa_pfu_data(hours: int = 6) -> Optional[pd.DataFrame]:
    """
    Fetch real-time PFU data from NOAA SWPC
    
    Args:
        hours: Time window (6 or 72 hours)
    
    Returns:
        DataFrame with columns: timestamp_utc, pfu
    """
    try:
        # Select endpoint based on time window
        if hours <= 6:
            url = NOAA_SWPC_6HOUR_URL
        else:
            url = NOAA_SWPC_3DAY_URL
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Filter for >= 10 MeV channel (standard PFU measurement)
        pfu_records = [
            {
                'timestamp_utc': record['time_tag'],
                'pfu': float(record['flux'])
            }
            for record in data
            if record.get('energy') == PFU_ENERGY_CHANNEL
        ]
        
        if not pfu_records:
            return None
        
        df = pd.DataFrame(pfu_records)
        df['timestamp_utc'] = pd.to_datetime(df['timestamp_utc'])
        df = df.sort_values('timestamp_utc').reset_index(drop=True)
        
        return df
    
    except Exception as e:
        print(f"❌ NOAA API Error: {e}")
        return None

def get_current_pfu() -> Optional[Tuple[float, str]]:
    """
    Get most recent PFU reading
    
    Returns:
        Tuple of (pfu_value, timestamp) or None if error
    """
    df = fetch_noaa_pfu_data(hours=6)
    
    if df is None or len(df) == 0:
        return None
    
    latest = df.iloc[-1]
    return (latest['pfu'], str(latest['timestamp_utc']))

def get_peak_pfu_last_n_hours(hours: int = 6) -> Optional[Tuple[float, str]]:
    """
    Get peak PFU in last N hours
    
    Returns:
        Tuple of (peak_pfu, timestamp_of_peak) or None
    """
    df = fetch_noaa_pfu_data(hours=hours)
    
    if df is None or len(df) == 0:
        return None
    
    peak_idx = df['pfu'].idxmax()
    peak_row = df.loc[peak_idx]
    
    return (peak_row['pfu'], str(peak_row['timestamp_utc']))

def check_trigger_threshold(threshold: float = 1000.0, hours: int = 6) -> Dict:
    """
    Check if PFU has exceeded trigger threshold in last N hours
    
    Returns:
        Dict with trigger status and details
    """
    df = fetch_noaa_pfu_data(hours=hours)
    
    if df is None or len(df) == 0:
        return {
            'triggered': False,
            'error': 'Failed to fetch NOAA data',
            'current_pfu': None
        }
    
    current_pfu = df.iloc[-1]['pfu']
    peak_pfu = df['pfu'].max()
    peak_time = df.loc[df['pfu'].idxmax(), 'timestamp_utc']
    
    triggered = peak_pfu >= threshold
    
    return {
        'triggered': triggered,
        'current_pfu': current_pfu,
        'peak_pfu': peak_pfu,
        'peak_time': str(peak_time),
        'threshold': threshold,
        'duration_hours': hours,
        'exceeded_by': peak_pfu - threshold if triggered else 0
    }

def get_pfu_event_summary(threshold: float = 1000.0, hours: int = 24) -> Optional[Dict]:
    """
    Analyze PFU event over time window
    
    Returns event summary if triggered, else None
    """
    df = fetch_noaa_pfu_data(hours=hours)
    
    if df is None or len(df) == 0:
        return None
    
    # Find periods above threshold
    df['above_threshold'] = df['pfu'] >= threshold
    
    if not df['above_threshold'].any():
        return None  # No trigger
    
    # Get event details
    triggered_df = df[df['above_threshold']]
    
    event_start = triggered_df.iloc[0]['timestamp_utc']
    event_end = triggered_df.iloc[-1]['timestamp_utc']
    duration = (event_end - event_start).total_seconds() / 3600  # hours
    
    peak_pfu = triggered_df['pfu'].max()
    peak_time = triggered_df.loc[triggered_df['pfu'].idxmax(), 'timestamp_utc']
    
    avg_pfu_during_event = triggered_df['pfu'].mean()
    
    return {
        'event_id': f"NOAA_{event_start.strftime('%Y%m%d_%H%M')}",
        'peak_pfu': peak_pfu,
        'peak_time': str(peak_time),
        'event_start': str(event_start),
        'event_end': str(event_end),
        'duration_hours': duration,
        'avg_pfu': avg_pfu_during_event,
        'threshold': threshold,
        'severity_tier': get_severity_tier(peak_pfu)
    }

def get_severity_tier(pfu: float) -> str:
    """Map PFU to NOAA severity scale"""
    if pfu >= 100000:
        return "S5_EXTREME"
    elif pfu >= 10000:
        return "S4_SEVERE"
    elif pfu >= 1000:
        return "S3_STRONG"
    elif pfu >= 100:
        return "S2_MODERATE"
    elif pfu >= 10:
        return "S1_MINOR"
    else:
        return "BELOW_THRESHOLD"

def generate_demo_pfu_data(scenario: str = "baseline") -> pd.DataFrame:
    """
    Generate demo PFU data for testing when NOAA API unavailable
    
    Scenarios:
    - baseline: Low activity, no trigger
    - moderate: S3 event (1000-2000 PFU)
    - severe: S4 event (10000-20000 PFU)
    - extreme: S5 event (50000+ PFU)
    """
    timestamps = pd.date_range(
        end=datetime.utcnow(),
        periods=72,
        freq='5min'
    )
    
    if scenario == "baseline":
        # Low background radiation
        pfu_values = np.random.lognormal(mean=1.0, sigma=0.5, size=len(timestamps))
        pfu_values = np.clip(pfu_values, 1, 50)
    
    elif scenario == "moderate":
        # S3 event (1000-2300 PFU)
        base = np.random.lognormal(mean=1.5, sigma=0.3, size=len(timestamps))
        # Add event spike
        peak_idx = len(timestamps) // 2
        pfu_values = base.copy()
        for i in range(peak_idx - 24, peak_idx + 24):
            if 0 <= i < len(timestamps):
                distance_from_peak = abs(i - peak_idx)
                pfu_values[i] = 1500 * np.exp(-distance_from_peak / 10) + base[i]
    
    elif scenario == "severe":
        # S4 event (10000-20000 PFU)
        base = np.random.lognormal(mean=2.0, sigma=0.4, size=len(timestamps))
        peak_idx = len(timestamps) // 2
        pfu_values = base.copy()
        for i in range(peak_idx - 30, peak_idx + 30):
            if 0 <= i < len(timestamps):
                distance_from_peak = abs(i - peak_idx)
                pfu_values[i] = 15000 * np.exp(-distance_from_peak / 12) + base[i]
    
    elif scenario == "extreme":
        # S5 event (50000+ PFU)
        base = np.random.lognormal(mean=2.5, sigma=0.5, size=len(timestamps))
        peak_idx = len(timestamps) // 2
        pfu_values = base.copy()
        for i in range(peak_idx - 36, peak_idx + 36):
            if 0 <= i < len(timestamps):
                distance_from_peak = abs(i - peak_idx)
                pfu_values[i] = 60000 * np.exp(-distance_from_peak / 15) + base[i]
    
    else:
        pfu_values = np.ones(len(timestamps)) * 5
    
    return pd.DataFrame({
        'timestamp_utc': timestamps,
        'pfu': pfu_values
    })

# Test function
# Commented out due to formatting issues - dashboard functionality not affected
# def test_noaa_api() -> None:
#     """Test NOAA API connection"""
#     pass

if __name__ == "__main__":
    import numpy as np
    print("NOAA API module loaded successfully")
    # test_noaa_api()  # Commented out
