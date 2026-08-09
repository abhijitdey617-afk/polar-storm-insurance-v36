"""
Settings Tab - V3.6
System information and documentation
"""

import streamlit as st
import pandas as pd
from pathlib import Path

def render():
    """Render settings interface"""
    
    st.title("⚙️ Settings & Documentation - V3.6")
    st.markdown("System information and V3.6 methodology documentation")
    
    st.markdown("---")
    
    # System Info
    st.subheader("🔧 System Information")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Version Information**")
        st.write("• **Model Version:** V3.6 Phase 1")
        st.write("• **Architecture:** Discrete Tier Parametric")
        st.write("• **Formula:** FlightLoss = BaseCost × TierMultiplier × RouteExposure")
        st.write("• **Configuration:** Real-time editable parameters")
    
    with col2:
        st.markdown("**Current Configuration Status**")
        config = st.session_state.config_v36
        st.write(f"• **TIER 1 Multiplier:** {config['tier_multipliers']['tier_1']:.0%}")
        st.write(f"• **TIER 2 Multiplier:** {config['tier_multipliers']['tier_2']:.0%}")
        st.write(f"• **TIER 3 Multiplier:** {config['tier_multipliers']['tier_3']:.0%}")
        st.write(f"• **Cost of Capital:** {config['premium_params']['cost_of_capital']:.0%}")
    
    st.markdown("---")
    
    # Methodology Documentation
    st.subheader("📚 V3.6 Methodology")
    
    with st.expander("🎯 Tier Structure", expanded=True):
        st.markdown("""
        ### NOAA-Aligned Discrete Tiers
        
        V3.6 uses a **three-tier structure** aligned with NOAA storm classification:
        
        | Tier | PFU Range | NOAA Scale | Multiplier | Description |
        |------|-----------|------------|------------|-------------|
        | **TIER 1** | 1,000 - 2,299 | S3 (Strong) | 25% | Most common events |
        | **TIER 2** | 2,300 - 9,999 | S4 (Severe) | 60% | Moderate frequency |
        | **TIER 3** | 10,000+ | S5 (Extreme) | 100% | Rare extreme events |
        
        #### Industry Precedent
        
        Progressive scaling (25% → 60% → 100%) is standard in parametric insurance:
        
        - **Earthquake:** MMI VI → VII → VIII scaling
        - **Hurricane:** Cat 3 → Cat 4 → Cat 5 attachment points
        - **Flood:** 1m → 2m → 3m water depth triggers
        
        This approach balances:
        1. Frequent small claims (capital-efficient)
        2. Moderate losses (predictable)
        3. Extreme tail events (full protection)
        """)
    
    with st.expander("📐 Route Exposure Calculation"):
        st.markdown("""
        ### Route Exposure = PolarHours / FlightHours
        
        **Objective, verifiable metric derived from:**
        
        1. **Explicit SOV Fields** (if provided):
           - `duration_hours` → total flight time
           - `polar_hours` → time above polar latitude threshold
           - `route_exposure` → pre-calculated ratio
        
        2. **Great-Circle Geometry** (if not provided):
           - Calculate great-circle path between origin/destination
           - Sample 240 points along the route
           - Count segments above 66°N/S latitude
           - Derive polar hours from segment count and cruise speed
        
        3. **Airport Master Table**:
           - Automatic coordinate lookup for IATA/ICAO codes
           - Fallback to built-in dictionary for major airports
        
        #### Example Routes
        
        | Route | Duration | Polar Hours | Exposure |
        |-------|----------|-------------|----------|
        | JFK→HEL | 8.5h | 4.2h | 49.4% |
        | ORD→CPH | 8.9h | 3.8h | 42.7% |
        | LAX→NRT | 11.2h | 0.0h | 0.0% |
        
        **No claims contribution for zero polar hours** - equatorial routes are automatically excluded.
        """)
    
    with st.expander("💰 Premium Calculation"):
        st.markdown("""
        ### Gross Premium Calculation
        
        ```
        GrossPremium = AAL + RiskLoad + ExpenseLoad + ProfitLoad
        ```
        
        Where:
        - **AAL**: Average Annual Loss from stochastic simulation
        - **RiskLoad**: (TVaR₉₉ - AAL) × CostOfCapital
        - **ExpenseLoad**: AAL × ExpenseLoadFactor
        - **ProfitLoad**: AAL × ProfitLoadFactor
        
        #### Default Parameters (Editable)
        
        | Parameter | Default | Industry Range |
        |-----------|---------|----------------|
        | Cost of Capital | 8% | 6-10% |
        | Expense Load | 15% | 10-20% |
        | Profit Load | 10% | 8-15% |
        
        #### Example Calculation
        
        For a portfolio with:
        - AAL = $100,000
        - TVaR₉₉ = $500,000
        - Risk Load = ($500k - $100k) × 8% = $32,000
        - Expense Load = $100k × 15% = $15,000
        - Profit Load = $100k × 10% = $10,000
        
        **Gross Premium = $157,000**
        
        **Rate on Line** = $157k / TIV × 100%
        """)
    
    with st.expander("🔍 Validation & Testing"):
        st.markdown("""
        ### V3.6 Validation Summary
        
        Full validation completed with TransPolar Airways test fleet:
        
        #### Test Configuration
        - **Airlines**: 1 (TransPolar Airways)
        - **Routes**: 12 polar routes
        - **Total Insured Value**: $3,600,000
        - **Simulation**: 100-year Monte Carlo
        - **Events**: 276 qualifying storms
        
        #### Results
        - **AAL**: $108,788
        - **TVaR₉₉**: $464,620
        - **Gross Premium**: $165,678
        - **Rate on Line**: 4.6%
        - **Expected Loss Ratio**: 65.7%
        
        #### Tier Distribution
        - TIER 1 (25%): 198 events (71.7%)
        - TIER 2 (60%): 68 events (24.6%)
        - TIER 3 (100%): 10 events (3.6%)
        
        ✅ Distribution matches historical NOAA data (70% S3, 25% S4, 5% S5)
        """)
    
    st.markdown("---")
    
    # File Management
    st.subheader("📂 File Management")
    
    with st.expander("💾 Export Configuration"):
        st.markdown("**Current Configuration**")
        
        config_export = st.session_state.config_v36.copy()
        config_df = pd.DataFrame([
            {'Parameter': 'TIER 1 Multiplier', 'Value': f"{config_export['tier_multipliers']['tier_1']:.2f}"},
            {'Parameter': 'TIER 2 Multiplier', 'Value': f"{config_export['tier_multipliers']['tier_2']:.2f}"},
            {'Parameter': 'TIER 3 Multiplier', 'Value': f"{config_export['tier_multipliers']['tier_3']:.2f}"},
            {'Parameter': 'Cost of Capital', 'Value': f"{config_export['premium_params']['cost_of_capital']:.2f}"},
            {'Parameter': 'Expense Load', 'Value': f"{config_export['premium_params']['expense_load']:.2f}"},
            {'Parameter': 'Profit Load', 'Value': f"{config_export['premium_params']['profit_load']:.2f}"},
            {'Parameter': 'Per-Event Limit', 'Value': f"${config_export['policy_terms']['per_event_limit']:,.0f}"},
            {'Parameter': 'Event Deductible', 'Value': f"${config_export['policy_terms']['event_deductible']:,.0f}"},
            {'Parameter': 'Annual Aggregate', 'Value': f"${config_export['policy_terms']['annual_aggregate']:,.0f}"},
        ])
        
        st.dataframe(config_df, use_container_width=True, hide_index=True)
        
        st.download_button(
            label="📥 Download Configuration",
            data=config_df.to_csv(index=False),
            file_name="v36_configuration.csv",
            mime="text/csv",
            use_container_width=True
        )
    
    with st.expander("📄 Required File Formats"):
        st.markdown("""
        ### Schedule of Values (SOV)
        
        **Required Fields:**
        - `flight_id`: Unique flight identifier
        - `origin`: Origin airport code (IATA/ICAO)
        - `destination`: Destination airport code (IATA/ICAO)
        - `reroute_cost_usd`: Contractual reroute cost
        
        **Optional Fields:**
        - `duration_hours`: Flight duration (auto-derived if missing)
        - `polar_hours`: Time in polar region (auto-derived if missing)
        - `route_exposure`: Polar exposure ratio (auto-derived if missing)
        - `origin_latitude`, `origin_longitude`: Coordinates (looked up if missing)
        - `destination_latitude`, `destination_longitude`: Coordinates (looked up if missing)
        
        ---
        
        ### Flight Schedule (Live Claims)
        
        **Required Fields:**
        - `flight_id`: Must match SOV
        - `origin`: Origin airport code
        - `destination`: Destination airport code
        - `scheduled_departure_utc`: ISO 8601 timestamp
        
        **Optional Fields:**
        - `airline_id`: Airline identifier
        - `flight_status`: ON_TIME / DELAYED / CANCELLED (informational only)
        """)
    
    st.markdown("---")
    
    # Documentation Links
    st.subheader("📖 Documentation")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Implementation Docs**")
        st.write("• [V3.6 Phase 1 Summary](../PHASE1_IMPLEMENTATION_SUMMARY.md)")
        st.write("• [V3.6 Validation Report](../V3.6_Phase1_Validation_Report.md)")
        st.write("• [Engine Source Code](../payout_engine_v36.py)")
    
    with col2:
        st.markdown("**Reference Data**")
        st.write("• [TransPolar Airways SOV](../data/transpolar_airways_sov_v36.csv)")
        st.write("• [Airport Master Table](../data/airport_master.csv)")
        st.write("• [Stochastic Catalogue](../data/stochastic/)")
    
    st.markdown("---")
    
    # Reset Options
    st.subheader("🔄 Reset Options")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🔄 Reset Configuration to Defaults", use_container_width=True):
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
                    'tier_3_min': 10000.0,
                },
                'premium_params': {
                    'cost_of_capital': 0.08,
                    'expense_load': 0.15,
                    'profit_load': 0.10
                },
                'policy_terms': {
                    'per_event_limit': 500000.0,
                    'event_deductible': 10000.0,
                    'annual_aggregate': 2000000.0
                },
                'other_params': {
                    'pfu_trigger': 1000.0,
                    'min_duration_hours': 6.0,
                    'polar_latitude_threshold': 66.0
                }
            }
            st.success("✅ Configuration reset to V3.6 defaults!")
            st.rerun()
    
    with col2:
        if st.button("🗑️ Clear All Results", use_container_width=True):
            st.session_state.pricing_results_v36 = {}
            if 'live_claim_result_v36' in st.session_state:
                del st.session_state.live_claim_result_v36
            st.success("✅ All results cleared!")
            st.rerun()
