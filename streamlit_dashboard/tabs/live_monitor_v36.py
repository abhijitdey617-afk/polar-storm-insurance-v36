"""
Live Monitor Tab - V3.6
Real-time claim calculation with configurable tier multipliers
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import payout_engine_v36 as pe

def get_tier_config():
    """Get current tier configuration from session state"""
    config = st.session_state.config_v36
    
    return {
        "NO_TRIGGER": {
            "min_pfu": 0.0, 
            "max_pfu": config['tier_boundaries']['no_trigger_max'], 
            "multiplier": 0.00
        },
        "TIER_1": {
            "min_pfu": config['tier_boundaries']['tier_1_min'], 
            "max_pfu": config['tier_boundaries']['tier_1_max'], 
            "multiplier": config['tier_multipliers']['tier_1']
        },
        "TIER_2": {
            "min_pfu": config['tier_boundaries']['tier_2_min'], 
            "max_pfu": config['tier_boundaries']['tier_2_max'], 
            "multiplier": config['tier_multipliers']['tier_2']
        },
        "TIER_3": {
            "min_pfu": config['tier_boundaries']['tier_3_min'], 
            "max_pfu": float("inf"), 
            "multiplier": config['tier_multipliers']['tier_3']
        },
    }

def render():
    """Render live monitor interface"""
    
    st.title("🚨 Live Monitor - V3.6")
    st.markdown("Real-time claim calculation with discrete tier structure")
    
    # Configuration indicator
    config = st.session_state.config_v36
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("TIER 1 Multiplier", f"{config['tier_multipliers']['tier_1']:.0%}")
    with col2:
        st.metric("TIER 2 Multiplier", f"{config['tier_multipliers']['tier_2']:.0%}")
    with col3:
        st.metric("TIER 3 Multiplier", f"{config['tier_multipliers']['tier_3']:.0%}")
    
    st.info("💡 To change tier multipliers, go to the Configuration tab")
    
    st.markdown("---")
    
    # PFU Event Input
    st.subheader("⚡ Proton Flux Event")
    
    col1, col2 = st.columns(2)
    
    with col1:
        event_id = st.text_input("Event ID", value=f"EVENT_{datetime.now().strftime('%Y%m%d_%H%M')}")
        peak_pfu = st.number_input(
            "Peak PFU",
            min_value=0.0,
            value=5000.0,
            step=100.0,
            format="%.1f",
            help="Peak proton flux units during the event"
        )
    
    with col2:
        event_start = st.date_input("Event Start Date", value=datetime.now())
        duration_hours = st.number_input(
            "Duration (hours)",
            min_value=0.0,
            value=12.0,
            step=1.0,
            format="%.1f",
            help="Event duration in hours"
        )
    
    # Show which tier this PFU falls into
    tier_config = get_tier_config()
    current_tier = "NO_TRIGGER"
    for name, cfg in tier_config.items():
        if cfg["min_pfu"] <= peak_pfu <= cfg["max_pfu"]:
            current_tier = name
            break
    
    tier_colors = {
        "NO_TRIGGER": "gray",
        "TIER_1": "orange",
        "TIER_2": "red",
        "TIER_3": "darkred"
    }
    
    tier_names = {
        "NO_TRIGGER": "Below Trigger",
        "TIER_1": "TIER 1 - Strong (S3)",
        "TIER_2": "TIER 2 - Severe (S4)",
        "TIER_3": "TIER 3 - Extreme (S5)"
    }
    
    st.markdown(f"**Current Tier:** :{tier_colors[current_tier]}[{tier_names[current_tier]}] - {tier_config[current_tier]['multiplier']:.0%} Multiplier")
    
    st.markdown("---")
    
    # Flight Schedule Upload
    st.subheader("✈️ Active Flight Schedule")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        uploaded_schedule = st.file_uploader(
            "Upload flight schedule CSV",
            type=['csv'],
            help="Required: flight_id, origin, destination, scheduled_departure_utc"
        )
    
    with col2:
        if st.button("Use Demo Schedule", use_container_width=True):
            # Create demo schedule
            demo_schedule = pd.DataFrame([
                {"flight_id": "AY105", "airline_id": "FIN", "origin": "JFK", "destination": "HEL", 
                 "scheduled_departure_utc": datetime.now().isoformat(), "flight_status": "ON_TIME"},
                {"flight_id": "AY107", "airline_id": "FIN", "origin": "ORD", "destination": "HEL", 
                 "scheduled_departure_utc": (datetime.now() + timedelta(hours=2)).isoformat(), "flight_status": "ON_TIME"},
                {"flight_id": "SK902", "airline_id": "SAS", "origin": "EWR", "destination": "OSL", 
                 "scheduled_departure_utc": (datetime.now() + timedelta(hours=1)).isoformat(), "flight_status": "ON_TIME"},
            ])
            st.session_state.live_schedule_v36 = demo_schedule
            st.success("✅ Loaded demo schedule")
            st.rerun()
    
    if uploaded_schedule:
        try:
            schedule = pd.read_csv(uploaded_schedule)
            st.session_state.live_schedule_v36 = schedule
            st.success(f"✅ Loaded schedule with {len(schedule)} flights")
        except Exception as e:
            st.error(f"Failed to load schedule: {e}")
            return
    
    if 'live_schedule_v36' not in st.session_state:
        st.warning("⚠️ Please upload a flight schedule or use demo data")
        return
    
    schedule = st.session_state.live_schedule_v36
    
    st.dataframe(schedule.head(10), use_container_width=True, hide_index=True)
    
    st.markdown("---")
    
    # SOV Upload
    st.subheader("📂 Schedule of Values (SOV)")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        uploaded_sov = st.file_uploader(
            "Upload SOV CSV",
            type=['csv'],
            help="Required: flight_id, origin, destination, reroute_cost_usd",
            key="live_sov_uploader"
        )
    
    with col2:
        if st.button("Use Test SOV", use_container_width=True):
            try:
                test_sov_path = str(project_root / 'data' / 'transpolar_airways_sov_v36.csv')
                st.session_state.live_sov_v36 = pd.read_csv(test_sov_path)
                st.success("✅ Loaded test SOV")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to load test SOV: {e}")
    
    if uploaded_sov:
        try:
            sov = pd.read_csv(uploaded_sov)
            st.session_state.live_sov_v36 = sov
            st.success(f"✅ Loaded SOV with {len(sov)} routes")
        except Exception as e:
            st.error(f"Failed to load SOV: {e}")
            return
    
    if 'live_sov_v36' not in st.session_state:
        st.warning("⚠️ Please upload an SOV file or use test data")
        return
    
    sov = st.session_state.live_sov_v36
    
    st.markdown("---")
    
    # Calculate Claim button
    if st.button("🚀 Calculate Claim", type="primary", use_container_width=True):
        with st.spinner("Calculating claim..."):
            # Create live event
            pfu_readings = pd.DataFrame({
                'timestamp_utc': pd.date_range(event_start, periods=int(duration_hours), freq='h'),
                'pfu': [peak_pfu] * int(duration_hours)
            })
            
            live_event = pe.evaluate_live_pfu_event(pfu_readings, event_id=event_id)
            
            # Update tier config in module
            original_tier_config = pe.PFU_TIER_CONFIG.copy()
            pe.PFU_TIER_CONFIG = get_tier_config()
            
            try:
                # Create policy
                policy = pe.PolicyTerms(
                    per_event_limit=config['policy_terms']['per_event_limit'],
                    event_deductible=config['policy_terms']['event_deductible'],
                    annual_aggregate=config['policy_terms']['annual_aggregate']
                )
                
                # Calculate claim
                result = pe.calculate_live_claim_from_scheduled_flights(
                    live_event, 
                    schedule, 
                    sov, 
                    policy,
                    pfu_trigger=config['other_params']['pfu_trigger'],
                    min_duration_hours=config['other_params']['min_duration_hours']
                )
                
                st.session_state.live_claim_result_v36 = result
                
            finally:
                pe.PFU_TIER_CONFIG = original_tier_config
            
            st.success("✅ Claim calculated!")
            st.rerun()
    
    # Display claim results
    if 'live_claim_result_v36' in st.session_state:
        result = st.session_state.live_claim_result_v36
        
        st.markdown("---")
        st.subheader("📊 Claim Results")
        
        # Summary metrics
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Gross Loss", f"${result.gross_event_loss:,.2f}")
        with col2:
            st.metric("After Deductible", f"${result.after_deductible:,.2f}")
        with col3:
            st.metric("Final Payout", f"${result.final_event_payout:,.2f}")
        with col4:
            st.metric("Eligible Flights", result.eligible_flight_count)
        
        # Event details
        with st.expander("⚡ Event Details", expanded=True):
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.write("**Event ID:**", result.event_id)
                st.write("**Peak PFU:**", f"{result.peak_pfu:,.1f}")
            with col2:
                st.write("**Tier:**", result.severity_tier)
                multiplier = tier_config[result.severity_tier]['multiplier']
                st.write("**Multiplier:**", f"{multiplier:.0%}")
            with col3:
                st.write("**Duration:**", f"{result.duration_hours:.1f}h")
                st.write("**Triggering:**", "Yes" if result.is_triggering else "No")
            with col4:
                st.write("**Remaining Aggregate:**", f"${result.remaining_aggregate_after_event:,.2f}")
        
        # Flight-by-flight breakdown
        with st.expander("✈️ Flight-by-Flight Breakdown"):
            if result.flight_results:
                flight_data = []
                for fr in result.flight_results:
                    flight_data.append({
                        'Flight ID': fr.flight_id,
                        'Route': fr.route_name,
                        'Base Cost': f"${fr.base_cost:,.0f}",
                        'Route Exposure': f"{fr.route_exposure:.1%}",
                        'Tier': fr.tier,
                        'Multiplier': f"{fr.tier_multiplier:.0%}",
                        'Flight Loss': f"${fr.flight_loss:,.2f}",
                        'Status': fr.status
                    })
                
                df_flights = pd.DataFrame(flight_data)
                st.dataframe(df_flights, use_container_width=True, hide_index=True)
                
                # Summary by tier
                st.markdown("#### Loss by Tier")
                tier_summary = {}
                for fr in result.flight_results:
                    if fr.tier not in tier_summary:
                        tier_summary[fr.tier] = {'count': 0, 'loss': 0.0}
                    tier_summary[fr.tier]['count'] += 1
                    tier_summary[fr.tier]['loss'] += fr.flight_loss
                
                tier_data = []
                for tier_name, data in tier_summary.items():
                    tier_data.append({
                        'Tier': tier_name,
                        'Flights': data['count'],
                        'Total Loss': f"${data['loss']:,.2f}"
                    })
                
                st.dataframe(pd.DataFrame(tier_data), use_container_width=True, hide_index=True)
            else:
                st.info("No eligible flights for this event")
