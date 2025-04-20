from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class AudioConversion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(255), nullable=False)
    original_format = db.Column(db.String(20), nullable=False)
    target_formats = db.Column(db.String(255), nullable=False)  # Хранит форматы через запятую
    status = db.Column(db.String(50), default='pending')  # pending, processing, completed, failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    def __repr__(self):
        return f'<AudioConversion {self.original_filename} -> {self.target_formats}>' 