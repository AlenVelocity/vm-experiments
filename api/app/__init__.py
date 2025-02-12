from flask import Flask, jsonify
from flask_cors import CORS
from .cloud_init import cloud_init
from .vms import vms
from .networks import networks
from .firewall import firewall
from .templates import templates
from datetime import datetime

def create_app():
    app = Flask(__name__)
    CORS(app)  # Enable CORS for all routes
    
    # Register blueprints
    app.register_blueprint(cloud_init, url_prefix='/cloud-init')
    app.register_blueprint(vms, url_prefix='/vms')
    app.register_blueprint(networks, url_prefix='/networks')
    app.register_blueprint(firewall, url_prefix='/firewall')
    app.register_blueprint(templates, url_prefix='/templates')
    
    @app.route('/health')
    def health_check():
        """Health check endpoint"""
        return jsonify({
            "status": "healthy",
            "version": "1.0.0",
            "timestamp": datetime.now().isoformat()
        })
    
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def server_error(error):
        return jsonify({"error": "Internal server error"}), 500
    
    return app 