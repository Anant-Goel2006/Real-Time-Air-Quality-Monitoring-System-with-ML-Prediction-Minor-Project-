import os
import joblib
from backend.app.config import CATS
from backend.app.utils import get_category

# Base paths relative to project root (2 levels up from backend/app/services/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ML_DIR = os.path.join(BASE_DIR, "models")

model = None
scaler = None
encoders = None

def load_models():
    """Load Random Forest Model, Scaler, and Encoders into memory."""
    global model, scaler, encoders
    
    # Check candidates
    model_cands = [
        os.path.join(ML_DIR, "aqi_model_random_forest.pkl"),
        os.path.join(ML_DIR, "aqi_model_xgb.pkl")
    ]
    scaler_cands = [os.path.join(ML_DIR, "aqi_scaler.pkl")]
    encoder_cands = [os.path.join(ML_DIR, "aqi_encoders.pkl")]
    
    def first_existing(cands):
        for c in cands:
            if os.path.exists(c): return c
        return None
        
    mp = first_existing(model_cands)
    sp = first_existing(scaler_cands)
    ep = first_existing(encoder_cands)

    if mp:
        try:
            model = joblib.load(mp)
            print(f"✓ Loaded Model: {os.path.basename(mp)}")
        except Exception as e:
            print(f"⚠️ Failed to load model: {e}")
    
    if sp:
        try:
            scaler = joblib.load(sp)
            print(f"✓ Loaded Scaler: {os.path.basename(sp)}")
        except Exception as e:
            print(f"⚠️ Failed to load scaler: {e}")
            
    if ep:
        try:
            encoders = joblib.load(ep)
            print(f"✓ Loaded Encoders: {os.path.basename(ep)}")
        except Exception as e:
            print(f"⚠️ Failed to load encoders: {e}")

def get_ml_model():
    return model

def get_ml_scaler():
    return scaler

def get_ml_encoders():
    return encoders
