"""
Stochastic Pricing Tab - V3.6 Multi-Airline Enhanced
Per-airline pricing metrics with booking workflow
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import payout_engine_v36 as pe
import numpy as np

# Pre-generated catalog path
PREGENERATED_CATALOG_PATH = os.path.join(project_root, "data", "stochastic", "Stochastic_Catalogue_Events.csv")

CATALOG_COLUMN_MAPPING = {
    "sim_event_id": "event_id",
    "sim_year": "simulation_year",
    "sim_peak_pfu": "peak_pfu",
    "sim_duration_h": "duration_hours",
}


def normalize_catalog_columns(catalog: pd.DataFrame) -> pd.DataFrame:
    """Map pre-generated CSV columns to payout_engine_v36 expected names."""
    normalized = catalog.copy()
    for old_name, new_name in CATALOG_COLUMN_MAPPING.items():
        if old_name in normalized.columns and new_name not in normalized.columns:
            normalized[new_name] = normalized[old_name]
    return normalized


@st.cache_data(show_spinner="Loading stochastic catalog...")
def load_pregenerated_catalog():
    """Load and cache the pre-generated stochastic catalog (100K years, ~587K events)"""
    try:
        if os.path.exists(PREGENERATED_CATALOG_PATH):
            catalog = normalize_catalog_columns(pd.read_csv(PREGENERATED_CATALOG_PATH))
            
            # Calculate actual statistics
            num_events = len(catalog)
            year_col = "simulation_year" if "simulation_year" in catalog.columns else "sim_year"
            num_years = catalog[year_col].max() if year_col in catalog.columns else 100000
            
            return catalog, True, num_events, num_years
        return None, False, 0, 0
    except Exception as e:
        st.error(f"Error loading pre-generated catalog: {e}")
        return None, False, 0, 0

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

def run_stochastic_pricing_with_config(sov, stochastic_catalogue, policy, use_fast=True, sample_size=10000):
    """
    Run stochastic pricing with current config parameters.
    
    PERFORMANCE OPTIMIZATION:
    - use_fast=True: Use optimized engine with sampling (10-20x faster)
    - sample_size=10000: Sample 10k years instead of processing all 100k
    - Set sample_size=None to use all years (slower but exact)
    
    Args:
        use_fast: If True, use optimized fast pricing engine (default)
        sample_size: Number of years to sample. 10k = ~10x faster, 20k = ~5x faster
    """
    config = st.session_state.config_v36
    
    # Temporarily update pe module constants
    original_tier_config = pe.PFU_TIER_CONFIG.copy()
    pe.PFU_TIER_CONFIG = get_tier_config()
    
    try:
        # Choose pricing engine
        if use_fast:
            # OPTIMIZED: Use fast engine with sampling (10-20x faster!)
            event_results, annual_results, metrics = pe.run_stochastic_pricing_fast(
                stochastic_catalogue, 
                sov, 
                policy,
                pfu_trigger=config['other_params']['pfu_trigger'],
                min_duration_hours=config['other_params']['min_duration_hours'],
                sample_size=sample_size  # Sample 10k years instead of 100k
            )
        else:
            # ORIGINAL: Use full catalogue (slower, for validation only)
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

def build_airline_policy_config(config_obj):
    """Build policy config dict for risk assessment from airline config."""
    return {
        'policy_terms': {
            'per_event_limit': config_obj.per_event_limit,
            'event_deductible': config_obj.event_deductible,
            'annual_aggregate': config_obj.annual_aggregate,
        }
    }


def assess_pricing_adequacy(metrics, premium_calc, total_tiv, config):
    """Assess pricing adequacy and provide dynamic limit/premium recommendations."""
    assessment = {
        'status': 'ADEQUATE',
        'warnings': [],
        'recommendations': [],
        'red_flags': [],
        'critical_actions': [],
        'pricing_actions': [],
        'coverage_actions': [],
        'risk_transfer_actions': [],
        'simulation_confidence': {},
        'loss_ratio': 0.0,
        'tail_risk_ratio': 0.0,
        'limit_coverage_pct': 100.0,
        'aggregate_coverage_pct': 100.0,
    }

    aal = metrics.get('AAL', 0.0) or 0.0
    gross_premium = premium_calc.get('GrossPremium', 0.0) or 0.0
    per_event_limit = config['policy_terms']['per_event_limit']
    annual_aggregate = config['policy_terms']['annual_aggregate']
    deductible = config['policy_terms']['event_deductible']

    loss_ratio = (aal / gross_premium) * 100 if gross_premium > 0 else 0.0
    assessment['loss_ratio'] = loss_ratio

    if loss_ratio > 85:
        assessment['red_flags'].append(f"⛔ CRITICAL: Loss ratio {loss_ratio:.1f}% exceeds 85% — premium too low")
        assessment['status'] = 'INADEQUATE'
        assessment['pricing_actions'].append(
            f"Increase gross premium by ~{((loss_ratio / 65) - 1) * 100:.0f}% to target a 65% loss ratio "
            f"(suggested premium: ${gross_premium * (loss_ratio / 65):,.0f})"
        )
    elif loss_ratio > 75:
        assessment['warnings'].append(f"⚠️ Loss ratio {loss_ratio:.1f}% is high (target: 60–70%)")
        assessment['status'] = 'MARGINAL'
        assessment['pricing_actions'].append(
            f"Raise premium by 10–15% (to ~${gross_premium * 1.12:,.0f}) or reduce per-event/aggregate limits"
        )
    elif loss_ratio < 50 and aal > 0:
        assessment['warnings'].append(f"⚠️ Loss ratio {loss_ratio:.1f}% is very low — potentially overpriced")
        assessment['pricing_actions'].append(
            f"Consider reducing premium by 10–15% (to ~${gross_premium * 0.88:,.0f}) to improve competitiveness"
        )

    cv = metrics.get('CV', np.nan)
    sd = metrics.get('SD', 0.0) or 0.0
    if not np.isnan(cv):
        if cv > 3.0:
            assessment['warnings'].append(f"⚠️ High volatility (CV={cv:.2f}, SD=${sd:,.0f}) — losses are unpredictable")
            assessment['pricing_actions'].append("Increase risk load or cost-of-capital allowance to reflect volatility")
            if assessment['status'] == 'ADEQUATE':
                assessment['status'] = 'MARGINAL'
        elif cv > 2.0:
            assessment['warnings'].append(f"⚠️ Moderate volatility (CV={cv:.2f}, SD=${sd:,.0f})")

    tail_risk_ratio = (metrics.get('TVaR_99', 0.0) / aal) if aal > 0 else 0.0
    assessment['tail_risk_ratio'] = tail_risk_ratio

    # Three-tier tail risk assessment:
    # - ADEQUATE:   Tail Risk ≤ 5.0x (low volatility)
    # - MARGINAL:   Tail Risk 5.0x - 14.0x (profitable but volatile - typical for parametric products)
    # - INADEQUATE: Tail Risk > 14.0x (extreme volatility)
    
    if tail_risk_ratio > 14.0:
        assessment['red_flags'].append(
            f"⛔ CRITICAL: Extreme tail volatility (TVaR/AAL = {tail_risk_ratio:.1f}x exceeds 14x threshold)"
        )
        assessment['status'] = 'INADEQUATE'
        assessment['risk_transfer_actions'].append(
            f"Portfolio restructuring required — tail risk ratio {tail_risk_ratio:.1f}x indicates extreme concentration in high-risk scenarios"
        )
        assessment['risk_transfer_actions'].append("Obtain catastrophe excess-of-loss reinsurance or diversify concentrated routes before booking")
    elif tail_risk_ratio > 5.0:
        # MARGINAL status: profitable but volatile (typical for parametric polar storm coverage)
        assessment['warnings'].append(
            f"⚠️ High tail volatility (TVaR/AAL = {tail_risk_ratio:.1f}x) — tail losses in worst-case events "
            f"are {tail_risk_ratio:.1f}x higher than average annual loss"
        )
        if assessment['status'] == 'ADEQUATE':
            assessment['status'] = 'MARGINAL'
        # Recommendations focus on tail risk management, not limit increases (which don't reduce the ratio)
        tvar_99 = metrics.get('TVaR_99', 0.0)
        suggested_xs_attach = tvar_99 * 0.4  # Stop-loss above 40% of TVaR
        assessment['risk_transfer_actions'].append(f"Consider stop-loss reinsurance attaching around ${suggested_xs_attach:,.0f} per event to address tail volatility")
        assessment['risk_transfer_actions'].append("Evaluate catastrophe excess reinsurance or portfolio diversification to reduce tail concentration")
    elif tail_risk_ratio > 3.5:
        assessment['warnings'].append(f"⚠️ Moderate tail risk (TVaR/AAL = {tail_risk_ratio:.1f}x)")

    rate_on_line = (gross_premium / total_tiv) * 100 if total_tiv > 0 else 0.0
    if rate_on_line < 2.0:
        assessment['warnings'].append(f"⚠️ Very low RoL ({rate_on_line:.2f}%) — may not cover expenses")
    elif rate_on_line > 15.0:
        assessment['warnings'].append(f"⚠️ Very high RoL ({rate_on_line:.2f}%) — may be uncompetitive")

    capped_oep_100 = metrics.get('OEP_1in100', 0.0) or 0.0
    capped_aep_200 = metrics.get('AEP_1in200', 0.0) or 0.0
    ground_up_available = bool(metrics.get('GroundUp_MetricsAvailable', False))
    rp_100_oep = (metrics.get('GroundUp_OEP_1in100', capped_oep_100) if ground_up_available else capped_oep_100) or 0.0
    limit_coverage = (per_event_limit / rp_100_oep * 100) if rp_100_oep > 0 else 100.0
    assessment['limit_coverage_pct'] = limit_coverage

    if rp_100_oep > per_event_limit:
        assessment['red_flags'].append(
            f"⛔ CRITICAL: 100-year OEP loss (${rp_100_oep:,.0f}) exceeds per-event limit (${per_event_limit:,.0f})"
        )
        assessment['status'] = 'INADEQUATE'
        assessment['critical_actions'].append(f"Increase per-event limit to at least ${rp_100_oep * 1.1:,.0f} (110% of the uncapped 100-year OEP benchmark)")
    elif limit_coverage < 120 and rp_100_oep > 0:
        assessment['warnings'].append(
            f"⚠️ Per-event limit covers only {limit_coverage:.0f}% of 100-year OEP RP (${rp_100_oep:,.0f})"
        )
        assessment['coverage_actions'].append(f"Review per-event limit against the uncapped 100-year OEP benchmark (${rp_100_oep:,.0f})")

    rp_200_aep = (metrics.get('GroundUp_AEP_1in200', capped_aep_200) if ground_up_available else capped_aep_200) or 0.0
    aggregate_coverage = (annual_aggregate / rp_200_aep * 100) if rp_200_aep > 0 else 100.0
    assessment['aggregate_coverage_pct'] = aggregate_coverage

    if rp_200_aep > annual_aggregate:
        assessment['warnings'].append(
            f"⚠️ 200-year AEP loss (${rp_200_aep:,.0f}) exceeds annual aggregate (${annual_aggregate:,.0f})"
        )
        assessment['coverage_actions'].append(
            f"Increase annual aggregate to at least ${rp_200_aep * 1.2:,.0f} (120% of 200-year AEP RP)"
        )
        if assessment['status'] == 'ADEQUATE':
            assessment['status'] = 'MARGINAL'
    elif aggregate_coverage < 120 and rp_200_aep > 0:
        assessment['warnings'].append(
            f"⚠️ Aggregate covers only {aggregate_coverage:.0f}% of 200-year AEP RP (${rp_200_aep:,.0f})"
        )

    if deductible > aal * 0.5 and aal > 0:
        assessment['warnings'].append(
            f"⚠️ Deductible (${deductible:,.0f}) exceeds 50% of AAL (${aal:,.0f}) — may suppress recoveries"
        )
        suggested_deductible = max(aal * 0.15, 0)
        assessment['coverage_actions'].append("Review the event deductible against claims frequency, attachment probability, and contract terms; no automatic target is recommended")
    elif deductible > rp_100_oep * 0.25 and rp_100_oep > 0:
        assessment['warnings'].append(
            f"⚠️ Deductible (${deductible:,.0f}) is high relative to 100-year OEP RP (${rp_100_oep:,.0f})"
        )

    agg_exhaust_prob = metrics.get('AggregateExhaustionProbability', 0.0) or 0.0
    if agg_exhaust_prob > 0.05:
        assessment['warnings'].append(
            f"⚠️ Aggregate exhaustion probability is {agg_exhaust_prob:.1%} — limit may bind frequently"
        )
        assessment['coverage_actions'].append("Review annual aggregate exhaustion and consider additional aggregate capacity above the uncapped benchmark")

    tvar_gap = metrics.get('TVaR_99', 0.0) - aal
    if tvar_gap > 0 and premium_calc.get('RiskLoad') is not None:
        capital_buffer = premium_calc['RiskLoad'] / tvar_gap
        if capital_buffer < 0.06:
            assessment['warnings'].append(
                f"⚠️ Low capital buffer ({capital_buffer:.1%}) — premium may undercharge for tail risk"
            )
            assessment['pricing_actions'].append(
                "Increase cost of capital or profit load in Configuration → Premium Parameters"
            )

    if assessment['status'] == 'ADEQUATE' and not assessment['warnings']:
        assessment['coverage_actions'].append(
            "Current limits, deductible, and premium appear well-calibrated — no major adjustments needed"
        )

    simulation_years = int(metrics.get('SimulationYears', 0) or 0)
    catalogue_years = int(metrics.get('CatalogueYears', 0) or 0)
    coverage_pct = (simulation_years / catalogue_years * 100) if catalogue_years else 0.0
    assessment['simulation_confidence'] = {
        'simulation_years': simulation_years,
        'catalogue_years': catalogue_years,
        'catalogue_coverage_pct': coverage_pct,
        'is_full_catalogue': bool(catalogue_years and simulation_years >= catalogue_years),
        'is_capped_comparison': not ground_up_available,
    }
    if simulation_years and catalogue_years and simulation_years < catalogue_years:
        assessment['warnings'].append(
            f"Simulation confidence: {simulation_years:,} of {catalogue_years:,} catalogue years used; validate with the full catalogue before booking"
        )
    if not ground_up_available:
        assessment['warnings'].append("Limit comparisons use policy-capped losses; uncovered ground-up exposure could not be assessed")

    assessment['recommendations'] = (
        assessment['critical_actions'] + assessment['pricing_actions'] +
        assessment['coverage_actions'] + assessment['risk_transfer_actions']
    )

    return assessment


def render_return_period_metrics(metrics, config_obj=None):
    """Display AEP/OEP return period tables and chart."""
    st.markdown("##### 📈 Return Period Analysis")

    return_periods = ['1-in-10', '1-in-25', '1-in-50', '1-in-100', '1-in-200']
    aep_values = [
        metrics.get('AEP_1in10', 0), metrics.get('AEP_1in25', 0), metrics.get('AEP_1in50', 0),
        metrics.get('AEP_1in100', 0), metrics.get('AEP_1in200', 0),
    ]
    oep_values = [
        metrics.get('OEP_1in10', 0), metrics.get('OEP_1in25', 0), metrics.get('OEP_1in50', 0),
        metrics.get('OEP_1in100', 0), metrics.get('OEP_1in200', 0),
    ]

    col1, col2, col3, col4, col5 = st.columns(5)
    key_rps = [
        ('OEP 1-in-10', oep_values[0]),
        ('OEP 1-in-100', oep_values[3]),
        ('AEP 1-in-100', aep_values[3]),
        ('AEP 1-in-200', aep_values[4]),
        ('TVaR 99.5%', metrics.get('TVaR_995', 0)),
    ]
    for col, (label, value) in zip([col1, col2, col3, col4, col5], key_rps):
        with col:
            st.metric(label, f"${value:,.0f}")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Aggregate Exceedance (AEP)**")
        aep_df = pd.DataFrame({'Return Period': return_periods, 'Loss ($)': aep_values})
        st.dataframe(
            aep_df.assign(**{'Loss ($)': aep_df['Loss ($)'].map(lambda x: f"${x:,.0f}")}),
            use_container_width=True,
            hide_index=True,
        )
    with col2:
        st.markdown("**Occurrence Exceedance (OEP)**")
        oep_df = pd.DataFrame({'Return Period': return_periods, 'Loss ($)': oep_values})
        st.dataframe(
            oep_df.assign(**{'Loss ($)': oep_df['Loss ($)'].map(lambda x: f"${x:,.0f}")}),
            use_container_width=True,
            hide_index=True,
        )

    rp_chart_df = pd.DataFrame({
        'Return Period': return_periods * 2,
        'Loss ($)': aep_values + oep_values,
        'Type': ['AEP'] * 5 + ['OEP'] * 5,
    })
    fig_rp = px.line(
        rp_chart_df,
        x='Return Period',
        y='Loss ($)',
        color='Type',
        markers=True,
        title='Return Period Loss Curve',
    )
    fig_rp.update_layout(height=350, yaxis_tickprefix='$', yaxis_tickformat=',.0f')

    if config_obj is not None:
        fig_rp.add_hline(
            y=config_obj.per_event_limit,
            line_dash='dash',
            line_color='orange',
            annotation_text='Per-Event Limit',
        )
        fig_rp.add_hline(
            y=config_obj.annual_aggregate,
            line_dash='dot',
            line_color='red',
            annotation_text='Annual Aggregate',
        )

    st.plotly_chart(fig_rp, use_container_width=True)


def render_risk_assessment_panel(metrics, premium_calc, config_obj, assessment=None):
    """Display pricing adequacy status, warnings, and actionable recommendations."""
    if assessment is None:
        assessment = assess_pricing_adequacy(
            metrics,
            premium_calc,
            config_obj.total_tiv,
            build_airline_policy_config(config_obj),
        )

    st.markdown("##### 🎯 Risk Assessment & Recommendations")

    status_labels = {
        'ADEQUATE': '✅ Adequate — limits and premium appear well-calibrated',
        'MARGINAL': '⚠️ Marginal — review recommended adjustments below',
        'INADEQUATE': '⛔ Inadequate — action required before booking',
    }
    if assessment['status'] == 'ADEQUATE':
        st.success(status_labels['ADEQUATE'])
    elif assessment['status'] == 'MARGINAL':
        st.warning(status_labels['MARGINAL'])
    else:
        st.error(status_labels['INADEQUATE'])

    summary_col1, summary_col2, summary_col3, summary_col4, summary_col5, summary_col6 = st.columns(6)
    with summary_col1:
        st.metric("AAL", f"${metrics.get('AAL', 0):,.0f}")
    with summary_col2:
        st.metric("Premium", f"${premium_calc.get('GrossPremium', 0):,.0f}")
    with summary_col3:
        st.metric("Loss Ratio", f"{assessment['loss_ratio']:.1f}%", help="Target: 60–70%")
    with summary_col4:
        st.metric("TVaR 99%", f"${metrics.get('TVaR_99', 0):,.0f}")
    with summary_col5:
        st.metric("Limit vs 100-yr OEP", f"{assessment['limit_coverage_pct']:.0f}%", help="Ground-up benchmark when available")
    with summary_col6:
        st.metric("Agg vs 200-yr AEP", f"{assessment['aggregate_coverage_pct']:.0f}%", help="Ground-up benchmark when available")

    if assessment['red_flags']:
        for flag in assessment['red_flags']:
            st.error(flag)
    if assessment['warnings']:
        for warning in assessment['warnings']:
            st.warning(warning)
    recommendation_sections = [
        ('Critical actions', 'critical_actions'),
        ('Pricing actions', 'pricing_actions'),
        ('Coverage actions', 'coverage_actions'),
        ('Risk-transfer actions', 'risk_transfer_actions'),
    ]
    for title, key in recommendation_sections:
        actions = assessment.get(key, [])
        if actions:
            st.markdown(f"**{title}:**")
            for action in actions:
                st.info(action)

    confidence = assessment.get('simulation_confidence', {})
    if confidence.get('simulation_years'):
        st.caption(
            f"Simulation confidence: {confidence['simulation_years']:,} years of "
            f"{confidence['catalogue_years']:,} catalogue years "
            f"({confidence['catalogue_coverage_pct']:.1f}% coverage). "
            + ("Full catalogue run." if confidence['is_full_catalogue'] else "Sampled run; validate before booking.")
        )

    with st.expander("📋 Detailed Limit & Premium Diagnostics"):
        diag_col1, diag_col2 = st.columns(2)
        with diag_col1:
            st.markdown("**Policy Terms**")
            st.write(f"• Per-Event Limit: ${config_obj.per_event_limit:,.0f}")
            st.write(f"• Event Deductible: ${config_obj.event_deductible:,.0f}")
            st.write(f"• Annual Aggregate: ${config_obj.annual_aggregate:,.0f}")
            st.write(f"• Gross Premium: ${premium_calc['GrossPremium']:,.0f}")
            st.write(f"• SD: ${metrics.get('SD', 0):,.0f}")
            st.write(f"• CV: {metrics.get('CV', 0):.2f}")
        with diag_col2:
            st.markdown("**Return Period Benchmarks**")
            st.write(f"• OEP 1-in-100: ${metrics.get('OEP_1in100', 0):,.0f}")
            st.write(f"• AEP 1-in-100: ${metrics.get('AEP_1in100', 0):,.0f}")
            st.write(f"• AEP 1-in-200: ${metrics.get('AEP_1in200', 0):,.0f}")
            st.write(f"• TVaR 99%: ${metrics.get('TVaR_99', 0):,.0f}")
            st.write(f"• Aggregate Exhaustion Prob: {metrics.get('AggregateExhaustionProbability', 0):.2%}")

    return assessment

def render():
    """Render multi-airline stochastic pricing interface"""
    
    st.title("💰 Stochastic Pricing - Multi-Airline")
    st.markdown("Per-airline risk metrics and pricing with booking workflow")
    
    # Check if airline manager exists
    if 'airline_manager' not in st.session_state:
        st.warning("⚠️ No airlines loaded. Please upload SOV in Airline Management tab first.")
        return
    
    manager = st.session_state.airline_manager
    
    if len(manager.airlines) == 0:
        st.warning("⚠️ No airlines found. Upload multi-airline SOV in Airline Management tab.")
        return
    
    st.markdown("---")
    
    # Pricing Mode Selection
    st.subheader("🎯 Pricing Mode")
    
    pricing_mode = st.radio(
        "Select pricing mode:",
        ["Single Airline", "All Airlines", "Portfolio View"],
        horizontal=True
    )
    
    st.markdown("---")
    
    if pricing_mode == "Single Airline":
        render_single_airline_pricing(manager)
    elif pricing_mode == "All Airlines":
        render_all_airlines_pricing(manager)
    else:
        render_portfolio_view(manager)

def render_single_airline_pricing(manager):
    """Price single airline with detailed metrics"""
    
    st.subheader("✈️ Single Airline Pricing")
    
    # Select airline
    uploaded_airlines = [cfg for cfg in manager.airlines.values() if cfg.status in ['uploaded', 'priced']]
    
    if len(uploaded_airlines) == 0:
        st.info("ℹ️ All airlines are already booked. Unbook from Live Monitor to re-price.")
        return
    
    airline_options = {f"{cfg.airline_name} ({cfg.airline_id})": cfg.airline_id for cfg in uploaded_airlines}
    selected_display = st.selectbox("Select Airline", list(airline_options.keys()))
    airline_id = airline_options[selected_display]
    
    config_obj = manager.get_airline_config(airline_id)
    airline_sov = manager.get_airline_sov(airline_id)
    
    # Display airline summary
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Routes", config_obj.route_count)
    with col2:
        st.metric("TIV", f"${config_obj.total_tiv:,.0f}")
    with col3:
        st.metric("Per-Event Limit", f"${config_obj.per_event_limit:,.0f}")
    with col4:
        st.metric("Deductible", f"${config_obj.event_deductible:,.0f}")
    
    # Load pre-generated catalog (required)
    pregenerated_catalog, catalog_available, num_events, num_years = load_pregenerated_catalog()
    
    if not catalog_available:
        st.error(
            "⛔ Pre-generated stochastic catalog not found!\n\n"
            f"Expected location: `{PREGENERATED_CATALOG_PATH}`\n\n"
            "Please ensure the file exists before running stochastic pricing."
        )
        return
    
    # Display catalog info
    st.info(f"📊 **Pre-Generated Catalog Loaded:** {num_events:,} events across {num_years:,} simulation years")
    use_pregenerated = True
    
    # Simulation Performance Controls
    with st.expander("⚡ Performance Settings (Optional)", expanded=False):
        st.markdown("""
        **Simulation Period:** Choose the number of years to simulate.
        - **10K years (Recommended):** ~10x faster, <1% accuracy difference
        - **100K years:** Full catalog, exact results (slower)
        """)
        
        sample_size_option = st.selectbox(
            "Simulation Period (years)",
            options=[5000, 10000, 20000, 50000, 100000],
            index=1,  # Default to 10000
            key=f'sample_size_select_{airline_id}',
            help="10k recommended for speed. 50-100k for final validation (slower)."
        )
        
        if sample_size_option < 100000:
            speedup = 100000 // sample_size_option
            st.caption(f"⚡ ~{speedup}x faster than full catalog")
        else:
            st.caption(f"🔬 Full catalog - exact results")
        
        # Store in session state
        st.session_state[f'sample_size_{airline_id}'] = sample_size_option
    
    if st.button("🚀 Run Pricing", type="primary", use_container_width=True):
        st.session_state[f'run_pricing_{airline_id}'] = True
    
    # Run pricing if requested
    if st.session_state.get(f'run_pricing_{airline_id}', False):
        # Professional progress indicators
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Use pre-generated catalog (always)
        stochastic_catalogue = pregenerated_catalog
        sample_size = st.session_state.get(f'sample_size_{airline_id}', 10000)
        
        # Step 1: Initialize (10%)
        status_text.text(f"🔄 Initializing pricing for {config_obj.airline_name}... (10%)")
        progress_bar.progress(0.10)
        st.success(f"✅ Using pre-generated catalog: {len(stochastic_catalogue):,} events across {num_years:,} years")
        
        # Step 2: Create policy and enrich SOV (20%)
        status_text.text(f"📋 Preparing policy terms and route data... (20%)")
        progress_bar.progress(0.20)
        
        policy = pe.PolicyTerms(
            per_event_limit=config_obj.per_event_limit,
            event_deductible=config_obj.event_deductible,
            annual_aggregate=config_obj.annual_aggregate
        )
        
        enriched_sov = pe.enrich_sov_route_exposure(airline_sov)
        
        # Step 3: Run simulation (20% -> 90%)
        status_text.text(f"🎲 Running {sample_size:,} year Monte Carlo simulation... (30%)")
        progress_bar.progress(0.30)
        
        # Use full catalog (no sampling) when sample_size = 100k for exact results
        use_sampling = (sample_size < 100000)
        
        event_results, annual_results, metrics, premium_calc = run_stochastic_pricing_with_config(
            enriched_sov,
            stochastic_catalogue,
            policy,
            use_fast=use_sampling,  # False for 100k (exact), True for <100k (sampled)
            sample_size=sample_size
        )
        metrics['SimulationYears'] = len(annual_results)
        metrics['CatalogueYears'] = num_years
        
        # Step 4: Finalizing (90% -> 100%)
        status_text.text(f"📊 Calculating risk metrics and premium... (90%)")
        progress_bar.progress(0.90)
        
        # Calculate derived metrics
        loss_ratio = (metrics['AAL'] / premium_calc['GrossPremium']) * 100
        rate_on_line = (premium_calc['GrossPremium'] / config_obj.total_tiv) * 100
        
        # Update manager
        manager.update_airline_pricing(
            airline_id,
            aal=metrics['AAL'],
            sd=metrics['SD'],
            cv=metrics['CV'],
            tvar_99=metrics['TVaR_99'],
            gross_premium=premium_calc['GrossPremium'],
            loss_ratio=loss_ratio,
            rate_on_line=rate_on_line
        )
        
        # Store results
        if 'airline_pricing_results' not in st.session_state:
            st.session_state.airline_pricing_results = {}
        
        st.session_state.airline_pricing_results[airline_id] = {
            'event_results': event_results,
            'annual_results': annual_results,
            'metrics': metrics,
            'premium': premium_calc,
            'sov': enriched_sov
        }
        
        # Complete!
        status_text.text(f"✅ Pricing complete for {config_obj.airline_name}! (100%)")
        progress_bar.progress(1.0)
        st.success(f"✅ Successfully priced {config_obj.airline_name}")
        st.session_state[f'run_pricing_{airline_id}'] = False
        st.rerun()
    
    # Display results if available
    if 'airline_pricing_results' in st.session_state and airline_id in st.session_state.airline_pricing_results:
        results = st.session_state.airline_pricing_results[airline_id]
        
        st.markdown("---")
        st.subheader("📊 Pricing Results")
        
        # Key metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("AAL", f"${results['metrics']['AAL']:,.0f}")
        with col2:
            st.metric("SD", f"${results['metrics']['SD']:,.0f}")
        with col3:
            st.metric("TVaR 99%", f"${results['metrics']['TVaR_99']:,.0f}")
        with col4:
            st.metric("CV", f"{results['metrics']['CV']:.2f}")
        
        
        # Tier-wise AAL Breakdown
        st.markdown("---")
        st.markdown("##### 🎯 Tier-Wise AAL Breakdown")
        
        tier_1_aal = results['metrics'].get('tier_1_aal', 0)
        tier_1_pct = results['metrics'].get('tier_1_pct', 0)
        tier_2_aal = results['metrics'].get('tier_2_aal', 0)
        tier_2_pct = results['metrics'].get('tier_2_pct', 0)
        tier_3_aal = results['metrics'].get('tier_3_aal', 0)
        tier_3_pct = results['metrics'].get('tier_3_pct', 0)
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric(
                "Tier 1 AAL (1000-2299 PFU)",
                f"${tier_1_aal:,.0f}",
                delta=f"{tier_1_pct:.1f}% of total AAL"
            )
        
        with col2:
            st.metric(
                "Tier 2 AAL (2300-9999 PFU)",
                f"${tier_2_aal:,.0f}",
                delta=f"{tier_2_pct:.1f}% of total AAL"
            )
        
        with col3:
            st.metric(
                "Tier 3 AAL (10000+ PFU)",
                f"${tier_3_aal:,.0f}",
                delta=f"{tier_3_pct:.1f}% of total AAL"
            )
        
        # Tier contribution chart
        if tier_1_aal + tier_2_aal + tier_3_aal > 0:
            tier_data = pd.DataFrame({
                'Tier': ['Tier 1\n(25%)', 'Tier 2\n(60%)', 'Tier 3\n(100%)'],
                'AAL': [tier_1_aal, tier_2_aal, tier_3_aal],
                'Percentage': [tier_1_pct, tier_2_pct, tier_3_pct]
            })
            
            import plotly.express as px
            fig_tier = px.bar(
                tier_data,
                x='Tier',
                y='AAL',
                title='AAL Contribution by Severity Tier',
                text='AAL',
                color='Percentage',
                color_continuous_scale='Blues'
            )
            fig_tier.update_traces(texttemplate='$%{text:,.0f}<br>(%{customdata[0]:.1f}%)', textposition='outside')
            fig_tier.update_traces(customdata=tier_data[['Percentage']].values)
            fig_tier.update_layout(height=350, showlegend=False)
            fig_tier.update_xaxes(title_text="")
            fig_tier.update_yaxes(title_text="Average Annual Loss ($)")
            
            st.plotly_chart(fig_tier, use_container_width=True)
        
        # Premium
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Gross Premium", f"${results['premium']['GrossPremium']:,.0f}")
        with col2:
            st.metric("Loss Ratio", f"{config_obj.loss_ratio:.1f}%")
        with col3:
            st.metric("Rate on Line", f"{config_obj.rate_on_line:.2f}%")

        st.markdown("---")
        render_return_period_metrics(results['metrics'], config_obj)

        st.markdown("---")
        render_risk_assessment_panel(
            results['metrics'],
            results['premium'],
            config_obj,
        )
        
        # Book Airline Button
        st.markdown("---")
        col1, col2 = st.columns([1, 2])
        
        with col1:
            if st.button(f"📋 Book {config_obj.airline_name}", type="primary", use_container_width=True):
                if manager.book_airline(airline_id):
                    st.success(f"✅ {config_obj.airline_name} booked! Now available in Live Monitor.")
                    st.rerun()
                else:
                    st.error("Failed to book airline")
        
        with col2:
            st.info("💡 Booking moves this airline to Live Monitor for real-time claim tracking")

def render_all_airlines_pricing(manager):
    """Price all airlines at once"""
    
    st.subheader("📊 Bulk Pricing - All Airlines")
    
    uploaded_airlines = [cfg for cfg in manager.airlines.values() if cfg.status in ['uploaded', 'priced']]
    
    if len(uploaded_airlines) == 0:
        st.info("ℹ️ All airlines are already booked.")
        return
    
    # Load pre-generated catalog (required)
    pregenerated_catalog, catalog_available, num_events, num_years = load_pregenerated_catalog()
    
    if not catalog_available:
        st.error(
            "⛔ Pre-generated stochastic catalog not found!\n\n"
            f"Expected location: `{PREGENERATED_CATALOG_PATH}`\n\n"
            "Please ensure the file exists before running bulk pricing."
        )
        return
    
    st.write(f"Ready to price {len(uploaded_airlines)} airlines using pre-generated catalog ({num_events:,} events across {num_years:,} simulation years)")
    
    # Bulk Pricing Performance Controls
    with st.expander("⚡ Performance Settings (Optional)", expanded=False):
        st.markdown("""
        **For bulk pricing, fast mode is STRONGLY recommended!**
        - **Fast Mode:** {airlines} airlines in ~{fast_time} seconds
        - **Full Mode:** {airlines} airlines in ~{slow_time} minutes (not recommended)
        """.format(
            airlines=len(uploaded_airlines),
            fast_time=len(uploaded_airlines) * 1,
            slow_time=round(len(uploaded_airlines) * 45 / 60, 1)
        ))
        
        col1, col2 = st.columns(2)
        # Simulation period selection for bulk pricing
        bulk_sample_size = st.selectbox(
            "Simulation Period (years)",
            options=[1000, 5000, 10000, 20000, 50000, 100000],
            index=2,  # Default to 10000
            key="bulk_sample_size",
            help="10k recommended for bulk pricing. 50-100k for final validation (slower)."
        )
        
        if bulk_sample_size < 100000:
            speedup = 100000 // bulk_sample_size
            est_time = len(uploaded_airlines) * (8 * speedup / 10)  # Rough estimate: 8 sec per airline at 10k
            st.caption(f"⚡ ~{speedup}x faster | Est. time: ~{int(est_time)} seconds for {len(uploaded_airlines)} airlines")
        else:
            est_time = len(uploaded_airlines) * 80  # ~80 sec per airline at 100k
            st.caption(f"🔬 Full catalog | Est. time: ~{int(est_time)} seconds ({int(est_time/60)} min) for {len(uploaded_airlines)} airlines")
    
    if st.button("🚀 Price All Airlines", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        detail_text = st.empty()
        
        total_airlines = len(uploaded_airlines)
        bulk_sample_size = st.session_state.get('bulk_sample_size', 10000)
        
        for idx, config_obj in enumerate(uploaded_airlines):
            # Calculate overall progress (each airline is 1/total of the work)
            base_progress = idx / total_airlines
            
            # Update status for this airline
            overall_pct = int(base_progress * 100)
            status_text.markdown(f"### 🎯 Bulk Pricing Progress: {overall_pct}% Complete")
            detail_text.text(f"🔄 Pricing {config_obj.airline_name} ({idx+1}/{total_airlines}) | {bulk_sample_size:,} year simulation")
            progress_bar.progress(base_progress)
            
            # Same pricing logic as single airline
            airline_sov = manager.get_airline_sov(config_obj.airline_id)
            
            # Use pre-generated catalogue (always)
            stochastic_catalogue = pregenerated_catalog
            
            # Sub-step: Preparing (add 20% of this airline's portion)
            sub_progress = base_progress + (0.2 / total_airlines)
            detail_text.text(f"📋 Preparing {config_obj.airline_name} policy and routes... ({int(sub_progress * 100)}%)")
            progress_bar.progress(sub_progress)
            
            policy = pe.PolicyTerms(
                per_event_limit=config_obj.per_event_limit,
                event_deductible=config_obj.event_deductible,
                annual_aggregate=config_obj.annual_aggregate
            )
            
            enriched_sov = pe.enrich_sov_route_exposure(airline_sov)
            
            # Sub-step: Simulating (add another 60% of this airline's portion)
            sub_progress = base_progress + (0.8 / total_airlines)
            detail_text.text(f"🎲 Running {bulk_sample_size:,} year simulation for {config_obj.airline_name}... ({int(sub_progress * 100)}%)")
            progress_bar.progress(sub_progress)
            
            use_sampling = (bulk_sample_size < 100000)
            event_results, annual_results, metrics, premium_calc = run_stochastic_pricing_with_config(
                enriched_sov,
                stochastic_catalogue,
                policy,
                use_fast=use_sampling,
                sample_size=bulk_sample_size
            )
            metrics['SimulationYears'] = len(annual_results)
            metrics['CatalogueYears'] = num_years
            
            loss_ratio = (metrics['AAL'] / premium_calc['GrossPremium']) * 100
            rate_on_line = (premium_calc['GrossPremium'] / config_obj.total_tiv) * 100
            
            manager.update_airline_pricing(
                config_obj.airline_id,
                aal=metrics['AAL'],
                sd=metrics['SD'],
                cv=metrics['CV'],
                tvar_99=metrics['TVaR_99'],
                gross_premium=premium_calc['GrossPremium'],
                loss_ratio=loss_ratio,
                rate_on_line=rate_on_line
            )
            
            # Store full results including tier-wise metrics
            if 'airline_pricing_results' not in st.session_state:
                st.session_state.airline_pricing_results = {}
            
            st.session_state.airline_pricing_results[config_obj.airline_id] = {
                'event_results': event_results,
                'annual_results': annual_results,
                'metrics': metrics,
                'premium': premium_calc,
                'sov': enriched_sov
            }
            
            # Finalize this airline's progress
            final_progress = (idx + 1) / total_airlines
            detail_text.text(f"✅ Completed {config_obj.airline_name} ({idx+1}/{total_airlines}) | {int(final_progress * 100)}% overall")
            progress_bar.progress(final_progress)
        
        # All done!
        status_text.markdown("### ✅ Bulk Pricing Complete: 100%")
        detail_text.text(f"✨ Successfully priced all {total_airlines} airlines using {bulk_sample_size:,} year simulations")
        progress_bar.progress(1.0)
        st.success(f"✅ Successfully priced {total_airlines} airlines! Results displayed below.")
        st.rerun()

    render_bulk_pricing_results(manager)


def render_bulk_pricing_results(manager):
    """Display per-airline risk metrics after bulk pricing."""
    priced_airlines = [
        cfg for cfg in manager.airlines.values()
        if cfg.status in ['priced', 'booked'] and cfg.aal is not None
    ]

    if not priced_airlines:
        return

    st.markdown("---")
    st.subheader("📊 Per-Airline Risk Metrics")
    st.success(f"✅ {len(priced_airlines)} airline(s) priced")

    comparison_data = []
    for cfg in priced_airlines:
        tier_1_aal = tier_2_aal = tier_3_aal = 0.0
        oep_100 = aep_100 = aep_200 = 0.0
        assessment_status = 'Unknown'
        results = None

        if 'airline_pricing_results' in st.session_state:
            results = st.session_state.airline_pricing_results.get(cfg.airline_id)
            if results:
                metrics = results['metrics']
                tier_1_aal = metrics.get('tier_1_aal', 0)
                tier_2_aal = metrics.get('tier_2_aal', 0)
                tier_3_aal = metrics.get('tier_3_aal', 0)
                oep_100 = metrics.get('OEP_1in100', 0)
                aep_100 = metrics.get('AEP_1in100', 0)
                aep_200 = metrics.get('AEP_1in200', 0)
                assessment = assess_pricing_adequacy(
                    metrics,
                    results['premium'],
                    cfg.total_tiv,
                    build_airline_policy_config(cfg),
                )
                assessment_status = assessment['status']

        comparison_data.append({
            'Airline': f"{cfg.airline_name} ({cfg.airline_id})",
            'Routes': cfg.route_count,
            'TIV': cfg.total_tiv,
            'AAL': cfg.aal,
            'SD': cfg.sd,
            'CV': cfg.cv,
            'OEP 1-in-100': oep_100,
            'AEP 1-in-100': aep_100,
            'AEP 1-in-200': aep_200,
            'TVaR 99%': cfg.tvar_99,
            'Tier 1 AAL': tier_1_aal,
            'Tier 2 AAL': tier_2_aal,
            'Tier 3 AAL': tier_3_aal,
            'Premium': cfg.gross_premium,
            'Loss Ratio %': cfg.loss_ratio,
            'RoL %': cfg.rate_on_line,
            'Assessment': assessment_status,
            'Status': cfg.status,
        })

    comparison_df = pd.DataFrame(comparison_data)

    st.dataframe(
        comparison_df.style.format({
            'TIV': '${:,.0f}',
            'AAL': '${:,.0f}',
            'SD': '${:,.0f}',
            'CV': '{:.2f}',
            'OEP 1-in-100': '${:,.0f}',
            'AEP 1-in-100': '${:,.0f}',
            'AEP 1-in-200': '${:,.0f}',
            'TVaR 99%': '${:,.0f}',
            'Tier 1 AAL': '${:,.0f}',
            'Tier 2 AAL': '${:,.0f}',
            'Tier 3 AAL': '${:,.0f}',
            'Premium': '${:,.0f}',
            'Loss Ratio %': '{:.1f}',
            'RoL %': '{:.2f}',
        }),
        use_container_width=True,
        hide_index=True,
    )

    assessment_counts = comparison_df['Assessment'].value_counts()
    if not assessment_counts.empty:
        st.markdown("#### Portfolio Risk Assessment Summary")
        sum_col1, sum_col2, sum_col3 = st.columns(3)
        with sum_col1:
            st.metric("Adequate", int(assessment_counts.get('ADEQUATE', 0)))
        with sum_col2:
            st.metric("Marginal", int(assessment_counts.get('MARGINAL', 0)))
        with sum_col3:
            st.metric("Inadequate", int(assessment_counts.get('INADEQUATE', 0)))

    col1, col2, col3 = st.columns(3)

    with col1:
        fig_aal = px.bar(
            comparison_df,
            x='Airline',
            y='AAL',
            title='AAL by Airline',
            color='Status',
            text='AAL',
        )
        fig_aal.update_traces(texttemplate='$%{text:,.0f}', textposition='outside')
        fig_aal.update_layout(height=400)
        st.plotly_chart(fig_aal, use_container_width=True)

    with col2:
        rp_chart_df = comparison_df.melt(
            id_vars=['Airline'],
            value_vars=['OEP 1-in-100', 'AEP 1-in-100', 'AEP 1-in-200'],
            var_name='Return Period',
            value_name='Loss ($)',
        )
        fig_rp = px.bar(
            rp_chart_df,
            x='Airline',
            y='Loss ($)',
            color='Return Period',
            barmode='group',
            title='Key Return Period Losses by Airline',
            text='Loss ($)',
        )
        fig_rp.update_traces(texttemplate='$%{text:,.0f}', textposition='outside')
        fig_rp.update_layout(height=400)
        st.plotly_chart(fig_rp, use_container_width=True)

    with col3:
        fig_lr = px.bar(
            comparison_df,
            x='Airline',
            y='Loss Ratio %',
            title='Loss Ratio by Airline',
            color='Loss Ratio %',
            text='Loss Ratio %',
            color_continuous_scale=['green', 'yellow', 'red'],
        )
        fig_lr.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        fig_lr.add_hline(y=70, line_dash="dash", line_color="orange", annotation_text="Target 70%")
        fig_lr.update_layout(height=400)
        st.plotly_chart(fig_lr, use_container_width=True)

    st.markdown("#### Airline Details")
    for cfg in priced_airlines:
        with st.expander(f"{cfg.airline_name} ({cfg.airline_id}) — {cfg.status.title()}"):
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("AAL", f"${cfg.aal:,.0f}")
            with col2:
                st.metric("SD", f"${cfg.sd:,.0f}")
            with col3:
                st.metric("TVaR 99%", f"${cfg.tvar_99:,.0f}")
            with col4:
                st.metric("CV", f"{cfg.cv:.2f}")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Gross Premium", f"${cfg.gross_premium:,.0f}")
            with col2:
                st.metric("Loss Ratio", f"{cfg.loss_ratio:.1f}%")
            with col3:
                st.metric("Rate on Line", f"{cfg.rate_on_line:.2f}%")

            if 'airline_pricing_results' in st.session_state:
                results = st.session_state.airline_pricing_results.get(cfg.airline_id)
                if results:
                    metrics = results['metrics']
                    tier_1_aal = metrics.get('tier_1_aal', 0)
                    tier_2_aal = metrics.get('tier_2_aal', 0)
                    tier_3_aal = metrics.get('tier_3_aal', 0)

                    st.markdown("##### Tier-Wise AAL Breakdown")
                    t1, t2, t3 = st.columns(3)
                    with t1:
                        st.metric(
                            "Tier 1 AAL",
                            f"${tier_1_aal:,.0f}",
                            delta=f"{metrics.get('tier_1_pct', 0):.1f}% of total AAL",
                        )
                    with t2:
                        st.metric(
                            "Tier 2 AAL",
                            f"${tier_2_aal:,.0f}",
                            delta=f"{metrics.get('tier_2_pct', 0):.1f}% of total AAL",
                        )
                    with t3:
                        st.metric(
                            "Tier 3 AAL",
                            f"${tier_3_aal:,.0f}",
                            delta=f"{metrics.get('tier_3_pct', 0):.1f}% of total AAL",
                        )

                    st.markdown("---")
                    render_return_period_metrics(metrics, cfg)
                    st.markdown("---")
                    render_risk_assessment_panel(metrics, results['premium'], cfg)
                else:
                    st.warning(
                        "Detailed return period and assessment data unavailable. "
                        "Re-run **Price All Airlines** to refresh full diagnostics."
                    )

            if cfg.status == 'priced':
                if st.button(
                    f"📋 Book {cfg.airline_name}",
                    key=f"book_bulk_{cfg.airline_id}",
                    use_container_width=True,
                ):
                    if manager.book_airline(cfg.airline_id):
                        st.success(f"✅ {cfg.airline_name} booked! Now available in Live Monitor.")
                        st.rerun()
                    else:
                        st.error("Failed to book airline")


def render_portfolio_view(manager):
    """Show portfolio-level aggregated view"""
    
    st.subheader("📈 Portfolio View")
    
    priced_airlines = manager.get_airlines_by_status('priced') + manager.get_airlines_by_status('booked')
    
    if len(priced_airlines) == 0:
        st.info("ℹ️ No airlines priced yet. Price airlines first to see portfolio view.")
        return
    
    # Portfolio summary
    portfolio = manager.get_portfolio_summary()
    
    st.markdown("#### Portfolio Metrics")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Airlines", portfolio['total_airlines'])
    with col2:
        st.metric("Priced Airlines", len(priced_airlines))
    with col3:
        st.metric("Portfolio AAL", f"${portfolio['portfolio_aal']:,.0f}")
    with col4:
        st.metric("Portfolio Premium", f"${portfolio['portfolio_premium']:,.0f}")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Portfolio Loss Ratio", f"{portfolio['portfolio_loss_ratio']:.1f}%")
    with col2:
        st.metric("Portfolio RoL", f"{portfolio['portfolio_rol']:.2f}%")
    with col3:
        st.metric("Total TIV Priced", f"${portfolio['portfolio_tiv_priced']:,.0f}")
    
    
    # Portfolio Tier-wise AAL Breakdown
    st.markdown("---")
    st.markdown("#### 🎯 Portfolio Tier-Wise AAL")
    
    # Aggregate tier-wise AAL from all priced airlines
    portfolio_tier_1_aal = 0.0
    portfolio_tier_2_aal = 0.0
    portfolio_tier_3_aal = 0.0
    
    if 'airline_pricing_results' in st.session_state:
        for cfg in priced_airlines:
            if cfg.airline_id in st.session_state.airline_pricing_results:
                results = st.session_state.airline_pricing_results[cfg.airline_id]
                portfolio_tier_1_aal += results['metrics'].get('tier_1_aal', 0)
                portfolio_tier_2_aal += results['metrics'].get('tier_2_aal', 0)
                portfolio_tier_3_aal += results['metrics'].get('tier_3_aal', 0)
    
    total_tier_aal = portfolio_tier_1_aal + portfolio_tier_2_aal + portfolio_tier_3_aal
    
    portfolio_tier_1_pct = (portfolio_tier_1_aal / total_tier_aal * 100) if total_tier_aal > 0 else 0
    portfolio_tier_2_pct = (portfolio_tier_2_aal / total_tier_aal * 100) if total_tier_aal > 0 else 0
    portfolio_tier_3_pct = (portfolio_tier_3_aal / total_tier_aal * 100) if total_tier_aal > 0 else 0
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Tier 1 Portfolio AAL",
            f"${portfolio_tier_1_aal:,.0f}",
            delta=f"{portfolio_tier_1_pct:.1f}% of total"
        )
    
    with col2:
        st.metric(
            "Tier 2 Portfolio AAL",
            f"${portfolio_tier_2_aal:,.0f}",
            delta=f"{portfolio_tier_2_pct:.1f}% of total"
        )
    
    with col3:
        st.metric(
            "Tier 3 Portfolio AAL",
            f"${portfolio_tier_3_aal:,.0f}",
            delta=f"{portfolio_tier_3_pct:.1f}% of total"
        )
    
    if total_tier_aal > 0:
        tier_data = pd.DataFrame({
            'Tier': ['Tier 1 (25%)', 'Tier 2 (60%)', 'Tier 3 (100%)'],
            'Portfolio AAL': [portfolio_tier_1_aal, portfolio_tier_2_aal, portfolio_tier_3_aal],
            'Percentage': [portfolio_tier_1_pct, portfolio_tier_2_pct, portfolio_tier_3_pct]
        })
        
        fig_portfolio_tier = px.bar(
            tier_data,
            x='Tier',
            y='Portfolio AAL',
            title='Portfolio AAL by Severity Tier',
            text='Portfolio AAL',
            color='Percentage',
            color_continuous_scale='Reds'
        )
        fig_portfolio_tier.update_traces(
            texttemplate='$%{text:,.0f}<br>(%{customdata[0]:.1f}%)', 
            textposition='outside'
        )
        fig_portfolio_tier.update_traces(customdata=tier_data[['Percentage']].values)
        fig_portfolio_tier.update_layout(height=350, showlegend=False)
        fig_portfolio_tier.update_xaxes(title_text="")
        fig_portfolio_tier.update_yaxes(title_text="Portfolio Average Annual Loss ($)")
        
        st.plotly_chart(fig_portfolio_tier, use_container_width=True)
    
    # Per-airline comparison table
    st.markdown("---")
    st.markdown("#### Per-Airline Comparison")
    
    comparison_data = []
    for cfg in priced_airlines:
        comparison_data.append({
            'Airline': f"{cfg.airline_name} ({cfg.airline_id})",
            'Routes': cfg.route_count,
            'TIV': cfg.total_tiv,
            'AAL': cfg.aal if cfg.aal else 0,
            'SD': cfg.sd if cfg.sd else 0,
            'CV': cfg.cv if cfg.cv else 0,
            'TVaR 99%': cfg.tvar_99 if cfg.tvar_99 else 0,
            'Premium': cfg.gross_premium if cfg.gross_premium else 0,
            'Loss Ratio %': cfg.loss_ratio if cfg.loss_ratio else 0,
            'RoL %': cfg.rate_on_line if cfg.rate_on_line else 0,
            'Status': cfg.status
        })
    
    comparison_df = pd.DataFrame(comparison_data)
    
    st.dataframe(
        comparison_df.style.format({
            'TIV': '${:,.0f}',
            'AAL': '${:,.0f}',
            'SD': '${:,.0f}',
            'CV': '{:.2f}',
            'TVaR 99%': '${:,.0f}',
            'Premium': '${:,.0f}',
            'Loss Ratio %': '{:.1f}',
            'RoL %': '{:.2f}'
        }),
        use_container_width=True,
        hide_index=True
    )
    
    # Visualization
    st.markdown("---")
    st.markdown("#### Portfolio Visualization")
    
    # AAL by airline
    fig = px.bar(
        comparison_df,
        x='Airline',
        y='AAL',
        title='AAL by Airline',
        color='Status',
        text='AAL'
    )
    fig.update_traces(texttemplate='$%{text:,.0f}', textposition='outside')
    fig.update_layout(height=400)
    st.plotly_chart(fig, use_container_width=True)
    
    # Loss ratio comparison
    fig2 = px.bar(
        comparison_df,
        x='Airline',
        y='Loss Ratio %',
        title='Loss Ratio by Airline',
        color='Loss Ratio %',
        text='Loss Ratio %',
        color_continuous_scale=['green', 'yellow', 'red']
    )
    fig2.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
    fig2.add_hline(y=70, line_dash="dash", line_color="orange", annotation_text="Target 70%")
    fig2.update_layout(height=400)
    st.plotly_chart(fig2, use_container_width=True)
