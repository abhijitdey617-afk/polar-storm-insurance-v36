"""
airline_manager.py

Multi-Airline Portfolio Management for Polar Storm Insurance V3.6

Handles:
- Multi-airline SOV parsing with airline_id grouping
- Auto-detection of per-airline limits/deductibles from SOV columns
- Airline state management (uploaded, priced, booked, active)
- Per-airline configuration storage
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

@dataclass
class AirlineConfig:
    """Configuration for a single airline"""
    airline_id: str
    airline_name: str  # Optional display name
    per_event_limit: float
    event_deductible: float
    annual_aggregate: float
    status: str  # uploaded, priced, booked, active
    route_count: int
    total_tiv: float
    avg_route_exposure: float
    last_priced: Optional[str] = None
    last_booked: Optional[str] = None
    
    # Risk metrics (populated after pricing)
    aal: Optional[float] = None
    sd: Optional[float] = None
    cv: Optional[float] = None
    tvar_99: Optional[float] = None
    gross_premium: Optional[float] = None
    loss_ratio: Optional[float] = None
    rate_on_line: Optional[float] = None

class AirlinePortfolioManager:
    """Manages multi-airline portfolio"""
    
    def __init__(self, default_per_event_limit=500000, 
                 default_event_deductible=10000,
                 default_annual_aggregate=2000000):
        self.default_per_event_limit = default_per_event_limit
        self.default_event_deductible = default_event_deductible
        self.default_annual_aggregate = default_annual_aggregate
        
        self.airlines: Dict[str, AirlineConfig] = {}
        self.sov_by_airline: Dict[str, pd.DataFrame] = {}
        self.raw_sov: Optional[pd.DataFrame] = None
        
    def parse_multi_airline_sov(self, sov_df: pd.DataFrame) -> Dict[str, str]:
        """
        Parse SOV with multi-airline support
        
        Returns:
            Dict with parsing results and warnings
        """
        self.raw_sov = sov_df.copy()
        results = {
            'status': 'success',
            'warnings': [],
            'airlines_detected': 0
        }
        
        # Check for required airline_id column
        if 'airline_id' not in sov_df.columns:
            results['status'] = 'error'
            results['warnings'].append("❌ CRITICAL: 'airline_id' column not found in SOV")
            return results
        
        # Check required fields
        required_fields = ['flight_id', 'origin', 'destination', 'reroute_cost_usd']
        missing_fields = [f for f in required_fields if f not in sov_df.columns]
        
        if missing_fields:
            results['status'] = 'error'
            results['warnings'].append(f"❌ Missing required fields: {', '.join(missing_fields)}")
            return results
        
        # Optional per-airline limit columns
        limit_columns = {
            'per_event_limit': self.default_per_event_limit,
            'event_deductible': self.default_event_deductible,
            'annual_aggregate': self.default_annual_aggregate
        }
        
        # Check which limit columns exist
        detected_limit_cols = []
        for col, default_val in limit_columns.items():
            if col in sov_df.columns:
                detected_limit_cols.append(col)
        
        if detected_limit_cols:
            results['warnings'].append(f"✅ Auto-detected limit columns: {', '.join(detected_limit_cols)}")
        else:
            results['warnings'].append(f"ℹ️ No per-airline limit columns found. Using defaults.")
        
        # Group by airline
        airline_groups = sov_df.groupby('airline_id')
        results['airlines_detected'] = len(airline_groups)
        
        for airline_id, airline_sov in airline_groups:
            # Get airline name if available
            airline_name = airline_sov['airline_name'].iloc[0] if 'airline_name' in airline_sov.columns else airline_id
            
            # Get limits for this airline (first row if specified, else defaults)
            per_event_limit = float(airline_sov['per_event_limit'].iloc[0]) if 'per_event_limit' in airline_sov.columns else self.default_per_event_limit
            event_deductible = float(airline_sov['event_deductible'].iloc[0]) if 'event_deductible' in airline_sov.columns else self.default_event_deductible
            annual_aggregate = float(airline_sov['annual_aggregate'].iloc[0]) if 'annual_aggregate' in airline_sov.columns else self.default_annual_aggregate
            
            # Calculate summary stats
            route_count = len(airline_sov)
            total_tiv = airline_sov['reroute_cost_usd'].sum()
            
            # Calculate avg route exposure if available
            if 'route_exposure' in airline_sov.columns:
                avg_route_exposure = airline_sov['route_exposure'].mean()
            else:
                avg_route_exposure = 0.0
            
            # Create airline config
            config = AirlineConfig(
                airline_id=str(airline_id),
                airline_name=str(airline_name),
                per_event_limit=per_event_limit,
                event_deductible=event_deductible,
                annual_aggregate=annual_aggregate,
                status='uploaded',
                route_count=route_count,
                total_tiv=total_tiv,
                avg_route_exposure=avg_route_exposure
            )
            
            self.airlines[str(airline_id)] = config
            self.sov_by_airline[str(airline_id)] = airline_sov.copy()
        
        results['warnings'].append(f"✅ Successfully parsed {results['airlines_detected']} airlines")
        
        return results
    
    def get_airline_sov(self, airline_id: str) -> Optional[pd.DataFrame]:
        """Get SOV for specific airline"""
        return self.sov_by_airline.get(airline_id)
    
    def get_airline_config(self, airline_id: str) -> Optional[AirlineConfig]:
        """Get configuration for specific airline"""
        return self.airlines.get(airline_id)
    
    def update_airline_limits(self, airline_id: str, 
                             per_event_limit: Optional[float] = None,
                             event_deductible: Optional[float] = None,
                             annual_aggregate: Optional[float] = None) -> bool:
        """Update limits for specific airline"""
        if airline_id not in self.airlines:
            return False
        
        config = self.airlines[airline_id]
        
        if per_event_limit is not None:
            config.per_event_limit = per_event_limit
        if event_deductible is not None:
            config.event_deductible = event_deductible
        if annual_aggregate is not None:
            config.annual_aggregate = annual_aggregate
        
        return True
    
    def update_airline_pricing(self, airline_id: str, 
                              aal: float, sd: float, cv: float, 
                              tvar_99: float, gross_premium: float,
                              loss_ratio: float, rate_on_line: float):
        """Update pricing metrics for airline after stochastic run"""
        if airline_id not in self.airlines:
            return False
        
        config = self.airlines[airline_id]
        config.aal = aal
        config.sd = sd
        config.cv = cv
        config.tvar_99 = tvar_99
        config.gross_premium = gross_premium
        config.loss_ratio = loss_ratio
        config.rate_on_line = rate_on_line
        config.status = 'priced'
        config.last_priced = datetime.utcnow().isoformat()
        
        return True
    
    def book_airline(self, airline_id: str) -> bool:
        """Mark airline as booked (move to live monitoring)"""
        if airline_id not in self.airlines:
            return False
        
        config = self.airlines[airline_id]
        
        # Can only book if priced
        if config.status != 'priced':
            return False
        
        config.status = 'booked'
        config.last_booked = datetime.utcnow().isoformat()
        
        return True
    
    def unbook_airline(self, airline_id: str) -> bool:
        """Remove airline from live monitoring"""
        if airline_id not in self.airlines:
            return False
        
        config = self.airlines[airline_id]
        config.status = 'priced'  # Back to priced state
        
        return True
    
    def get_airlines_by_status(self, status: str) -> List[AirlineConfig]:
        """Get all airlines with specific status"""
        return [cfg for cfg in self.airlines.values() if cfg.status == status]
    
    def get_portfolio_summary(self) -> Dict:
        """Get portfolio-level summary"""
        total_airlines = len(self.airlines)
        total_routes = sum(cfg.route_count for cfg in self.airlines.values())
        total_tiv = sum(cfg.total_tiv for cfg in self.airlines.values())
        
        status_counts = {}
        for status in ['uploaded', 'priced', 'booked', 'active']:
            status_counts[status] = len(self.get_airlines_by_status(status))
        
        # Portfolio risk metrics (only for priced airlines)
        priced_airlines = self.get_airlines_by_status('priced') + self.get_airlines_by_status('booked')
        
        portfolio_aal = sum(cfg.aal for cfg in priced_airlines if cfg.aal is not None)
        portfolio_premium = sum(cfg.gross_premium for cfg in priced_airlines if cfg.gross_premium is not None)
        portfolio_tiv_priced = sum(cfg.total_tiv for cfg in priced_airlines)
        
        return {
            'total_airlines': total_airlines,
            'total_routes': total_routes,
            'total_tiv': total_tiv,
            'status_counts': status_counts,
            'portfolio_aal': portfolio_aal,
            'portfolio_premium': portfolio_premium,
            'portfolio_tiv_priced': portfolio_tiv_priced,
            'portfolio_loss_ratio': (portfolio_aal / portfolio_premium * 100) if portfolio_premium > 0 else 0,
            'portfolio_rol': (portfolio_premium / portfolio_tiv_priced * 100) if portfolio_tiv_priced > 0 else 0
        }
    
    def to_dataframe(self) -> pd.DataFrame:
        """Export all airline configs to DataFrame"""
        return pd.DataFrame([asdict(cfg) for cfg in self.airlines.values()])
    
    def export_airline_config_csv(self, filename: str):
        """Export airline configurations to CSV"""
        df = self.to_dataframe()
        df.to_csv(filename, index=False)
        return filename
