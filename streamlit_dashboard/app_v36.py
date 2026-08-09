"""
Polar Storm Insurance V3.6 - Multi-Airline Dashboard
Streamlit application with 5 tabs:
1. Airline Management - Upload and configure multi-airline SOV
2. Stochastic Pricing - Per-airline risk metrics and booking
3. Live Monitor - Real-time NOAA PFU monitoring for booked airlines
4. Configuration - Edit tier multipliers and parameters
5. Settings - Documentation and methodology
"""

import streamlit as st
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import tabs
from tabs import airline_management_v36
from tabs import stochastic_pricing_multi_v36
from tabs import live_monitor_multi_v36
from tabs import config_v36
from tabs import settings_v36

# Page config
st.set_page_config(
    page_title="Polar Storm Insurance V3.6 Multi-Airline",
    page_icon="🌌",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'config_v36' not in st.session_state:
    st.session_state.config_v36 = {
        'tier_multipliers': {
            'tier_1': 0.25,
            'tier_2': 0.60,
            'tier_3': 1.00
        },
        'tier_boundaries': {
            'no_trigger_max': 999.999999,
            'tier_1_min': 1000.0,
            'tier_1_max': 2299.999999,
            'tier_2_min': 2300.0,
            'tier_2_max': 9999.999999,
            'tier_3_min': 10000.0
        },
        'premium_params': {
            'cost_of_capital': 0.08,
            'expense_load': 0.15,
            'profit_load': 0.10
        },
        'policy_terms': {
            'per_event_limit': 500000,
            'event_deductible': 10000,
            'annual_aggregate': 2000000
        },
        'other_params': {
            'pfu_trigger': 1000.0,
            'min_duration_hours': 6.0,
            'polar_latitude_threshold': 60.0
        },
        'monitoring_windows': {
            'alert_window_hours': 6,
            'settlement_window_hours': 24,
            'available_windows': [6, 12, 24, 48]
        }
    }

if 'pricing_results_v36' not in st.session_state:
    st.session_state.pricing_results_v36 = {}

# Sidebar
with st.sidebar:
    st.title("🌌 Polar Storm Insurance")
    st.markdown("### V3.6 Multi-Airline")
    st.markdown("---")
    
    st.markdown("**Architecture:**")
    st.info("Discrete Tier Parametric")
    
    st.markdown("**Formula:**")
    st.code("FlightLoss = BaseCost × TierMultiplier × RouteExposure", language="python")
    
    st.markdown("**Current Tier Multipliers:**")
    config = st.session_state.config_v36
    st.write(f"• TIER 1: {config['tier_multipliers']['tier_1']:.0%}")
    st.write(f"• TIER 2: {config['tier_multipliers']['tier_2']:.0%}")
    st.write(f"• TIER 3: {config['tier_multipliers']['tier_3']:.0%}")
    
    st.markdown("---")
    
    # Portfolio stats (if airline manager exists)
    if 'airline_manager' in st.session_state:
        manager = st.session_state.airline_manager
        portfolio = manager.get_portfolio_summary()
        
        st.markdown("**Portfolio Status:**")
        st.metric("Total Airlines", portfolio['total_airlines'])
        st.metric("Uploaded", portfolio['status_counts']['uploaded'])
        st.metric("Priced", portfolio['status_counts']['priced'])
        st.metric("Booked", portfolio['status_counts']['booked'])
    
    st.markdown("---")
    st.info("💡 Multi-Airline Platform\n\nUpload → Price → Book → Monitor")

# Main content
st.title("🌌 Polar Storm Insurance Platform V3.6")
st.markdown("### Multi-Airline Portfolio Management & Real-Time Monitoring")

# Tab navigation
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🏢 Airline Management",
    "💰 Stochastic Pricing", 
    "🚨 Live Monitor",
    "⚙️ Configuration",
    "📚 Settings"
])

with tab1:
    airline_management_v36.render()

with tab2:
    stochastic_pricing_multi_v36.render()

with tab3:
    live_monitor_multi_v36.render()

with tab4:
    config_v36.render()

with tab5:
    settings_v36.render()

# Footer
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666;'>
    Polar Storm Insurance V3.6 Multi-Airline Platform | 
    Discrete Tier Parametric | 
    Real-Time NOAA Integration | 
    Per-Airline Risk Metrics
</div>
""", unsafe_allow_html=True)
