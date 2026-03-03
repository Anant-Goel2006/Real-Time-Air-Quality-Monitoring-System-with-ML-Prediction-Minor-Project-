# 🌍 Real-Time Air Quality Monitoring System with ML Prediction

A web-based intelligent system that monitors real-time air quality, predicts Air Quality Index (AQI) using Machine Learning, and visualizes pollution data through interactive dashboards and maps.  
Built using **Flask, Python, Machine Learning, and modern frontend technologies**.

---

## 📌 Project Overview

Air pollution is a major environmental and public health concern worldwide. This project provides a unified platform to:
- Monitor real-time air quality parameters
- Predict AQI values using a trained Machine Learning model
- Visualize pollution trends and geographic distribution
- Provide health recommendations based on AQI levels

The system is designed as a **final year engineering project** and follows industry-standard development practices.

---

## 🚀 Features

- 🌡️ **Live Air Quality Dashboard** with EPA-based AQI categories  
- 🤖 **Machine Learning AQI Prediction** using Random Forest  
- 📊 **Interactive Charts** (AQI trends, pollutant comparison)  
- 🗺️ **Geographic Visualization** using interactive maps  
- 🏥 **Health Recommendations** based on pollution levels  
- 📁 **Data Export** to CSV format  
- 🔄 **Auto Refresh** every 5 minutes  
- 📱 **Responsive UI** (mobile, tablet, desktop)  
- 🔌 **RESTful APIs** for easy integration  

---

## 🛠️ Technology Stack

### Backend
- Python
- Flask
- Pandas
- NumPy
- Scikit-learn
- Joblib

### Frontend
- HTML5
- CSS3
- JavaScript (ES6)
- Chart.js
- Leaflet.js

### Machine Learning
- Random Forest Regressor
- Feature Engineering (time-based & interaction features)

---

## 📂 Project Structure

```text
Air project/
├── app (1).py                  # Flask backend (live AQI + APIs)
├── requirements.txt
├── README.md
├── ML-Model/                   # Trained ML artifacts
│   ├── aqi_model_random_forest.pkl
│   ├── aqi_scaler.pkl
│   ├── aqi_encoders.pkl
│   └── air_quality_model_training.ipynb
├── Sample_Dataset/             # Dataset used by CSV-backed APIs
│   └── globalAirQuality.csv
├── templates/                  # HTML pages
│   ├── index.html
│   ├── analytics.html
│   └── predict.html
└── static/
    ├── css/style.css
    ├── js/main.js
    ├── js/analytics.js
    ├── js/predict.js
    └── assets/hero/
```

---

## ▶️ Run Locally

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the server:
   ```bash
   python "app (1).py"
   ```
3. Open:
   - `http://127.0.0.1:8080/`
   - `http://127.0.0.1:8080/analytics`
   - `http://127.0.0.1:8080/predict`

