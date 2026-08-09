"""
Configuration Tab - V3.6
Editable tier multipliers and premium parameters
All changes apply immediately to stochastic and live calculations
"""

import streamlit as st
import pandas as pd

def render():
    """Render configuration interface"""
    
    st.title("📊 V3.6 Configuration")
    st.markdown("**Edit all tier multipliers and premium calculation parameters**")
    st.info("💡 Changes apply immediately to all pricing and claim calculations")
    st.markdown("---")
    
    # Create two columns for configuration
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🎯 Tier Multipliers")
        st.markdown("*Percentage of BaseCost paid out per tier*")
        
        config = st.session_state.config_v36
        
        # TIER 1
        st.markdown("#### 🟡 TIER 1 (NOAA S3 - Strong)")
        tier_1_percent = st.slider(
            f"PFU {config['tier_boundaries']['tier_1_min']:.0f} - {config['tier_boundaries']['tier_1_max']:.0f}",
            min_value=0,
            max_value=100,
            value=int(config['tier_multipliers']['tier_1'] * 100),
            step=1,
            format="%d%%",
            key="tier_1_slider_v36",
            help="Most common events - conservative payout"
        )
        tier_1 = tier_1_percent / 100.0
        
        # TIER 2
        st.markdown("#### 🟠 TIER 2 (NOAA S4 - Severe)")
        tier_2_percent = st.slider(
            f"PFU {config['tier_boundaries']['tier_2_min']:.0f} - {config['tier_boundaries']['tier_2_max']:.0f}",
            min_value=0,
            max_value=100,
            value=int(config['tier_multipliers']['tier_2'] * 100),
            step=1,
            format="%d%%",
            key="tier_2_slider_v36",
            help="Moderate frequency - higher payout"
        )
        tier_2 = tier_2_percent / 100.0
        
        # TIER 3
        st.markdown("#### 🔴 TIER 3 (NOAA S5 - Extreme)")
        tier_3_percent = st.slider(
            f"PFU {config['tier_boundaries']['tier_3_min']:.0f}+",
            min_value=0,
            max_value=100,
            value=int(config['tier_multipliers']['tier_3'] * 100),
            step=1,
            format="%d%%",
            key="tier_3_slider_v36",
            help="Rare extreme events - full coverage"
        )
        tier_3 = tier_3_percent / 100.0
        
        st.markdown("---")
        st.success(
            "**Industry Standard:** Progressive scaling 25% → 60% → 100%\n\n"
            "Similar to earthquake/hurricane parametric products"
        )
    
    with col2:
        st.subheader("💰 Premium Calculation Parameters")
        st.markdown("*Capital and loading factors*")
        
        # Cost of Capital
        st.markdown("#### 💵 Cost of Capital")
        cost_of_capital = st.number_input(
            "Annual cost of capital (risk load multiplier)",
            min_value=0.0,
            max_value=0.50,
            value=config['premium_params']['cost_of_capital'],
            step=0.01,
            format="%.2f",
            key="cost_of_capital_input_v36",
            help="Typically 6-10% for catastrophe risk"
        )
        st.caption(f"Current: {cost_of_capital:.0%}")
        
        # Expense Load
        st.markdown("#### 📋 Expense Load")
        expense_load = st.number_input(
            "Administrative expense loading",
            min_value=0.0,
            max_value=0.50,
            value=config['premium_params']['expense_load'],
            step=0.01,
            format="%.2f",
            key="expense_load_input_v36",
            help="Operating costs, claims handling, admin"
        )
        st.caption(f"Current: {expense_load:.0%} of AAL")
        
        # Profit Load
        st.markdown("#### 🎯 Profit Load")
        profit_load = st.number_input(
            "Profit margin loading",
            min_value=0.0,
            max_value=0.50,
            value=config['premium_params']['profit_load'],
            step=0.01,
            format="%.2f",
            key="profit_load_input_v36",
            help="Target profit margin"
        )
        st.caption(f"Current: {profit_load:.0%} of AAL")
        
        st.markdown("---")
        total_expense_profit = expense_load + profit_load
        st.metric("Total Expense + Profit Load", f"{total_expense_profit:.0%}")
        st.caption(f"*Plus {cost_of_capital:.0%} of (TVaR₉₉ - AAL)*")
    
    st.markdown("---")
    
    # Policy Terms section
    st.subheader("📜 Policy Terms")
    col3, col4, col5 = st.columns(3)
    
    with col3:
        per_event_limit = st.number_input(
            "Per-Event Limit ($)",
            min_value=0.0,
            value=float(config['policy_terms']['per_event_limit']),
            step=10000.0,
            format="%.0f",
            key="per_event_limit_input_v36"
        )
    
    with col4:
        event_deductible = st.number_input(
            "Event Deductible ($)",
            min_value=0.0,
            value=float(config['policy_terms']['event_deductible']),
            step=1000.0,
            format="%.0f",
            key="event_deductible_input_v36"
        )
    
    with col5:
        annual_aggregate = st.number_input(
            "Annual Aggregate ($)",
            min_value=0.0,
            value=float(config['policy_terms']['annual_aggregate']),
            step=100000.0,
            format="%.0f",
            key="annual_aggregate_input_v36"
        )
    
    st.markdown("---")
    
    # Other Parameters
    st.subheader("⚙️ Other Parameters")
    col6, col7, col8 = st.columns(3)
    
    with col6:
        pfu_trigger = st.number_input(
            "PFU Trigger",
            min_value=0.0,
            value=float(config['other_params']['pfu_trigger']),
            step=100.0,
            format="%.0f",
            key="pfu_trigger_input_v36",
            help="Minimum PFU to trigger coverage"
        )
    
    with col7:
        min_duration = st.number_input(
            "Min Duration (hours)",
            min_value=0.0,
            value=float(config['other_params']['min_duration_hours']),
            step=1.0,
            format="%.1f",
            key="min_duration_input_v36",
            help="Minimum event duration"
        )
    
    with col8:
        polar_threshold = st.number_input(
            "Polar Latitude Threshold (°)",
            min_value=60.0,
            max_value=70.0,
            value=float(config['other_params']['polar_latitude_threshold']),
            step=1.0,
            format="%.1f",
            key="polar_threshold_input_v36",
            help="Latitude defining polar region"
        )
    
    st.markdown("---")
    
    # Monitoring Windows section
    st.subheader("⏱️ Monitoring Windows")
    st.markdown("*Configure PFU monitoring timeframes for alerts and claim settlement*")
    
    col9, col10 = st.columns(2)
    
    with col9:
        st.markdown("#### 🔔 Alert Window")
        alert_window = st.selectbox(
            "Real-time monitoring window (hours)",
            options=config['monitoring_windows']['available_windows'],
            index=config['monitoring_windows']['available_windows'].index(
                config['monitoring_windows']['alert_window_hours']
            ),
            key="alert_window_select_v36",
            help="Lookback window for operational alerts and triggers"
        )
        st.caption("✓ Fast detection for operational decisions")
        st.caption("✓ Used for initial breach alerts")
    
    with col10:
        st.markdown("#### 📊 Settlement Window")
        settlement_window = st.selectbox(
            "Claim settlement window (hours)",
            options=config['monitoring_windows']['available_windows'],
            index=config['monitoring_windows']['available_windows'].index(
                config['monitoring_windows']['settlement_window_hours']
            ),
            key="settlement_window_select_v36",
            help="Lookback window for determining final payout tier"
        )
        st.caption("✓ Captures full event evolution")
        st.caption("✓ Industry standard (24h recommended)")
    
    st.info(
        "**🎯 How it works:**\n"
        f"• **Alert Window ({config['monitoring_windows']['alert_window_hours']}h):** "
        "Monitors recent PFU for fast breach detection and operational alerts.\n"
        f"• **Settlement Window ({config['monitoring_windows']['settlement_window_hours']}h):** "
        "Determines peak PFU over extended period for accurate tier assignment and final payout."
    )
    
    st.markdown("---")
    
    # Save/Reset buttons
    col_save, col_reset, col_spacer = st.columns([1, 1, 2])
    
    with col_save:
        if st.button("💾 Apply Configuration", type="primary", use_container_width=True):
            # Update session state
            st.session_state.config_v36['tier_multipliers']['tier_1'] = tier_1
            st.session_state.config_v36['tier_multipliers']['tier_2'] = tier_2
            st.session_state.config_v36['tier_multipliers']['tier_3'] = tier_3
            st.session_state.config_v36['premium_params']['cost_of_capital'] = cost_of_capital
            st.session_state.config_v36['premium_params']['expense_load'] = expense_load
            st.session_state.config_v36['premium_params']['profit_load'] = profit_load
            st.session_state.config_v36['policy_terms']['per_event_limit'] = per_event_limit
            st.session_state.config_v36['policy_terms']['event_deductible'] = event_deductible
            st.session_state.config_v36['policy_terms']['annual_aggregate'] = annual_aggregate
            st.session_state.config_v36['other_params']['pfu_trigger'] = pfu_trigger
            st.session_state.config_v36['other_params']['min_duration_hours'] = min_duration
            st.session_state.config_v36['other_params']['polar_latitude_threshold'] = polar_threshold
            st.session_state.config_v36['monitoring_windows']['alert_window_hours'] = alert_window
            st.session_state.config_v36['monitoring_windows']['settlement_window_hours'] = settlement_window
            
            # Clear cached pricing results so they recalculate with new config
            if 'airline_pricing_results' in st.session_state:
                cleared_airlines = list(st.session_state.airline_pricing_results.keys())
                st.session_state.airline_pricing_results = {}
                st.warning(
                    f"⚠️ Configuration changed! Cached pricing results cleared for {len(cleared_airlines)} airline(s). "
                    "Please re-run pricing in the Stochastic Pricing tab to see updated premiums and status."
                )
            
            st.success("✅ Configuration applied! Changes are now active for all calculations.")
            st.balloons()
    
    with col_reset:
        if st.button("🔄 Reset to Defaults", use_container_width=True):
            # Reset to V3.6 defaults
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
                },
                'monitoring_windows': {
                    'alert_window_hours': 6,
                    'settlement_window_hours': 24,
                    'available_windows': [6, 12, 24, 48]
                }
            }
            st.success("✅ Configuration reset to V3.6 defaults!")
            st.rerun()
    
    # Display current configuration summary
    st.markdown("---")
    st.subheader("📋 Current Configuration Summary")
    
    summary_data = {
        'Category': ['Tier Multipliers', '', '', 'Premium Parameters', '', '', 'Policy Terms', '', '', 'Other Parameters', '', '', 'Monitoring Windows', ''],
        'Parameter': [
            'TIER 1 (1000-2299 PFU)',
            'TIER 2 (2300-9999 PFU)',
            'TIER 3 (10000+ PFU)',
            'Cost of Capital',
            'Expense Load',
            'Profit Load',
            'Per-Event Limit',
            'Event Deductible',
            'Annual Aggregate',
            'PFU Trigger',
            'Min Duration',
            'Polar Latitude Threshold',
            'Alert Window',
            'Settlement Window'
        ],
        'Value': [
            f"{st.session_state.config_v36['tier_multipliers']['tier_1']:.0%}",
            f"{st.session_state.config_v36['tier_multipliers']['tier_2']:.0%}",
            f"{st.session_state.config_v36['tier_multipliers']['tier_3']:.0%}",
            f"{st.session_state.config_v36['premium_params']['cost_of_capital']:.0%}",
            f"{st.session_state.config_v36['premium_params']['expense_load']:.0%}",
            f"{st.session_state.config_v36['premium_params']['profit_load']:.0%}",
            f"${st.session_state.config_v36['policy_terms']['per_event_limit']:,.0f}",
            f"${st.session_state.config_v36['policy_terms']['event_deductible']:,.0f}",
            f"${st.session_state.config_v36['policy_terms']['annual_aggregate']:,.0f}",
            f"{st.session_state.config_v36['other_params']['pfu_trigger']:.0f}",
            f"{st.session_state.config_v36['other_params']['min_duration_hours']:.1f}h",
            f"{st.session_state.config_v36['other_params']['polar_latitude_threshold']:.1f}°",
            f"{st.session_state.config_v36['monitoring_windows']['alert_window_hours']}h",
            f"{st.session_state.config_v36['monitoring_windows']['settlement_window_hours']}h"
        ]
    }
    
    st.dataframe(pd.DataFrame(summary_data), use_container_width=True, hide_index=True)
    
    st.success(
        "**💡 Configuration applies to:**\n"
        "• All stochastic pricing simulations\n"
        "• Live claim calculations\n"
        "• Premium computations\n"
        "• Payout tier calculations"
    )
