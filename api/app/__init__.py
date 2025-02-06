from flask import Flask
from .cloud_init import cloud_init

def create_app():
    app = Flask(__name__)
    app.register_blueprint(cloud_init, url_prefix='/cloud-init')
    return app 