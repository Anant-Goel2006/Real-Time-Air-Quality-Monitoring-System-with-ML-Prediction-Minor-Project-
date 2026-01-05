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

## 📊 Dataset Information

- Covers **50 global cities**
- **15 days of hourly data**
- ~18,000 records
- Includes:
  - PM2.5, PM10, NO₂, SO₂, O₃, CO
  - Temperature, Humidity, Wind Speed
  - Latitude & Longitude for mapping

---

## 🤖 Machine Learning Model

- Algorithm: **Random Forest Regressor**
- Features:
  - Pollutant concentrations
  - Weather conditions
  - Time-based features (hour, day, weekday)
- Output:
  - Predicted Air Quality Index (AQI)

---

## 📁 Project Structure

├── app.py
├── model/
│ └── aqi_model.pkl
├── data/
│ └── air_quality_data.csv
├── templates/
│ └── index.html
├── static/
│ ├── css/
│ ├── js/
│ └── images/
├── notebooks/
│ └── model_training.ipynb
├── requirements.txt
└── README.md

yaml
Copy code

---

## ⚙️ Installation & Setup

### 1️⃣ Clone the Repository
```bash
git clone https://github.com/your-username/air-quality-monitoring-ml.git
cd air-quality-monitoring-ml
2️⃣ Create Virtual Environment (Optional)
bash
Copy code
python -m venv venv
source venv/bin/activate   # For Linux/Mac
venv\Scripts\activate      # For Windows
3️⃣ Install Dependencies
bash
Copy code
pip install -r requirements.txt
4️⃣ Run the Application
bash
Copy code
python app.py
5️⃣ Open in Browser
cpp
Copy code
http://127.0.0.1:5000
📡 API Endpoints
Endpoint	Description
/api/current	Get current AQI data
/api/predict	Predict AQI
/api/history	Historical AQI data
/api/export	Download data (CSV)

🎓 Learning Outcomes
Full-stack web development using Flask

Machine Learning model deployment

Environmental data analysis

REST API design

Data visualization techniques

Software engineering best practices

🌱 Future Enhancements
Integration with real-time IoT sensors

Deep Learning–based prediction models

Mobile application support

User authentication & alerts

Real-time government AQI APIs

🧑‍🎓 Academic Suitability
Suitable for:

B.Tech / BE

MCA

MSc (Computer Science / Data Science)

Domains:

Computer Science

Data Science

Environmental Engineering

Information Technology

📜 License
This project is for educational purposes only.
You may modify and extend it for academic and research use.

⭐ Acknowledgment
Special thanks to open-source libraries and datasets that made this project possible.

⭐ If you like this project, don’t forget to star the repository!
yaml
Copy code

---

If you want, I can also:
- Customize README with **your name, college & guide**
- Add **screenshots section**
- Write a **GitHub project description (short)**
- Prepare **deployment steps for viva**

Just tell me 😊
