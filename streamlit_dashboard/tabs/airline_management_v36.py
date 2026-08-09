"""
Airline Management Tab - V3.6 Multi-Airline
Upload and configure multi-airline SOV with per-airline limits/deductibles
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

import airline_manager as am

def render():
    """Render airline management interface"""
    
    st.title("🏢 Airline Portfolio Management")
    st.markdown("Upload multi-airline SOV and configure per-airline policy terms")
    
    # Initialize airline manager in session state if not exists
    if 'airline_manager' not in st.session_state:
        config = st.session_state.config_v36
        st.session_state.airline_manager = am.AirlinePortfolioManager(
            default_per_event_limit=config['policy_terms']['per_event_limit'],
            default_event_deductible=config['policy_terms']['event_deductible'],
            default_annual_aggregate=config['policy_terms']['annual_aggregate']
        )
    
    manager = st.session_state.airline_manager
    
    st.markdown("---")
    
    # SOV Upload Section
    st.subheader("📂 Upload Multi-Airline SOV")
    
    # Show SOV format requirements
    with st.expander("📋 Required SOV Format", expanded=False):
        st.markdown("""
        **REQUIRED Columns:**
        - `airline_id` - Airline identifier (e.g., "FIN", "SAS", "LUFTHANSA")
        - `flight_id` - Unique flight identifier
        - `origin` - Origin airport IATA/ICAO code
        - `destination` - Destination airport IATA/ICAO code
        - `reroute_cost_usd` - Base reroute cost per flight
        
        **OPTIONAL Columns (per-airline policy terms):**
        - `per_event_limit` - Per-event limit for this airline
        - `event_deductible` - Event deductible for this airline
        - `annual_aggregate` - Annual aggregate limit for this airline
        
        **OPTIONAL Columns (route exposure):**
        - `airline_name` - Display name for airline
        - `duration_hours`, `polar_hours`, `route_exposure`
        - `origin_latitude`, `origin_longitude`, `destination_latitude`, `destination_longitude`
        
        **Notes:**
        - If per-airline limits are not specified, defaults from Configuration tab are used
        - All routes with same `airline_id` will be grouped together
        - Each airline can have different policy terms
        """)
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        uploaded_sov = st.file_uploader(
            "Upload Multi-Airline SOV CSV",
            type=['csv'],
            help="Must include airline_id column"
        )
    
    with col2:
        if st.button("📥 Load Demo SOV", use_container_width=True):
            # Create demo multi-airline SOV
            demo_data = []
            
            # Finnair routes
            finnair_routes = [
                {"airline_id": "FIN", "airline_name": "Finnair", "flight_id": "AY105", "origin": "JFK", "destination": "HEL", "reroute_cost_usd": 18000, "per_event_limit": 500000, "event_deductible": 10000, "annual_aggregate": 2000000},
                {"airline_id": "FIN", "airline_name": "Finnair", "flight_id": "AY107", "origin": "ORD", "destination": "HEL", "reroute_cost_usd": 22000, "per_event_limit": 500000, "event_deductible": 10000, "annual_aggregate": 2000000},
                {"airline_id": "FIN", "airline_name": "Finnair", "flight_id": "AY091", "origin": "SFO", "destination": "HEL", "reroute_cost_usd": 35000, "per_event_limit": 500000, "event_deductible": 10000, "annual_aggregate": 2000000},
            ]
            
            # SAS routes
            sas_routes = [
                {"airline_id": "SAS", "airline_name": "SAS Scandinavian", "flight_id": "SK902", "origin": "EWR", "destination": "OSL", "reroute_cost_usd": 19000, "per_event_limit": 750000, "event_deductible": 15000, "annual_aggregate": 3000000},
                {"airline_id": "SAS", "airline_name": "SAS Scandinavian", "flight_id": "SK904", "origin": "LAX", "destination": "CPH", "reroute_cost_usd": 38000, "per_event_limit": 750000, "event_deductible": 15000, "annual_aggregate": 3000000},
            ]
            
            # Lufthansa routes
            lufthansa_routes = [
                {"airline_id": "LH", "airline_name": "Lufthansa", "flight_id": "LH400", "origin": "JFK", "destination": "FRA", "reroute_cost_usd": 16000, "per_event_limit": 1000000, "event_deductible": 20000, "annual_aggregate": 5000000},
                {"airline_id": "LH", "airline_name": "Lufthansa", "flight_id": "LH456", "origin": "SFO", "destination": "MUC", "reroute_cost_usd": 32000, "per_event_limit": 1000000, "event_deductible": 20000, "annual_aggregate": 5000000},
                {"airline_id": "LH", "airline_name": "Lufthansa", "flight_id": "LH490", "origin": "ORD", "destination": "FRA", "reroute_cost_usd": 20000, "per_event_limit": 1000000, "event_deductible": 20000, "annual_aggregate": 5000000},
            ]
            
            demo_data = finnair_routes + sas_routes + lufthansa_routes
            demo_sov = pd.DataFrame(demo_data)
            
            # Clear existing airline data when demo SOV is loaded
            manager.airlines.clear()
            
            # Clear old pricing results
            if 'airline_pricing_results' in st.session_state:
                del st.session_state.airline_pricing_results
            
            st.session_state.uploaded_multi_sov = demo_sov
            st.success("✅ Loaded demo multi-airline SOV (3 airlines, 8 routes)")
            st.rerun()
    
    # Process uploaded SOV
    if uploaded_sov:
        try:
            sov_df = pd.read_csv(uploaded_sov)
            st.session_state.uploaded_multi_sov = sov_df
            st.success(f"✅ Uploaded SOV with {len(sov_df)} routes")
        except Exception as e:
            st.error(f"Failed to load SOV: {e}")
            return
    
    # Parse and display airlines
    if 'uploaded_multi_sov' in st.session_state:
        sov_df = st.session_state.uploaded_multi_sov
        
        # Parse SOV
        if len(manager.airlines) == 0:  # Only parse if not already parsed
            with st.spinner("Parsing multi-airline SOV..."):
                results = manager.parse_multi_airline_sov(sov_df)
                
                if results['status'] == 'error':
                    for warning in results['warnings']:
                        st.error(warning)
                    return
                else:
                    for warning in results['warnings']:
                        st.info(warning)
        
        st.markdown("---")
        
        # Portfolio Summary
        st.subheader("📊 Portfolio Summary")
        
        portfolio = manager.get_portfolio_summary()
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Airlines", portfolio['total_airlines'])
        with col2:
            st.metric("Total Routes", portfolio['total_routes'])
        with col3:
            st.metric("Total TIV", f"${portfolio['total_tiv']:,.0f}")
        with col4:
            priced_count = portfolio['status_counts']['priced'] + portfolio['status_counts']['booked']
            st.metric("Airlines Priced", f"{priced_count}/{portfolio['total_airlines']}")
        
        # Status breakdown
        st.markdown("#### Airline Status")
        status_cols = st.columns(4)
        for i, (status, count) in enumerate(portfolio['status_counts'].items()):
            with status_cols[i]:
                st.metric(status.capitalize(), count)
        
        # Portfolio risk metrics (if any airlines priced)
        if portfolio['portfolio_aal'] > 0:
            st.markdown("#### Portfolio Risk Metrics")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Portfolio AAL", f"${portfolio['portfolio_aal']:,.0f}")
            with col2:
                st.metric("Portfolio Premium", f"${portfolio['portfolio_premium']:,.0f}")
            with col3:
                st.metric("Loss Ratio", f"{portfolio['portfolio_loss_ratio']:.1f}%")
            with col4:
                st.metric("Rate on Line", f"{portfolio['portfolio_rol']:.2f}%")
        
        st.markdown("---")
        
        # Per-Airline Management
        st.subheader("✈️ Airline Management")
        
        # Display each airline
        for airline_id, config in manager.airlines.items():
            with st.expander(f"✈️ {config.airline_name} ({airline_id}) - Status: {config.status.upper()}", expanded=True):
                
                # Airline summary
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Routes", config.route_count)
                with col2:
                    st.metric("Total TIV", f"${config.total_tiv:,.0f}")
                with col3:
                    st.metric("Avg Exposure", f"{config.avg_route_exposure:.1%}")
                with col4:
                    status_colors = {
                        'uploaded': '🔵',
                        'priced': '🟢',
                        'booked': '🟡',
                        'active': '🔴'
                    }
                    st.markdown(f"**Status**: {status_colors.get(config.status, '⚪')} {config.status.upper()}")
                
                # Editable policy terms
                st.markdown("##### Policy Terms")
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    new_per_event = st.number_input(
                        "Per-Event Limit ($)",
                        min_value=0,
                        value=int(config.per_event_limit),
                        step=50000,
                        key=f"per_event_{airline_id}"
                    )
                
                with col2:
                    new_deductible = st.number_input(
                        "Event Deductible ($)",
                        min_value=0,
                        value=int(config.event_deductible),
                        step=5000,
                        key=f"deductible_{airline_id}"
                    )
                
                with col3:
                    new_aggregate = st.number_input(
                        "Annual Aggregate ($)",
                        min_value=0,
                        value=int(config.annual_aggregate),
                        step=100000,
                        key=f"aggregate_{airline_id}"
                    )
                
                # Update button
                col1, col2, col3 = st.columns([1, 1, 2])
                
                with col1:
                    if st.button("💾 Update Terms", key=f"update_{airline_id}", use_container_width=True):
                        manager.update_airline_limits(
                            airline_id,
                            per_event_limit=float(new_per_event),
                            event_deductible=float(new_deductible),
                            annual_aggregate=float(new_aggregate)
                        )
                        st.success(f"✅ Updated terms for {config.airline_name}")
                        st.rerun()
                
                with col2:
                    if config.status == 'priced':
                        if st.button("📊 Re-Price", key=f"reprice_{airline_id}", use_container_width=True):
                            # Store reprice request
                            st.session_state.reprice_airline = airline_id
                            st.info(f"Navigate to Stochastic Pricing tab to re-price {config.airline_name}")
                
                # Risk metrics (if priced)
                if config.aal is not None:
                    st.markdown("##### Risk Metrics")
                    col1, col2, col3, col4, col5 = st.columns(5)
                    with col1:
                        st.metric("AAL", f"${config.aal:,.0f}")
                    with col2:
                        st.metric("SD", f"${config.sd:,.0f}")
                    with col3:
                        st.metric("CV", f"{config.cv:.2f}")
                    with col4:
                        st.metric("Premium", f"${config.gross_premium:,.0f}")
                    with col5:
                        st.metric("Loss Ratio", f"{config.loss_ratio:.1f}%")
                
                # SOV preview
                with st.expander("📋 View Routes"):
                    airline_sov = manager.get_airline_sov(airline_id)
                    if airline_sov is not None:
                        display_cols = ['flight_id', 'origin', 'destination', 'reroute_cost_usd']
                        if 'route_exposure' in airline_sov.columns:
                            display_cols.append('route_exposure')
                        st.dataframe(
                            airline_sov[display_cols],
                            use_container_width=True,
                            hide_index=True
                        )
        
        st.markdown("---")
        
        # Bulk actions
        st.subheader("⚙️ Bulk Actions")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("📊 Price All Airlines", type="primary", use_container_width=True):
                st.session_state.price_all_airlines = True
                st.info("Navigate to Stochastic Pricing tab to price all airlines")
        
        with col2:
            if st.button("📥 Export Configuration", use_container_width=True):
                filename = f"airline_config_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
                filepath = f"/tmp/{filename}"
                manager.export_airline_config_csv(filepath)
                
                with open(filepath, 'r') as f:
                    csv_data = f.read()
                
                st.download_button(
                    label="💾 Download CSV",
                    data=csv_data,
                    file_name=filename,
                    mime="text/csv",
                    use_container_width=True
                )
        
        with col3:
            if st.button("🔄 Reset All", use_container_width=True):
                if st.button("⚠️ Confirm Reset", use_container_width=True):
                    # Clear airline manager
                    config = st.session_state.config_v36
                    st.session_state.airline_manager = am.AirlinePortfolioManager(
                        default_per_event_limit=config['policy_terms']['per_event_limit'],
                        default_event_deductible=config['policy_terms']['event_deductible'],
                        default_annual_aggregate=config['policy_terms']['annual_aggregate']
                    )
                    if 'uploaded_multi_sov' in st.session_state:
                        del st.session_state.uploaded_multi_sov
                    st.success("✅ Reset complete")
                    st.rerun()
    
    else:
        st.info("⬆️ Upload a multi-airline SOV to get started")
        
        # Show sample SOV
        with st.expander("📄 Download Sample Multi-Airline SOV Template"):
            sample_data = {
                'airline_id': ['FIN', 'FIN', 'SAS', 'SAS'],
                'airline_name': ['Finnair', 'Finnair', 'SAS', 'SAS'],
                'flight_id': ['AY105', 'AY107', 'SK902', 'SK904'],
                'origin': ['JFK', 'ORD', 'EWR', 'LAX'],
                'destination': ['HEL', 'HEL', 'OSL', 'CPH'],
                'reroute_cost_usd': [18000, 22000, 19000, 38000],
                'per_event_limit': [500000, 500000, 750000, 750000],
                'event_deductible': [10000, 10000, 15000, 15000],
                'annual_aggregate': [2000000, 2000000, 3000000, 3000000]
            }
            
            sample_df = pd.DataFrame(sample_data)
            st.dataframe(sample_df, use_container_width=True, hide_index=True)
            
            csv = sample_df.to_csv(index=False)
            st.download_button(
                "📥 Download Template CSV",
                data=csv,
                file_name="multi_airline_sov_template.csv",
                mime="text/csv"
            )
