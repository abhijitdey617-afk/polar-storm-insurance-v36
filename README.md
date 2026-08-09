# Polar Storm Insurance V3.6

Streamlit dashboard for multi-airline polar storm insurance pricing and live NOAA monitoring.

## Run locally

```bash
pip install -r requirements.txt
streamlit run streamlit_dashboard/app_v36.py
```

## Streamlit Community Cloud deployment

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, select the repository and its default branch.
3. Set the main file path to `streamlit_dashboard/app_v36.py`.
4. Deploy with Python dependencies from `requirements.txt`.

The repository includes:

- All active V3.6 dashboard scripts in `streamlit_dashboard\` and `streamlit_dashboard\tabs\`.
- The complete `data\` directory, including stochastic and historical event data.
- The root-level `airport_master.csv` file.
- Root-level V3.6 engines required by the dashboard.

The Live Monitor tab retrieves public NOAA SWPC data at runtime and does not require an API key.