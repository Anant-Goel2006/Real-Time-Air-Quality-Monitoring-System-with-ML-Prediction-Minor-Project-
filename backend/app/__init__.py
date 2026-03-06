import os
from flask import Flask, send_from_directory

def create_app():
    # Build a Flask application factory
    # Use explicitly defined static/template paths pointing sequentially up 2 dirs 
    # (from app/ -> backend/ -> Air project root)
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    app = Flask(
        __name__,
        static_folder=os.path.join(base_dir, "static"),
        template_folder=os.path.join(base_dir, "templates")
    )
    
    # Configuration
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    
    # Load Models on start
    from backend.app.services.ml import load_models
    load_models()

    # Pre-warm Live Cache for top cities in background
    from backend.app.services.core_live import get_live_snapshot_rows
    from backend.app.config import LIVE_MONITOR_CITIES
    import threading
    def initial_warmup():
        try:
            get_live_snapshot_rows(force=False, city_queries=LIVE_MONITOR_CITIES)
        except Exception as e:
            print(f"Warning: initial warmup failed - {e}")
            
    threading.Thread(target=initial_warmup, daemon=True).start()

    # Register Blueprints
    from backend.app.routes.api import api_bp
    from backend.app.routes.ml import ml_bp
    from backend.app.routes.views import views_bp
    
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(ml_bp, url_prefix="/api")
    app.register_blueprint(views_bp)
    
    # Route to serve the dataset CSV if needed
    @app.route("/data/<path:filename>")
    def download_dataset(filename):
        dataset_dir = os.path.join(base_dir, "data")
        return send_from_directory(dataset_dir, filename)
        
    return app
