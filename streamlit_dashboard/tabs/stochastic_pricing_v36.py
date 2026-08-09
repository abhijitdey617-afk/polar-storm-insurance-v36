"""
Stochastic Pricing Tab - V3.6 Enhanced
Uses payout_engine_v36.py with configurable tier multipliers
NOW INCLUDES: Pricing adequacy assessment and dynamic recommendations
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import payout_engine_v36 as pe
import numpy as np

def get_tier_config():
    """Get current tier configuration from session state"""
    config = st.session_state.config_v36
    
    # Build PFU_TIER_CONFIG with current multipliers
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

def calculate_flight_loss_with_config(row, peak_pfu):
    """Calculate flight loss using current config"""
    tier_config = get_tier_config()
    config = st.session_state.config_v36
    
    # Get tier
    pfu = float(peak_pfu)
    tier_name = "NO_TRIGGER"
    for name, cfg in tier_config.items():
        if cfg["min_pfu"] <= pfu <= cfg["max_pfu"]:
            tier_name = name
            break
    
    multiplier = tier_config[tier_name]["multiplier"]
    
    # Get base cost
    base_cost = float(row.get('reroute_cost_usd', 0))
    
    # Get route exposure
    route_exposure = float(row.get('route_exposure', 0))
    
    # Calculate loss
    flight_loss = base_cost * multiplier * route_exposure
    
    return flight_loss, tier_name, multiplier

def run_stochastic_pricing_with_config(sov, stochastic_catalogue):
    """Run stochastic pricing with current config parameters"""
    config = st.session_state.config_v36
    
    # Temporarily update pe module constants
    original_tier_config = pe.PFU_TIER_CONFIG.copy()
    pe.PFU_TIER_CONFIG = get_tier_config()
    
    try:
        # Create policy terms
        policy = pe.PolicyTerms(
            per_event_limit=config['policy_terms']['per_event_limit'],
            event_deductible=config['policy_terms']['event_deductible'],
            annual_aggregate=config['policy_terms']['annual_aggregate']
        )
        
        # Run stochastic pricing
        event_results, annual_results, metrics = pe.run_stochastic_pricing(
            stochastic_catalogue, 
            sov, 
            policy,
            pfu_trigger=config['other_params']['pfu_trigger'],
            min_duration_hours=config['other_params']['min_duration_hours']
        )
        
        # Calculate premium with current config
        premium_calc = pe.gross_premium_with_capital_load(
            aal=metrics['AAL'],
            tvar_99=metrics['TVaR_99'],
            cost_of_capital=config['premium_params']['cost_of_capital'],
            expense_load=config['premium_params']['expense_load'],
            profit_load=config['premium_params']['profit_load']
        )
        
        return event_results, annual_results, metrics, premium_calc
        
    finally:
        # Restore original config
        pe.PFU_TIER_CONFIG = original_tier_config

def assess_pricing_adequacy(metrics, premium_calc, total_tiv, config):
    """
    Assess pricing adequacy based on risk metrics and provide recommendations
    
    Returns:
        dict with assessment results and recommendations
    """
    assessment = {
        'status': 'ADEQUATE',
        'warnings': [],
        'recommendations': [],
        'red_flags': []
    }
    
    # 1. Loss Ratio Check
    loss_ratio = (metrics['AAL'] / premium_calc['GrossPremium']) * 100
    
    if loss_ratio > 85:
        assessment['red_flags'].append(f"⛔ CRITICAL: Loss ratio {loss_ratio:.1f}% exceeds 85% - premium too low")
        assessment['status'] = 'INADEQUATE'
        assessment['recommendations'].append(
            f"Increase premium by {((loss_ratio / 65) - 1) * 100:.1f}% to target 65% loss ratio"
        )
    elif loss_ratio > 75:
        assessment['warnings'].append(f"⚠️ Loss ratio {loss_ratio:.1f}% is high (target: 60-70%)")
        assessment['status'] = 'MARGINAL'
        assessment['recommendations'].append(
            "Consider 10-15% premium increase or reduce limits"
        )
    elif loss_ratio < 50:
        assessment['warnings'].append(f"⚠️ Loss ratio {loss_ratio:.1f}% is very low - potentially overpriced")
        assessment['recommendations'].append(
            "Consider reducing premium by 10-15% to remain competitive"
        )
    
    # 2. Coefficient of Variation (Volatility) Check
    cv = metrics['CV']
    
    if cv > 3.0:
        assessment['warnings'].append(f"⚠️ High volatility (CV={cv:.2f}) - losses are unpredictable")
        assessment['recommendations'].append(
            "Consider higher aggregate limit or increase capital loading"
        )
    elif cv > 2.0:
        assessment['warnings'].append(f"⚠️ Moderate volatility (CV={cv:.2f})")
    
    # 3. Tail Risk Check (TVaR vs AAL)
    tail_risk_ratio = metrics['TVaR_99'] / metrics['AAL']
    
    if tail_risk_ratio > 5.0:
        assessment['red_flags'].append(
            f"⛔ CRITICAL: Extreme tail risk (TVaR/AAL = {tail_risk_ratio:.1f}x)"
        )
        assessment['status'] = 'INADEQUATE'
        assessment['recommendations'].append(
            "Increase per-event limit or add catastrophe excess coverage"
        )
    elif tail_risk_ratio > 3.5:
        assessment['warnings'].append(
            f"⚠️ High tail risk (TVaR/AAL = {tail_risk_ratio:.1f}x)"
        )
        assessment['recommendations'].append(
            "Consider increasing risk load or per-event limit"
        )
    
    # 4. Rate on Line Check
    rate_on_line = (premium_calc['GrossPremium'] / total_tiv) * 100
    
    if rate_on_line < 2.0:
        assessment['warnings'].append(f"⚠️ Very low RoL ({rate_on_line:.2f}%) - may not cover administrative costs")
    elif rate_on_line > 15.0:
        assessment['warnings'].append(f"⚠️ Very high RoL ({rate_on_line:.2f}%) - may be uncompetitive")
    
    # 5. Per-Event Limit vs 100-year RP
    rp_100 = metrics['OEP_1in100']
    per_event_limit = config['policy_terms']['per_event_limit']
    
    if rp_100 > per_event_limit:
        assessment['red_flags'].append(
            f"⛔ CRITICAL: 100-year loss (${rp_100:,.0f}) exceeds per-event limit (${per_event_limit:,.0f})"
        )
        assessment['status'] = 'INADEQUATE'
        assessment['recommendations'].append(
            f"Increase per-event limit to at least ${rp_100:,.0f} (100-year RP)"
        )
    
    # 6. Aggregate Limit vs 200-year RP
    rp_200_aep = metrics['AEP_1in200']
    annual_aggregate = config['policy_terms']['annual_aggregate']
    
    if rp_200_aep > annual_aggregate:
        assessment['warnings'].append(
            f"⚠️ 200-year aggregate loss (${rp_200_aep:,.0f}) exceeds aggregate limit (${annual_aggregate:,.0f})"
        )
        assessment['recommendations'].append(
            f"Consider increasing aggregate to ${rp_200_aep * 1.2:,.0f} for better coverage"
        )
    
    # 7. Deductible vs AAL
    deductible = config['policy_terms']['event_deductible']
    aal = metrics['AAL']
    
    if deductible > aal * 0.5:
        assessment['warnings'].append(
            f"⚠️ Deductible (${deductible:,.0f}) is > 50% of AAL - may reduce claim frequency significantly"
        )
    
    # 8. Capital Loading Check
    capital_buffer = premium_calc['RiskLoad'] / (metrics['TVaR_99'] - metrics['AAL'])
    
    if capital_buffer < 0.06:
        assessment['warnings'].append(
            f"⚠️ Low capital buffer ({capital_buffer:.1%}) - may not adequately compensate for tail risk"
        )
        assessment['recommendations'].append(
            "Increase cost of capital from current level"
        )
    
    return assessment

def render():
    """Render stochastic pricing interface"""
    
    st.title("💰 Stochastic Pricing - V3.6 Enhanced")
    st.markdown("Calculate AAL and premium using discrete tier multipliers with **pricing adequacy assessment**")
    
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
    
    # Upload SOV
    st.subheader("📂 Upload Schedule of Values (SOV)")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        uploaded_sov = st.file_uploader(
            "Upload SOV CSV file",
            type=['csv'],
            help="Required: flight_id, origin, destination, reroute_cost_usd"
        )
    
    with col2:
        if st.button("Use Test Data", use_container_width=True):
            # Load TransPolar Airways test SOV
            try:
                test_sov_path = str(project_root / 'data' / 'transpolar_airways_sov_v36.csv')
                st.session_state.current_sov = pd.read_csv(test_sov_path)
                st.success("✅ Loaded TransPolar Airways test SOV")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to load test SOV: {e}")
    
    if uploaded_sov:
        try:
            sov = pd.read_csv(uploaded_sov)
            st.session_state.current_sov = sov
            st.success(f"✅ Loaded SOV with {len(sov)} routes")
        except Exception as e:
            st.error(f"Failed to load SOV: {e}")
            return
    
    if 'current_sov' not in st.session_state:
        st.warning("⚠️ Please upload an SOV file or use test data to continue")
        return
    
    sov = st.session_state.current_sov
    
    # Display SOV summary
    with st.expander("📊 SOV Summary", expanded=True):
        enriched_sov = pe.enrich_sov_route_exposure(sov)
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Routes", len(enriched_sov))
        with col2:
            st.metric("Total Insured Value", f"${enriched_sov['reroute_cost_usd'].sum():,.0f}")
        with col3:
            st.metric("Avg Route Exposure", f"{enriched_sov['route_exposure'].mean():.1%}")
        with col4:
            st.metric("Avg Polar Hours", f"{enriched_sov['polar_hours'].mean():.1f}h")
        
        st.dataframe(
            enriched_sov[['flight_id', 'route_name', 'reroute_cost_usd', 'duration_hours', 'polar_hours', 'route_exposure']].head(10),
            use_container_width=True,
            hide_index=True
        )
    
    st.markdown("---")
    
    # Stochastic catalogue generation
    st.subheader("🎲 Stochastic Event Catalogue")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        num_years = st.number_input(
            "Simulation Years",
            min_value=10,
            max_value=10000,
            value=100,
            step=10,
            help="Number of years to simulate"
        )
    
    with col2:
        seed = st.number_input(
            "Random Seed",
            min_value=0,
            value=42,
            help="For reproducible results"
        )
    
    with col3:
        if st.button("🚀 Run Pricing", type="primary", use_container_width=True):
            st.session_state.run_pricing_v36 = True
    
    if st.session_state.get('run_pricing_v36', False):
        with st.spinner(f"Running {num_years}-year stochastic pricing simulation..."):
            # Generate stochastic catalogue
            np.random.seed(seed)
            events_per_year = [0, 1, 2, 3]
            event_catalogue = []
            
            for year in range(1, num_years + 1):
                num_events = np.random.choice(events_per_year, p=[0.3, 0.4, 0.2, 0.1])
                
                for event_num in range(1, num_events + 1):
                    pfu_sample = np.random.choice([
                        np.random.lognormal(7.0, 0.5),
                        np.random.lognormal(8.0, 0.4),
                        np.random.lognormal(9.8, 0.3),
                    ], p=[0.70, 0.25, 0.05])
                    
                    duration = max(6.0, np.random.normal(12, 4))
                    
                    event_catalogue.append({
                        'simulation_year': year,
                        'event_id': f'Y{year}_E{event_num}',
                        'peak_pfu': pfu_sample,
                        'duration_hours': duration
                    })
            
            stochastic_catalogue = pd.DataFrame(event_catalogue)
            
            # Run pricing
            event_results, annual_results, metrics, premium_calc = run_stochastic_pricing_with_config(
                sov, 
                stochastic_catalogue
            )
            
            # Store results
            st.session_state.pricing_results_v36['current'] = {
                'event_results': event_results,
                'annual_results': annual_results,
                'metrics': metrics,
                'premium': premium_calc,
                'catalogue': stochastic_catalogue,
                'sov': enriched_sov,
                'config_snapshot': st.session_state.config_v36.copy()
            }
            
            st.success("✅ Pricing complete!")
            st.session_state.run_pricing_v36 = False
            st.rerun()
    
    # Display results
    if 'current' in st.session_state.pricing_results_v36:
        results = st.session_state.pricing_results_v36['current']
        
        st.markdown("---")
        st.subheader("📊 Pricing Results")
        
        # Key metrics
        st.markdown("#### Risk Metrics")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("AAL", f"${results['metrics']['AAL']:,.0f}")
        with col2:
            st.metric("Standard Deviation", f"${results['metrics']['SD']:,.0f}")
        with col3:
            st.metric("TVaR 99%", f"${results['metrics']['TVaR_99']:,.0f}")
        with col4:
            st.metric("CV (Volatility)", f"{results['metrics']['CV']:.2f}")
        
        # Premium breakdown
        st.markdown("#### Premium Calculation")
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            st.metric("AAL", f"${results['premium']['AAL']:,.0f}")
        with col2:
            st.metric("Risk Load", f"${results['premium']['RiskLoad']:,.0f}")
        with col3:
            st.metric("Expense Load", f"${results['premium']['ExpenseLoad']:,.0f}")
        with col4:
            st.metric("Profit Load", f"${results['premium']['ProfitLoad']:,.0f}")
        with col5:
            st.metric("Gross Premium", f"${results['premium']['GrossPremium']:,.0f}", 
                     delta=None, delta_color="normal")
        
        # Rate on line
        total_tiv = results['sov']['reroute_cost_usd'].sum()
        rate_on_line = (results['premium']['GrossPremium'] / total_tiv) * 100
        loss_ratio = (results['metrics']['AAL'] / results['premium']['GrossPremium']) * 100
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Rate on Line", f"{rate_on_line:.2f}%")
        with col2:
            st.metric("Expected Loss Ratio", f"{loss_ratio:.1f}%")
        with col3:
            st.metric("Combined Ratio Target", "~85%")
        
        # PRICING ADEQUACY ASSESSMENT
        st.markdown("---")
        st.subheader("🎯 Pricing Adequacy Assessment")
        
        assessment = assess_pricing_adequacy(
            results['metrics'], 
            results['premium'], 
            total_tiv,
            st.session_state.config_v36
        )
        
        # Status indicator
        status_colors = {
            'ADEQUATE': 'success',
            'MARGINAL': 'warning',
            'INADEQUATE': 'error'
        }
        
        status_messages = {
            'ADEQUATE': '✅ Pricing appears adequate based on risk metrics',
            'MARGINAL': '⚠️ Pricing is marginal - review recommendations',
            'INADEQUATE': '⛔ Pricing is inadequate - immediate action required'
        }
        
        if assessment['status'] == 'ADEQUATE':
            st.success(status_messages[assessment['status']])
        elif assessment['status'] == 'MARGINAL':
            st.warning(status_messages[assessment['status']])
        else:
            st.error(status_messages[assessment['status']])
        
        # Display red flags (critical issues)
        if assessment['red_flags']:
            st.markdown("#### 🚨 Critical Issues")
            for flag in assessment['red_flags']:
                st.error(flag)
        
        # Display warnings
        if assessment['warnings']:
            st.markdown("#### ⚠️ Warnings")
            for warning in assessment['warnings']:
                st.warning(warning)
        
        # Display recommendations
        if assessment['recommendations']:
            st.markdown("#### 💡 Recommendations")
            for i, rec in enumerate(assessment['recommendations'], 1):
                st.info(f"{i}. {rec}")
        
        # Detailed adequacy metrics
        with st.expander("📋 Detailed Adequacy Metrics"):
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**Loss Development Metrics**")
                st.write(f"• **Loss Ratio**: {loss_ratio:.1f}% (Target: 60-70%)")
                st.write(f"• **Tail Risk Ratio**: {results['metrics']['TVaR_99'] / results['metrics']['AAL']:.2f}x (Target: <3.5x)")
                st.write(f"• **Volatility (CV)**: {results['metrics']['CV']:.2f} (Target: <2.5)")
                
                capital_buffer = results['premium']['RiskLoad'] / (results['metrics']['TVaR_99'] - results['metrics']['AAL'])
                st.write(f"• **Capital Buffer**: {capital_buffer:.1%} (Target: 6-10%)")
            
            with col2:
                st.markdown("**Limit Adequacy**")
                per_event_limit = st.session_state.config_v36['policy_terms']['per_event_limit']
                rp_100 = results['metrics']['OEP_1in100']
                limit_coverage = (per_event_limit / rp_100) * 100
                
                st.write(f"• **Per-Event Limit**: ${per_event_limit:,.0f}")
                st.write(f"• **100-yr RP (OEP)**: ${rp_100:,.0f}")
                st.write(f"• **Limit Coverage**: {limit_coverage:.1f}% of 100-yr RP")
                
                annual_aggregate = st.session_state.config_v36['policy_terms']['annual_aggregate']
                rp_200_aep = results['metrics']['AEP_1in200']
                aggregate_coverage = (annual_aggregate / rp_200_aep) * 100
                
                st.write(f"• **Annual Aggregate**: ${annual_aggregate:,.0f}")
                st.write(f"• **200-yr RP (AEP)**: ${rp_200_aep:,.0f}")
                st.write(f"• **Aggregate Coverage**: {aggregate_coverage:.1f}% of 200-yr RP")
        
        # Return periods
        with st.expander("📈 Return Period Analysis"):
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**Aggregate Exceedance Probability (AEP)**")
                aep_data = pd.DataFrame({
                    'Return Period': ['1-in-10', '1-in-25', '1-in-50', '1-in-100', '1-in-200'],
                    'Loss ($)': [
                        results['metrics']['AEP_1in10'],
                        results['metrics']['AEP_1in25'],
                        results['metrics']['AEP_1in50'],
                        results['metrics']['AEP_1in100'],
                        results['metrics']['AEP_1in200']
                    ]
                })
                aep_data['Loss ($)'] = aep_data['Loss ($)'].apply(lambda x: f"${x:,.0f}")
                st.dataframe(aep_data, use_container_width=True, hide_index=True)
            
            with col2:
                st.markdown("**Occurrence Exceedance Probability (OEP)**")
                oep_data = pd.DataFrame({
                    'Return Period': ['1-in-10', '1-in-25', '1-in-50', '1-in-100', '1-in-200'],
                    'Loss ($)': [
                        results['metrics']['OEP_1in10'],
                        results['metrics']['OEP_1in25'],
                        results['metrics']['OEP_1in50'],
                        results['metrics']['OEP_1in100'],
                        results['metrics']['OEP_1in200']
                    ]
                })
                oep_data['Loss ($)'] = oep_data['Loss ($)'].apply(lambda x: f"${x:,.0f}")
                st.dataframe(oep_data, use_container_width=True, hide_index=True)
        
        # Event tier distribution
        with st.expander("🎯 Event Tier Distribution"):
            tier_counts = results['event_results']['severity_tier'].value_counts()
            total_events = len(results['event_results'])
            
            tier_data = []
            for tier in ['TIER_1', 'TIER_2', 'TIER_3']:
                count = tier_counts.get(tier, 0)
                pct = (count / total_events * 100) if total_events > 0 else 0
                config_multiplier = st.session_state.config_v36['tier_multipliers'][tier.lower()]
                
                tier_data.append({
                    'Tier': tier,
                    'Multiplier': f"{config_multiplier:.0%}",
                    'Events': count,
                    'Percentage': f"{pct:.1f}%"
                })
            
            st.dataframe(pd.DataFrame(tier_data), use_container_width=True, hide_index=True)
            
            st.info(f"📊 Qualifying events: {total_events} out of {len(results['catalogue'])} total simulated events")
        
        # Loss distribution chart
        with st.expander("📊 Annual Loss Distribution"):
            fig = go.Figure()
            
            losses = results['annual_results']['annual_loss'].sort_values()
            
            fig.add_trace(go.Histogram(
                x=losses,
                nbinsx=50,
                name='Annual Loss',
                marker_color='lightblue'
            ))
            
            # Add AAL line
            fig.add_vline(
                x=results['metrics']['AAL'],
                line_dash="dash",
                line_color="red",
                annotation_text=f"AAL: ${results['metrics']['AAL']:,.0f}",
                annotation_position="top"
            )
            
            fig.update_layout(
                title="Distribution of Annual Losses",
                xaxis_title="Annual Loss ($)",
                yaxis_title="Frequency",
                showlegend=False,
                height=400
            )
            
            st.plotly_chart(fig, use_container_width=True)
