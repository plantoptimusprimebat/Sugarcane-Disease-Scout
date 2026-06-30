import streamlit as st
import requests
import pandas as pd
import json
import os
import re
from datetime import datetime, date
from io import BytesIO
from PIL import Image
from dotenv import load_dotenv

# Try importing streamlit-js-eval for GPS
try:
    from streamlit_js_eval import streamlit_js_eval
    JS_EVAL_AVAILABLE = True
except ImportError:
    JS_EVAL_AVAILABLE = False

load_dotenv()

# --- Configuration ---
PLANTNET_API_KEY = os.getenv("PLANTNET_API_KEY", st.secrets.get("PLANTNET_API_KEY", ""))
VISUALCROSSING_API_KEY = os.getenv("VISUALCROSSING_API_KEY", st.secrets.get("VISUALCROSSING_API_KEY", ""))
DATA_DIR = "data"
SUBMISSIONS_FILE = os.path.join(DATA_DIR, "submissions.csv")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)


# --- Helper Functions ---

def extract_coords_from_google_link(link: str):
    """Extract latitude and longitude from a Google Maps link."""
    # Pattern 1: https://maps.google.com/?q=lat,lon
    match = re.search(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)", link)
    if match:
        return float(match.group(1)), float(match.group(2))

    # Pattern 2: https://www.google.com/maps/place/.../@lat,lon,zoom
    match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", link)
    if match:
        return float(match.group(1)), float(match.group(2))

    # Pattern 3: https://maps.app.goo.gl/ short links - try to follow redirect
    # Pattern 4: https://goo.gl/maps/...
    # Pattern 5: Simple lat,lon format
    match = re.search(r"(-?\d+\.\d+),\s*(-?\d+\.\d+)", link)
    if match:
        return float(match.group(1)), float(match.group(2))

    return None, None


def identify_disease(image_bytes: bytes, organ: str = "leaf"):
    """Send image to PlantNet Diseases & Pests API for identification."""
    url = "https://my-api.plantnet.org/v2/diseases/identify"
    
    params = {
        "api-key": PLANTNET_API_KEY,
        "nb-results": 5,
        "lang": "en",
    }

    files = {
        "images": ("image.jpg", image_bytes, "image/jpeg"),
    }

    data = {
        "organs": organ,
    }

    try:
        response = requests.post(url, params=params, files=files, data=data, timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"PlantNet API error: {response.status_code} - {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        st.error(f"Connection error: {e}")
        return None


def get_weather_data(lat: float, lon: float, date_str: str):
    """Fetch weather data from Visual Crossing for a specific date and location."""
    url = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"
        f"{lat},{lon}/{date_str}/{date_str}"
    )

    params = {
        "unitGroup": "metric",
        "key": VISUALCROSSING_API_KEY,
        "contentType": "json",
        "include": "days",
        "elements": (
            "datetime,tempmax,tempmin,temp,humidity,precip,precipprob,"
            "windspeed,windgust,winddir,pressure,cloudcover,uvindex,"
            "dew,visibility,conditions,description"
        ),
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if "days" in data and len(data["days"]) > 0:
                return data["days"][0]
        else:
            st.warning(f"Weather API error: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        st.warning(f"Could not fetch weather data: {e}")
        return None


def save_submission(record: dict):
    """Append a submission record to the CSV file."""
    df_new = pd.DataFrame([record])

    if os.path.exists(SUBMISSIONS_FILE):
        df_existing = pd.read_csv(SUBMISSIONS_FILE)
        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_combined = df_new

    df_combined.to_csv(SUBMISSIONS_FILE, index=False)


def load_submissions():
    """Load all submission records."""
    if os.path.exists(SUBMISSIONS_FILE):
        return pd.read_csv(SUBMISSIONS_FILE)
    return pd.DataFrame()


# --- Streamlit App ---

st.set_page_config(
    page_title="Sugarcane Disease Scout",
    page_icon="🌾",
    layout="centered",
)

st.title("🌾 Sugarcane Disease Scout")
st.markdown("Upload or take a photo of a sugarcane leaf to identify diseases.")

# --- Sidebar: Admin/Analytics ---
with st.sidebar:
    st.header("📊 Analytics")

    admin_password = st.text_input("Admin password", type="password")

    if admin_password == os.getenv("ADMIN_PASSWORD", "admin123"):
        df = load_submissions()
        if not df.empty:
            st.success(f"{len(df)} submissions recorded")
            st.dataframe(df.tail(10), use_container_width=True)

            csv_data = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="⬇️ Download CSV",
                data=csv_data,
                file_name=f"disease_submissions_{date.today().isoformat()}.csv",
                mime="text/csv",
            )
        else:
            st.info("No submissions yet.")
    elif admin_password:
        st.error("Incorrect password")

# --- Main Section ---

# Step 1: Image Upload
st.header("📸 Step 1: Capture or Upload Image")

upload_method = st.radio(
    "How would you like to provide the image?",
    ["📷 Take a photo", "📁 Upload from device"],
    horizontal=True,
)

image_bytes = None

if upload_method == "📷 Take a photo":
    camera_image = st.camera_input("Take a photo of the affected sugarcane")
    if camera_image:
        image_bytes = camera_image.getvalue()
else:
    uploaded_file = st.file_uploader(
        "Upload an image", type=["jpg", "jpeg", "png"], accept_multiple_files=False
    )
    if uploaded_file:
        image_bytes = uploaded_file.getvalue()

if image_bytes:
    st.image(image_bytes, caption="Submitted image", use_column_width=True)

# Step 2: Organ selection (like PlantNet's interface)
st.header("🌿 Step 2: What part of the plant is shown?")
organ = st.selectbox(
    "Select the plant organ visible in the image:",
    options=["leaf", "fruit", "flower", "bark"],
    index=0,
    help="This helps the AI narrow down the identification. For sugarcane diseases, 'leaf' is most common.",
)

# Step 3: Location
st.header("📍 Step 3: Location")

location_method = st.radio(
    "How should we get your location?",
    ["🛰️ Auto-detect (GPS)", "📋 Paste Google Maps link", "✏️ Enter coordinates manually"],
    horizontal=False,
)

lat, lon = None, None

if location_method == "🛰️ Auto-detect (GPS)":
    if JS_EVAL_AVAILABLE:
        st.info("Click the button below to share your location.")

        if st.button("📍 Get My Location"):
            # Request GPS from browser
            location_data = streamlit_js_eval(
                js_expressions="""
                new Promise((resolve, reject) => {
                    navigator.geolocation.getCurrentPosition(
                        (pos) => resolve({lat: pos.coords.latitude, lon: pos.coords.longitude}),
                        (err) => resolve({error: err.message}),
                        {enableHighAccuracy: true, timeout: 10000}
                    );
                })
                """,
                key="gps_location",
            )

            if location_data and "lat" in location_data:
                lat = location_data["lat"]
                lon = location_data["lon"]
                st.success(f"Location captured: {lat:.6f}, {lon:.6f}")
            elif location_data and "error" in location_data:
                st.error(f"GPS error: {location_data['error']}. Try pasting a Google Maps link instead.")
    else:
        st.warning(
            "GPS auto-detect requires the `streamlit-js-eval` package. "
            "Please paste a Google Maps link or enter coordinates manually."
        )

elif location_method == "📋 Paste Google Maps link":
    maps_link = st.text_input(
        "Paste your Google Maps pin/link here:",
        placeholder="https://maps.google.com/?q=-29.1234,31.5678",
    )
    if maps_link:
        lat, lon = extract_coords_from_google_link(maps_link)
        if lat and lon:
            st.success(f"Coordinates extracted: {lat:.6f}, {lon:.6f}")
        else:
            st.error("Could not extract coordinates from that link. Try pasting the raw lat,lon values.")

elif location_method == "✏️ Enter coordinates manually":
    col1, col2 = st.columns(2)
    with col1:
        lat = st.number_input("Latitude", value=-29.0, format="%.6f", min_value=-90.0, max_value=90.0)
    with col2:
        lon = st.number_input("Longitude", value=31.0, format="%.6f", min_value=-180.0, max_value=180.0)

# Show location on map if available
if lat and lon:
    st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}), zoom=10)

# Step 4: Submit
st.header("🔍 Step 4: Identify Disease")

if st.button("🚀 Identify Disease", type="primary", disabled=(image_bytes is None)):
    if not PLANTNET_API_KEY:
        st.error("PlantNet API key not configured. Set PLANTNET_API_KEY in .env or Streamlit secrets.")
    elif not image_bytes:
        st.error("Please upload or take a photo first.")
    elif not lat or not lon:
        st.error("Please provide a location.")
    else:
        with st.spinner("Identifying disease with PlantNet AI..."):
            results = identify_disease(image_bytes, organ)

        if results and "results" in results:
            st.subheader("🦠 Disease Identification Results")

            top_disease = None
            top_score = 0
            
            for i, result in enumerate(results["results"][:5]):
                score = result.get("score", 0)
                eppo_code = result.get("name", "Unknown")
                description = result.get("description", "N/A")

                if i == 0:
                    top_disease = description
                    top_score = score

                # Display with progress bar
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"**{description}**")
                with col2:
                    st.markdown(f"**{score*100:.1f}%**")
                st.progress(score)

            # Fetch weather data
            weather = None
            submission_date = date.today().isoformat()

            if VISUALCROSSING_API_KEY:
                with st.spinner("Fetching weather data for location..."):
                    weather = get_weather_data(lat, lon, submission_date)

                if weather:
                    st.subheader("🌤️ Weather Conditions")
                    wcol1, wcol2, wcol3, wcol4 = st.columns(4)
                    with wcol1:
                        st.metric("Temperature", f"{weather.get('temp', 'N/A')}°C")
                    with wcol2:
                        st.metric("Humidity", f"{weather.get('humidity', 'N/A')}%")
                    with wcol3:
                        st.metric("Precipitation", f"{weather.get('precip', 0)} mm")
                    with wcol4:
                        st.metric("Wind Speed", f"{weather.get('windspeed', 'N/A')} km/h")

            # Save submission record
            record = {
                "submission_datetime": datetime.now().isoformat(),
                "submission_date": submission_date,
                "latitude": lat,
                "longitude": lon,
                "organ": organ,
                "disease_identified": top_disease,
                "confidence_score": round(top_score, 4),
                "all_results_json": json.dumps(
                    [
                        {
                            "eppo_code": r.get("name", ""),
                            "description": r.get("description", ""),
                            "score": round(r.get("score", 0), 4),
                        }
                        for r in results["results"][:5]
                    ]
                ),
                # Weather fields
                "temp_c": weather.get("temp") if weather else None,
                "temp_max_c": weather.get("tempmax") if weather else None,
                "temp_min_c": weather.get("tempmin") if weather else None,
                "humidity_pct": weather.get("humidity") if weather else None,
                "precip_mm": weather.get("precip") if weather else None,
                "precip_prob_pct": weather.get("precipprob") if weather else None,
                "wind_speed_kmh": weather.get("windspeed") if weather else None,
                "wind_gust_kmh": weather.get("windgust") if weather else None,
                "wind_dir_deg": weather.get("winddir") if weather else None,
                "pressure_hpa": weather.get("pressure") if weather else None,
                "cloud_cover_pct": weather.get("cloudcover") if weather else None,
                "uv_index": weather.get("uvindex") if weather else None,
                "dew_point_c": weather.get("dew") if weather else None,
                "visibility_km": weather.get("visibility") if weather else None,
                "conditions": weather.get("conditions") if weather else None,
            }

            save_submission(record)
            st.success("✅ Submission saved to analytics database!")

        elif results and "results" not in results:
            st.warning("PlantNet could not identify a disease from this image. Try a clearer photo or different angle.")
        else:
            st.error("Identification failed. Please try again.")

# Footer
st.markdown("---")
st.caption(
    "Powered by [PlantNet API](https://my.plantnet.org/) & "
    "[Visual Crossing Weather](https://www.visualcrossing.com/) | "
    "Built for sugarcane disease monitoring in South Africa"
)
