"""
Live Monitor Tab - V3.6 Multi-Airline with NOAA Real-Time & Auto-Calculation
Real-time PFU monitoring with AUTOMATIC claim calculation for all booked airlines
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
import noaa_api


def calculate_airline_claim(cfg, manager, event_pfu, event_duration):
    """Calculate claim for a single airline"""
    try:
        # Get airline SOV
        airline_sov = manager.get_airline_sov(cfg.airline_id)
        enriched_sov = pe.enrich_sov_route_exposure(airline_sov)
        
        # Create live event using the factory function, consistent with live_monitor_v36.py
        # This resolves the "unexpected keyword argument 'start_timestamp_utc'" error
        # by avoiding direct instantiation of LivePFUEvent.
        event_id = f"LIVE_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"
        # Assume the event ended 'now' for calculation purposes
        event_start = datetime.utcnow() - timedelta(hours=event_duration)
        # Ensure at least one period for the date_range, even if duration is < 1 hour
        num_periods = int(event_duration) or 1
        pfu_readings = pd.DataFrame({
            'timestamp_utc': pd.to_datetime(pd.date_range(start=event_start, periods=num_periods, freq='h')),
            'pfu': [event_pfu] * num_periods
        })
        live_event = pe.evaluate_live_pfu_event(pfu_readings, event_id=event_id)

        # Create policy
        policy = pe.PolicyTerms(
            per_event_limit=cfg.per_event_limit,
            event_deductible=cfg.event_deductible,
            annual_aggregate=cfg.annual_aggregate
        )
        
        # For live claims, use all routes as scheduled flights (demo)
        scheduled_flights = enriched_sov[['flight_id', 'origin', 'destination']].copy()
        scheduled_flights['scheduled_departure_utc'] = datetime.utcnow().isoformat()
        scheduled_flights['flight_status'] = 'SCHEDULED'
        scheduled_flights['airline_id'] = cfg.airline_id
        
        # Calculate claim
        result = pe.calculate_live_claim_from_scheduled_flights(
            live_event,
            scheduled_flights,
            enriched_sov,
            policy
        )
        
        return {
            'airline_id': cfg.airline_id,
            'airline_name': cfg.airline_name,
            'status': 'SUCCESS',
            'gross_loss': result.gross_event_loss,
            'after_deductible': result.after_deductible,
            'final_payout': result.final_event_payout,
            'eligible_flights': result.eligible_flight_count,
            'flight_results': result.flight_results,
            'timestamp': datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            'airline_id': cfg.airline_id,
            'airline_name': cfg.airline_name,
            'status': 'ERROR',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        }


def calculate_all_booked_claims(booked_airlines, manager, event_pfu, event_duration):
    """Calculate claims for ALL booked airlines automatically"""
    results = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, cfg in enumerate(booked_airlines):
        status_text.text(f"Calculating {cfg.airline_name} ({idx + 1}/{len(booked_airlines)})...")
        result = calculate_airline_claim(cfg, manager, event_pfu, event_duration)
        results.append(result)
        progress_bar.progress((idx + 1) / len(booked_airlines))
    
    progress_bar.empty()
    status_text.empty()
    
    return results


def render():
    """Render live monitoring interface with NOAA integration and AUTO-CALCULATION"""
    
    st.title("🚨 Live Monitor - Real-Time Claims")
    st.markdown("Real-time PFU monitoring with **automatic claim calculation** for all booked airlines")
    
    # Check for airline manager
    if 'airline_manager' not in st.session_state:
        st.warning("⚠️ No airlines loaded. Please upload SOV in Airline Management tab.")
        return
    
    manager = st.session_state.airline_manager
    
    # Get booked airlines only
    booked_airlines = manager.get_airlines_by_status('booked')
    
    if len(booked_airlines) == 0:
        st.info("ℹ️ No airlines booked yet. Book airlines in Stochastic Pricing tab to monitor them here.")
        
        # Show unbooked airlines
        priced_airlines = manager.get_airlines_by_status('priced')
        if len(priced_airlines) > 0:
            st.markdown("#### 📋 Available Airlines (Not Yet Booked)")
            for cfg in priced_airlines:
                st.write(f"- {cfg.airline_name} ({cfg.airline_id}) - AAL: ${cfg.aal:,.0f}, Premium: ${cfg.gross_premium:,.0f}")
            st.info("💡 Go to Stochastic Pricing tab to book airlines for live monitoring")
        
        return
    
    st.markdown("---")
    
    # NOAA Real-Time PFU Monitor
    st.subheader("📡 NOAA Real-Time PFU Monitor")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        st.markdown("**Live PFU Feed from NOAA SWPC**")
    
    with col2:
        auto_refresh = st.checkbox("Auto-Refresh (30s)", value=False)
    
    with col3:
        if st.button("🔄 Refresh Now", use_container_width=True):
            st.session_state.force_refresh_noaa = True
    
    # Fetch NOAA data
    triggered = False
    peak_pfu = 0
    event_duration = 0
    
    try:
        # Use alert window for real-time monitoring
        config = st.session_state.config_v36
        alert_hours = config['monitoring_windows']['alert_window_hours']
        settlement_hours = config['monitoring_windows']['settlement_window_hours']
        
        noaa_df = noaa_api.fetch_noaa_pfu_data(hours=alert_hours)
        
        if noaa_df is None:
            st.warning("⚠️ NOAA API unavailable. Using demo data.")
            noaa_df = noaa_api.generate_demo_pfu_data(scenario="baseline")
        
        # Current PFU
        current_pfu = noaa_df.iloc[-1]['pfu']
        current_time = noaa_df.iloc[-1]['timestamp_utc']
        
        # Peak PFU in last 6 hours
        peak_pfu = noaa_df['pfu'].max()
        peak_time = noaa_df.loc[noaa_df['pfu'].idxmax(), 'timestamp_utc']
        
        # Trigger check
        config = st.session_state.config_v36
        trigger_threshold = config['other_params']['pfu_trigger']
        
        triggered = peak_pfu >= trigger_threshold
        
        # Calculate settlement window metrics for final tier determination
        settlement_df = noaa_api.fetch_noaa_pfu_data(hours=settlement_hours)
        settlement_peak_pfu = settlement_df['pfu'].max() if settlement_df is not None else peak_pfu
        
        # Display current status
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Current PFU", f"{current_pfu:.1f}", delta=None)
        
        with col2:
            st.metric(f"Peak PFU ({alert_hours}h)", f"{peak_pfu:.1f}", 
                     delta=f"+{peak_pfu - current_pfu:.1f}" if peak_pfu > current_pfu else "0")
        
        with col3:
            st.metric("Trigger Threshold", f"{trigger_threshold:.0f}")
        
        with col4:
            if triggered:
                st.error("⚠️ TRIGGERED")
            else:
                st.success("✅ Below Threshold")
        
        # PFU chart
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=noaa_df['timestamp_utc'],
            y=noaa_df['pfu'],
            mode='lines',
            name='PFU',
            line=dict(color='blue', width=2)
        ))
        
        # Add threshold line
        fig.add_hline(
            y=trigger_threshold,
            line_dash="dash",
            line_color="red",
            annotation_text=f"Trigger: {trigger_threshold}",
            annotation_position="right"
        )
        
        fig.update_layout(
            title="NOAA Real-Time PFU (Last 6 Hours)",
            xaxis_title="Time (UTC)",
            yaxis_title="PFU (>= 10 MeV)",
            yaxis_type="log",
            height=400,
            hovermode='x unified'
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Event summary if triggered
        if triggered:
            st.markdown("---")
            st.subheader("⚡ Active Event Detected")
            
            event_summary = noaa_api.get_pfu_event_summary(threshold=trigger_threshold, hours=24)
            
            if event_summary:
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric("Event ID", event_summary['event_id'])
                with col2:
                    st.metric("Peak PFU", f"{event_summary['peak_pfu']:.1f}")
                with col3:
                    event_duration = event_summary['duration_hours']
                    st.metric("Duration", f"{event_duration:.1f}h")
                with col4:
                    st.metric("Severity", event_summary['severity_tier'])
                
                # Store event for auto-calculation
                st.session_state.active_noaa_event = {
                    'event_id': event_summary['event_id'],
                    'peak_pfu': event_summary['peak_pfu'],
                    'duration_hours': event_duration,
                    'event_start': event_summary['event_start']
                }
                
                # AUTO-CALCULATE ALL CLAIMS
                st.markdown("---")
                st.subheader("💰 Auto-Calculated Portfolio Loss")
                
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.success("✅ **AUTOMATIC CLAIM CALCULATION ACTIVE**")
                    st.info(f"Calculating claims for **{len(booked_airlines)} booked airlines** using PFU={peak_pfu:.1f}, Duration={event_duration:.1f}h")
                
                with col2:
                    if st.button("🔄 Recalculate All", use_container_width=True):
                        st.session_state.force_recalc_all = True
                
                # Check if we need to calculate or use cached
                event_key = f"{event_summary['event_id']}_{peak_pfu:.1f}_{event_duration:.1f}"
                
                if 'live_claim_results' not in st.session_state:
                    st.session_state.live_claim_results = {}
                
                if event_key not in st.session_state.live_claim_results or st.session_state.get('force_recalc_all', False):
                    with st.spinner("🔄 Calculating claims for all booked airlines..."):
                        results = calculate_all_booked_claims(booked_airlines, manager, peak_pfu, event_duration)
                        st.session_state.live_claim_results[event_key] = {
                            'results': results,
                            'timestamp': datetime.utcnow().isoformat(),
                            'event_pfu': peak_pfu,
                            'event_duration': event_duration
                        }
                        st.session_state.force_recalc_all = False
                
                # Get cached results
                cached = st.session_state.live_claim_results[event_key]
                results = cached['results']
                
                # PORTFOLIO AGGREGATE VIEW
                st.markdown("#### 📊 Portfolio Aggregate")
                
                total_gross_loss = sum(r.get('gross_loss', 0) for r in results if r['status'] == 'SUCCESS')
                total_after_ded = sum(r.get('after_deductible', 0) for r in results if r['status'] == 'SUCCESS')
                total_payout = sum(r.get('final_payout', 0) for r in results if r['status'] == 'SUCCESS')
                total_eligible_flights = sum(r.get('eligible_flights', 0) for r in results if r['status'] == 'SUCCESS')
                
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric("Portfolio Gross Loss", f"${total_gross_loss:,.0f}")
                with col2:
                    st.metric("After Deductibles", f"${total_after_ded:,.0f}")
                with col3:
                    st.metric("Total Portfolio Payout", f"${total_payout:,.0f}")
                with col4:
                    st.metric("Eligible Flights", f"{total_eligible_flights:,}")
                
                st.caption(f"Last calculated: {cached['timestamp'][:19]}")
    
    except Exception as e:
        st.error(f"Failed to fetch NOAA data: {e}")
        noaa_df = None
    
    st.markdown("---")
    
    # PER-AIRLINE BREAKDOWN
    st.subheader(f"📋 Per-Airline Breakdown ({len(booked_airlines)} Airlines)")
    
    # Check if we have auto-calculated results
    has_auto_results = (triggered and 
                       'live_claim_results' in st.session_state and 
                       st.session_state.live_claim_results)
    
    if has_auto_results:
        # Get latest results
        latest_key = list(st.session_state.live_claim_results.keys())[-1]
        results = st.session_state.live_claim_results[latest_key]['results']
        results_dict = {r['airline_id']: r for r in results}
    else:
        results_dict = {}
    
    # Display each airline
    for cfg in booked_airlines:
        # Check if we have auto-calculated results for this airline
        airline_result = results_dict.get(cfg.airline_id)
        
        if airline_result and airline_result['status'] == 'SUCCESS':
            # Show pre-calculated results
            payout_display = f"💰 ${airline_result['final_payout']:,.0f}"
            expanded_state = False
        else:
            payout_display = "Calculate manually"
            expanded_state = False
        
        with st.expander(
            f"✈️ {cfg.airline_name} ({cfg.airline_id}) - {payout_display}", 
            expanded=expanded_state
        ):
            
            col1, col2, col3, col4, col5 = st.columns(5)
            
            with col1:
                st.metric("Routes", cfg.route_count)
            with col2:
                st.metric("TIV", f"${cfg.total_tiv:,.0f}")
            with col3:
                st.metric("AAL", f"${cfg.aal:,.0f}" if cfg.aal else "N/A")
            with col4:
                st.metric("Premium", f"${cfg.gross_premium:,.0f}" if cfg.gross_premium else "N/A")
            with col5:
                st.metric("Per-Event Limit", f"${cfg.per_event_limit:,.0f}")
            
            # Show auto-calculated results if available
            if airline_result and airline_result['status'] == 'SUCCESS':
                st.markdown("##### 💰 Auto-Calculated Claim Results")
                
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric("Gross Loss", f"${airline_result['gross_loss']:,.0f}")
                with col2:
                    st.metric("After Deductible", f"${airline_result['after_deductible']:,.0f}")
                with col3:
                    st.metric("Final Payout", f"${airline_result['final_payout']:,.0f}")
                with col4:
                    st.metric("Eligible Flights", airline_result['eligible_flights'])
                
                # Flight-level breakdown
                with st.expander("📊 Flight-Level Breakdown"):
                    flight_results_df = pd.DataFrame([
                        {
                            'Flight': fr.flight_id,
                            'Route': f"{fr.origin} → {fr.destination}",
                            'Base Cost': fr.base_cost,
                            'Tier': fr.severity_tier,
                            'Flight Loss': fr.flight_loss,
                            'Status': fr.calculation_notes
                        }
                        for fr in airline_result['flight_results']
                    ])
                    
                    st.dataframe(
                        flight_results_df.style.format({
                            'Base Cost': '${:,.0f}',
                            'Flight Loss': '${:,.0f}'
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
            
            else:
                # Manual claim calculation
                st.markdown("##### Manual Claim Calculation")
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    manual_pfu = st.number_input(
                        "Event PFU",
                        min_value=0.0,
                        value=float(peak_pfu) if peak_pfu > 0 else 1500.0,
                        step=100.0,
                        key=f"pfu_{cfg.airline_id}"
                    )
                
                with col2:
                    manual_duration = st.number_input(
                        "Duration (hours)",
                        min_value=0.0,
                        value=float(event_duration) if event_duration > 0 else 12.0,
                        step=1.0,
                        key=f"duration_{cfg.airline_id}"
                    )
                
                with col3:
                    if st.button("💰 Calculate Claim", key=f"calc_{cfg.airline_id}", use_container_width=True):
                        st.session_state[f'calc_claim_{cfg.airline_id}'] = True
                
                # Calculate claim if requested
                if st.session_state.get(f'calc_claim_{cfg.airline_id}', False):
                    
                    with st.spinner(f"Calculating claim for {cfg.airline_name}..."):
                        result = calculate_airline_claim(cfg, manager, manual_pfu, manual_duration)
                        
                        if result['status'] == 'SUCCESS':
                            # Display results
                            st.markdown("##### 💰 Claim Results")
                            
                            col1, col2, col3, col4 = st.columns(4)
                            
                            with col1:
                                st.metric("Gross Loss", f"${result['gross_loss']:,.0f}")
                            with col2:
                                st.metric("After Deductible", f"${result['after_deductible']:,.0f}")
                            with col3:
                                st.metric("Final Payout", f"${result['final_payout']:,.0f}")
                            with col4:
                                st.metric("Eligible Flights", result['eligible_flights'])
                            
                            # Flight-level breakdown
                            with st.expander("📊 Flight-Level Breakdown"):
                                flight_results_df = pd.DataFrame([
                                    {
                                        'Flight': fr.flight_id,
                                        'Route': f"{fr.origin} → {fr.destination}",
                                        'Base Cost': fr.base_cost,
                                        'Tier': fr.severity_tier,
                                        'Flight Loss': fr.flight_loss,
                                        'Status': fr.calculation_notes
                                    }
                                    for fr in result['flight_results']
                                ])
                                
                                st.dataframe(
                                    flight_results_df.style.format({
                                        'Base Cost': '${:,.0f}',
                                        'Flight Loss': '${:,.0f}'
                                    }),
                                    use_container_width=True,
                                    hide_index=True
                                )
                        else:
                            st.error(f"Calculation failed: {result.get('error', 'Unknown error')}")
                    
                    st.session_state[f'calc_claim_{cfg.airline_id}'] = False
            
            # Unbook button
            col1, col2 = st.columns([1, 2])
            with col1:
                if st.button(f"❌ Unbook", key=f"unbook_{cfg.airline_id}", use_container_width=True):
                    if manager.unbook_airline(cfg.airline_id):
                        st.success(f"✅ {cfg.airline_name} unbooked")
                        st.rerun()
            
            with col2:
                st.info("Unbooking returns airline to Stochastic Pricing for re-pricing")
    
    # Auto-refresh logic
    if auto_refresh:
        import time
        time.sleep(30)
        st.rerun()
