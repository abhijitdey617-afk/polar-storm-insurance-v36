"""
Stochastic Pricing Tab - V3.6.1 Multi-Airline Enhanced
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


def assess_pricing_adequacy(metrics, premium_calc, total_tiv, config, sample_size=None):
    """
    Assess pricing adequacy with prioritized, actionable recommendations.
    
    IMPROVED STRUCTURE:
    - Overall status with simulation confidence
    - Separated critical actions from pricing/coverage/risk-transfer actions
    - Clear distinction between premium levers (premium, load, deductible, limits, reinsurance)
    - Uncovered loss calculations (not just limit ratios)
    - Caution on deductible recommendations
    """
    # Initialize assessment structure
    assessment = {
        'status': 'ADEQUATE',
        'critical_actions': [],  # Required before booking
        'pricing_actions': [],   # Premium/load adjustments
        'coverage_actions': [],  # Limit/aggregate/deductible changes
        'risk_transfer_actions': [],  # Reinsurance/diversification
        'simulation_confidence': '',
        'loss_ratio': 0.0,
        'tail_risk_ratio': 0.0,
        'limit_coverage_pct': 100.0,
        'aggregate_coverage_pct': 100.0,
        'uncovered_100yr_oep': 0.0,  # Uncovered tail loss
        'uncovered_200yr_aep': 0.0,  # Uncovered aggregate tail
    }

    aal = metrics.get('AAL', 0.0) or 0.0
    gross_premium = premium_calc.get('GrossPremium', 0.0) or 0.0
    per_event_limit = config['policy_terms']['per_event_limit']
    annual_aggregate = config['policy_terms']['annual_aggregate']
    deductible = config['policy_terms']['event_deductible']

    # ═══════════════════════════════════════════════════════════════════════
    # SIMULATION CONFIDENCE
    # ═══════════════════════════════════════════════════════════════════════
    if sample_size:
        if sample_size >= 100000:
            confidence = "Full catalog (100K years, exact, deterministic)"
        elif sample_size >= 50000:
            confidence = f"Sampled ({sample_size:,} years, high confidence, ±2% variance)"
        elif sample_size >= 20000:
            confidence = f"Sampled ({sample_size:,} years, moderate confidence, ±5% variance)"
        else:
            confidence = f"⚠️ Sampled ({sample_size:,} years, LOW CONFIDENCE, ±10% variance) — run 100K for booking"
            if sample_size < 20000:
                assessment['critical_actions'].append(
                    f"🔴 CRITICAL: Results based on {sample_size:,} year sample — re-run with 100K simulation before booking for accurate tail risk metrics"
                )
                assessment['status'] = 'INADEQUATE'
        assessment['simulation_confidence'] = confidence
    else:
        assessment['simulation_confidence'] = "Unknown simulation period"

    # ═══════════════════════════════════════════════════════════════════════
    # 1. LOSS RATIO (Premium Adequacy)
    # ═══════════════════════════════════════════════════════════════════════
    loss_ratio = (aal / gross_premium) * 100 if gross_premium > 0 else 0.0
    assessment['loss_ratio'] = loss_ratio

    # THRESHOLD ADJUSTMENT: Changed from 85% to 95% for Inadequate
    # Changed from 75% to 85% for Marginal
    if loss_ratio > 95:
        assessment['critical_actions'].append(
            f"🔴 CRITICAL: Loss ratio {loss_ratio:.1f}% exceeds 95% — premium insufficient to cover expected losses and capital costs"
        )
        assessment['status'] = 'INADEQUATE'
        target_premium = gross_premium * (loss_ratio / 70)
        assessment['pricing_actions'].append(
            f"Increase gross premium by {((loss_ratio / 70) - 1) * 100:.0f}% to ${target_premium:,.0f} (targeting 70% loss ratio)"
        )
    elif loss_ratio > 85:
        assessment['status'] = 'MARGINAL'
        assessment['pricing_actions'].append(
            f"⚠️ Loss ratio {loss_ratio:.1f}% is elevated (target: 60–70%). Consider 10-15% premium increase to ${gross_premium * 1.12:,.0f}"
        )
    elif loss_ratio < 50 and aal > 0:
        assessment['pricing_actions'].append(
            f"Loss ratio {loss_ratio:.1f}% is low — may be overpriced. Consider 10-15% premium reduction to ${gross_premium * 0.88:,.0f} for competitiveness"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 2. TAIL RISK VOLATILITY (TVaR/AAL Ratio)
    # ═══════════════════════════════════════════════════════════════════════
    tail_risk_ratio = (metrics.get('TVaR_99', 0.0) / aal) if aal > 0 else 0.0
    assessment['tail_risk_ratio'] = tail_risk_ratio

    if tail_risk_ratio > 20.0:
        assessment['critical_actions'].append(
            f"🔴 CRITICAL: Extreme tail volatility — TVaR/AAL ratio of {tail_risk_ratio:.1f}x indicates severe concentration in worst-case events"
        )
        assessment['status'] = 'INADEQUATE'
        assessment['risk_transfer_actions'].append(
            "Portfolio restructuring required: (1) reduce high-exposure routes, (2) catastrophe XL reinsurance, or (3) product redesign"
        )
    elif tail_risk_ratio > 14.0:  # 14-20x range
        if assessment['status'] == 'ADEQUATE':
            assessment['status'] = 'MARGINAL'
        tvar_99 = metrics.get('TVaR_99', 0.0)
        suggested_xs_attach = tvar_99 * 0.4
        assessment['risk_transfer_actions'].append(
            f"High tail volatility (TVaR/AAL = {tail_risk_ratio:.1f}x) — typical for parametric products but monitor exposure"
        )
        assessment['risk_transfer_actions'].append(
            f"Consider stop-loss reinsurance attaching around ${suggested_xs_attach:,.0f} per event to cap tail losses"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 3. VOLATILITY (Coefficient of Variation)
    # ═══════════════════════════════════════════════════════════════════════
    cv = metrics.get('CV', np.nan)
    sd = metrics.get('SD', 0.0) or 0.0
    if not np.isnan(cv) and cv > 3.0:
        if assessment['status'] == 'ADEQUATE':
            assessment['status'] = 'MARGINAL'
        assessment['pricing_actions'].append(
            f"⚠️ High volatility (CV={cv:.2f}, SD=${sd:,.0f}) — increase cost-of-capital or profit load in premium parameters"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 4. LIMIT ADEQUACY (Per-Event Limit vs OEP)
    # NOTE: OEP/AEP are policy-capped, so we calculate UNCOVERED losses
    # ═══════════════════════════════════════════════════════════════════════
    rp_100_oep_capped = metrics.get('OEP_1in100', 0.0) or 0.0  # This is capped at limit
    
    # Check if OEP is at limit (indicating capping)
    if abs(rp_100_oep_capped - per_event_limit) / per_event_limit < 0.005:  # Within 0.5%
        # OEP is capped — uncovered exposure exists
        assessment['critical_actions'].append(
            f"🔴 CRITICAL: 100-year OEP loss (${rp_100_oep_capped:,.0f}) equals per-event limit — actual tail exposure is HIGHER but capped in results"
        )
        assessment['status'] = 'INADEQUATE'
        assessment['coverage_actions'].append(
            f"Increase per-event limit above ${per_event_limit:,.0f} to capture true tail exposure (recommend +20-30%: ${per_event_limit * 1.25:,.0f})"
        )
        assessment['uncovered_100yr_oep'] = 0  # Unknown, but exists
    elif rp_100_oep_capped > per_event_limit * 0.95:  # Close to limit
        assessment['coverage_actions'].append(
            f"⚠️ 100-year OEP loss (${rp_100_oep_capped:,.0f}) is {(rp_100_oep_capped / per_event_limit * 100):.0f}% of per-event limit — near full utilization"
        )
        assessment['coverage_actions'].append(
            f"Consider raising per-event limit to ${per_event_limit * 1.2:,.0f} for adequate buffer (20% above current OEP)"
        )
    
    limit_coverage = (per_event_limit / rp_100_oep_capped * 100) if rp_100_oep_capped > 0 else 100.0
    assessment['limit_coverage_pct'] = limit_coverage

    # ═══════════════════════════════════════════════════════════════════════
    # 5. AGGREGATE ADEQUACY (Annual Aggregate vs AEP)
    # ═══════════════════════════════════════════════════════════════════════
    rp_200_aep_capped = metrics.get('AEP_1in200', 0.0) or 0.0  # This is capped at aggregate
    
    # Check if AEP is at aggregate (indicating capping)
    if abs(rp_200_aep_capped - annual_aggregate) / annual_aggregate < 0.005:  # Within 0.5%
        assessment['critical_actions'].append(
            f"🔴 CRITICAL: 200-year AEP loss (${rp_200_aep_capped:,.0f}) equals annual aggregate — actual annual tail exposure is HIGHER but capped"
        )
        assessment['status'] = 'INADEQUATE'
        assessment['coverage_actions'].append(
            f"Increase annual aggregate above ${annual_aggregate:,.0f} to capture true annual tail (recommend +30-50%: ${annual_aggregate * 1.4:,.0f})"
        )
        assessment['uncovered_200yr_aep'] = 0  # Unknown, but exists
    elif rp_200_aep_capped > annual_aggregate * 0.90:  # Close to aggregate
        if assessment['status'] == 'ADEQUATE':
            assessment['status'] = 'MARGINAL'
        assessment['coverage_actions'].append(
            f"⚠️ 200-year AEP loss (${rp_200_aep_capped:,.0f}) is {(rp_200_aep_capped / annual_aggregate * 100):.0f}% of annual aggregate — near exhaustion"
        )
        assessment['coverage_actions'].append(
            f"Raise annual aggregate to ${annual_aggregate * 1.3:,.0f} for adequate buffer"
        )
    
    aggregate_coverage = (annual_aggregate / rp_200_aep_capped * 100) if rp_200_aep_capped > 0 else 100.0
    assessment['aggregate_coverage_pct'] = aggregate_coverage

    # ═══════════════════════════════════════════════════════════════════════
    # 6. DEDUCTIBLE ASSESSMENT (with caution)
    # ═══════════════════════════════════════════════════════════════════════
    if deductible > aal * 0.5 and aal > 0:
        assessment['coverage_actions'].append(
            f"⚠️ Deductible (${deductible:,.0f}) exceeds 50% of AAL (${aal:,.0f}) — may suppress recoveries significantly"
        )
        # CAUTION: Don't automatically recommend 15% of AAL
        assessment['coverage_actions'].append(
            f"⚠️ CAUTION: Deductible reduction may not be commercially appropriate — verify contractual and competitive constraints before adjusting"
        )
    elif deductible > rp_100_oep_capped * 0.3 and rp_100_oep_capped > 0:
        assessment['coverage_actions'].append(
            f"Deductible (${deductible:,.0f}) is {(deductible / rp_100_oep_capped * 100):.0f}% of 100-year OEP — may block tail recoveries"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 7. RATE ON LINE
    # ═══════════════════════════════════════════════════════════════════════
    rate_on_line = (gross_premium / total_tiv) * 100 if total_tiv > 0 else 0.0
    if rate_on_line < 2.0:
        assessment['pricing_actions'].append(
            f"⚠️ Very low Rate on Line ({rate_on_line:.2f}%) — may not cover expenses"
        )
    elif rate_on_line > 15.0:
        assessment['pricing_actions'].append(
            f"⚠️ Very high Rate on Line ({rate_on_line:.2f}%) — may be uncompetitive in market"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 8. AGGREGATE EXHAUSTION PROBABILITY
    # ═══════════════════════════════════════════════════════════════════════
    agg_exhaust_prob = metrics.get('AggregateExhaustionProbability', 0.0) or 0.0
    if agg_exhaust_prob > 0.05:
        assessment['coverage_actions'].append(
            f"⚠️ Aggregate exhaustion probability is {agg_exhaust_prob:.1%} — limit may bind frequently (>5% of years)"
        )
        assessment['coverage_actions'].append(
            f"Raise annual aggregate above ${metrics.get('AEP_1in100', annual_aggregate):,.0f} (100-year AEP)"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 9. CAPITAL BUFFER (Risk Load)
    # ═══════════════════════════════════════════════════════════════════════
    tvar_gap = metrics.get('TVaR_99', 0.0) - aal
    if tvar_gap > 0 and premium_calc.get('RiskLoad') is not None:
        capital_buffer = premium_calc['RiskLoad'] / tvar_gap
        if capital_buffer < 0.06:
            assessment['pricing_actions'].append(
                f"⚠️ Low capital buffer ({capital_buffer:.1%}) — premium may undercharge for tail risk. Increase cost-of-capital in Configuration → Premium Parameters"
            )

    # ═══════════════════════════════════════════════════════════════════════
    # FINAL STATUS: If no critical actions and no pricing/coverage actions
    # ═══════════════════════════════════════════════════════════════════════
    if assessment['status'] == 'ADEQUATE' and not assessment['critical_actions']:
        if not assessment['pricing_actions'] and not assessment['coverage_actions']:
            # Truly adequate
            pass
        else:
            # Has minor recommendations
            assessment['status'] = 'MARGINAL'

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


def render_risk_assessment_panel(metrics, premium_calc, config_obj, assessment=None, sample_size=None):
    """Display pricing adequacy status with improved categorization of recommendations."""
    if assessment is None:
        assessment = assess_pricing_adequacy(
            metrics,
            premium_calc,
            config_obj.total_tiv,
            build_airline_policy_config(config_obj),
            sample_size=sample_size,
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

    # Display simulation confidence
    if assessment.get('simulation_confidence'):
        st.caption(f"**Simulation confidence:** {assessment['simulation_confidence']}")

    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
    with summary_col1:
        st.metric("Loss Ratio", f"{assessment['loss_ratio']:.1f}%", help="Target: 60–70%")
    with summary_col2:
        st.metric("Tail Risk (TVaR/AAL)", f"{assessment['tail_risk_ratio']:.2f}x", help="Target: ≤5.0x (Adequate), 5-20x (Marginal - typical for parametric), >20x (Inadequate)")
    with summary_col3:
        st.metric("Limit vs 100-yr OEP", f"{assessment['limit_coverage_pct']:.0f}%", help="Target: ≥120%")
    with summary_col4:
        st.metric("Agg vs 200-yr AEP", f"{assessment['aggregate_coverage_pct']:.0f}%", help="Target: ≥120%")

    # Display recommendations by category
    if assessment.get('critical_actions'):
        st.markdown("**🔴 Critical Actions (Required Before Booking):**")
        for action in assessment['critical_actions']:
            st.error(action)

    if assessment.get('pricing_actions'):
        st.markdown("**💰 Pricing Adjustments:**")
        for action in assessment['pricing_actions']:
            st.info(action)

    if assessment.get('coverage_actions'):
        st.markdown("**📊 Coverage Structure Adjustments:**")
        for action in assessment['coverage_actions']:
            st.info(action)

    if assessment.get('risk_transfer_actions'):
        st.markdown("**🔄 Risk Transfer / Reinsurance:**")
        for action in assessment['risk_transfer_actions']:
            st.info(action)

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
            sample_size=results.get('sample_size', None),  # Pass simulation size for confidence reporting
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
            
            # Use exact catalog at 100K, sampling for smaller sizes (matches per-airline logic)
            use_sampling_bulk = (bulk_sample_size < 100000)
            
            event_results, annual_results, metrics, premium_calc = run_stochastic_pricing_with_config(
                enriched_sov,
                stochastic_catalogue,
                policy,
                use_fast=use_sampling_bulk,  # False for 100K (exact), True for <100K (sampled)
                sample_size=bulk_sample_size
            )
            
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
                    sample_size=results.get('sample_size', None),  # Pass simulation size for confidence
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
