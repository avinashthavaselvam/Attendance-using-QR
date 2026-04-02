from flask import Flask, render_template_string, request, jsonify, send_file, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from datetime import datetime, timedelta
import qrcode
import io
import base64
import secrets
import csv
from functools import wraps
import hashlib
import json
import math
from collections import defaultdict

ALLOWED_EMAIL_DOMAIN = '@nprcolleges.org'

def is_valid_email(email):
    """Checks if the email belongs to the permitted domain"""
    return email and email.endswith(ALLOWED_EMAIL_DOMAIN)

def calculate_distance(lat1, lon1, lat2, lon2):
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return float('inf')
    R = 6371000  # radius of Earth in meters
    phi_1, phi_2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi_1) * math.cos(phi_2) * \
        math.sin(delta_lambda / 2.0) ** 2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

# Initialize Flask application
app = Flask(__name__)
app.config['SECRET_KEY'] = 'qr-attendance-system-secret-key-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///attendance.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

db = SQLAlchemy(app)

# ==================== DATABASE MODELS ====================

class Student(db.Model):
    """Model for storing student information"""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    qr_token = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_active = db.Column(db.Boolean, default=True)
    is_approved = db.Column(db.Boolean, default=False)
    
    def __init__(self, **kwargs):
        super(Student, self).__init__(**kwargs)
    
    def set_password(self, password):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    def check_password(self, password):
        if not self.password_hash: return False
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()
    
    attendance_records = db.relationship('AttendanceRecord', backref='student', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Student {self.student_id}: {self.name}>'
    
    def generate_qr_code(self):
        """Generate QR code for the student"""
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(self.qr_token)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/png;base64,{img_base64}"
    
    def to_dict(self):
        return {
            'id': self.id,
            'student_id': self.student_id,
            'name': self.name,
            'email': self.email,
            'is_active': self.is_active,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else "N/A"
        }


class AttendanceSession(db.Model):
    """Model for managing attendance sessions"""
    id = db.Column(db.Integer, primary_key=True)
    session_name = db.Column(db.String(200), nullable=False)
    session_code = db.Column(db.String(20), unique=True, nullable=False)
    description = db.Column(db.Text)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    period_number = db.Column(db.Integer, default=0)
    latitude = db.Column(db.Float, nullable=False, default=0.0)
    longitude = db.Column(db.Float, nullable=False, default=0.0)
    radius = db.Column(db.Float, default=50.0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    attendance_records = db.relationship('AttendanceRecord', backref='session', lazy=True, cascade='all, delete-orphan')
    
    def __init__(self, **kwargs):
        super(AttendanceSession, self).__init__(**kwargs)
    
    def __repr__(self):
        return f'<AttendanceSession {self.session_code}: {self.session_name}>'
    
    def is_ongoing(self):
        now = datetime.now()
        return self.start_time <= now <= self.end_time and self.is_active
    
    def to_dict(self):
        return {
            'id': self.id,
            'session_name': self.session_name,
            'session_code': self.session_code,
            'description': self.description,
            'period_number': self.period_number,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'latitude': self.latitude,
            'longitude': self.longitude,
            'radius': self.radius,
            'is_active': self.is_active,
            'is_ongoing': self.is_ongoing(),
            'total_attendees': len(self.attendance_records)
        }


class AttendanceRecord(db.Model):
    """Model for storing individual attendance records"""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('attendance_session.id'), nullable=False)
    check_in_time = db.Column(db.DateTime, default=datetime.now)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(200))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    status = db.Column(db.String(20), default='present')
    
    __table_args__ = (db.UniqueConstraint('student_id', 'session_id', name='_student_session_uc'),)
    
    def __init__(self, **kwargs):
        super(AttendanceRecord, self).__init__(**kwargs)
    
    def __repr__(self):
        return f'<AttendanceRecord Student:{self.student_id} Session:{self.session_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'student_name': self.student.name if self.student else "Unknown",
            'student_id': self.student.student_id if self.student else "N/A",
            'session_name': self.session.session_name if self.session else "Deleted Session",
            'check_in_time': self.check_in_time.strftime('%Y-%m-%d %H:%M:%S'),
            'status': self.status
        }
    
    def to_dict_admin(self):
        d = self.to_dict()
        d['location'] = f"{self.latitude}, {self.longitude}" if self.latitude is not None else "N/A"
        return d


class Admin(db.Model):
    """Model for admin users"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_staff = db.Column(db.Boolean, default=False)
    
    def __init__(self, **kwargs):
        super(Admin, self).__init__(**kwargs)
    
    def set_password(self, password):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    def check_password(self, password):
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()
    
    def __repr__(self):
        return f'<Admin {self.username}>'


class Mark(db.Model):
    """Model for storing student marks"""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    internal_marks = db.Column(db.Float, default=0.0)
    assignment_marks = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    student_rel = db.relationship('Student', backref=db.backref('marks', lazy=True))
    
    def __init__(self, **kwargs):
        super(Mark, self).__init__(**kwargs)
    
    def to_dict(self):
        return {
            'id': self.id,
            'subject': self.subject,
            'internal_marks': self.internal_marks,
            'assignment_marks': self.assignment_marks,
            'total_marks': (self.internal_marks or 0.0) + (self.assignment_marks or 0.0),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else "N/A"
        }


# ==================== AUTHENTICATION DECORATOR ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def staff_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        admin = Admin.query.get(session['admin_id'])
        if not admin or not admin.is_staff:
            return jsonify({'success': False, 'message': 'Staff verification (ID card scan) required for this action'}), 403
        return f(*args, **kwargs)
    return decorated_function


# ==================== HTML TEMPLATES ====================

LANDING_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Attendance System</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 600px;
            width: 100%;
            text-align: center;
        }
        h1 { color: #667eea; margin-bottom: 10px; font-size: 2.5em; }
        .subtitle { color: #666; margin-bottom: 40px; font-size: 1.1em; }
        .button-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px; }
        .btn {
            padding: 20px; border: none; border-radius: 12px;
            font-size: 1.1em; font-weight: 600; cursor: pointer;
            transition: all 0.3s; text-decoration: none;
            display: block; color: white;
        }
        .btn-primary { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .btn-secondary { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .btn-success { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }
        .btn-info { background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%); }
        .btn:hover { transform: translateY(-3px); box-shadow: 0 10px 25px rgba(0,0,0,0.2); }
        .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-top: 30px; }
        .stat-box { background: #f8f9fa; padding: 20px; border-radius: 10px; }
        .stat-number { font-size: 2em; font-weight: bold; color: #667eea; }
        .stat-label { color: #666; font-size: 0.9em; margin-top: 5px; }
        @media (max-width: 600px) {
            .button-grid { grid-template-columns: 1fr; }
            .stats { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎯 QR Attendance</h1>
        <p class="subtitle">Advanced Attendance Management System</p>
        <div class="button-grid">
            <a href="/student-portal" class="btn btn-secondary">👤 Student Portal (Scan QR)</a>
            <a href="/admin" class="btn btn-success">🔐 Admin Dashboard</a>
            <a href="/sessions" class="btn btn-info">📅 View Sessions</a>
        </div>
        <div class="stats">
            <div class="stat-box">
                <div class="stat-number">{{ stats.total_students }}</div>
                <div class="stat-label">Students</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ stats.total_sessions }}</div>
                <div class="stat-label">Sessions</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ stats.total_records }}</div>
                <div class="stat-label">Records</div>
            </div>
        </div>
    </div>
</body>
</html>
"""

SCANNER_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scan QR Code</title>
    <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh; padding: 20px;
        }
        .container {
            max-width: 800px; margin: 0 auto; background: white;
            border-radius: 20px; padding: 30px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 { color: #667eea; margin-bottom: 30px; text-align: center; }
        #reader { border: 3px solid #667eea; border-radius: 12px; overflow: hidden; margin-bottom: 20px; }
        .session-selector { margin-bottom: 20px; }
        select, button { width: 100%; padding: 15px; font-size: 1em; border-radius: 8px; border: 2px solid #ddd; margin-bottom: 15px; }
        button { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; cursor: pointer; font-weight: 600; }
        button:hover { opacity: 0.9; }
        .result { padding: 20px; border-radius: 8px; margin-top: 20px; display: none; }
        .success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .attendance-list { margin-top: 30px; }
        .attendance-item {
            background: #f8f9fa; padding: 15px; border-radius: 8px;
            margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center;
        }
        .back-btn { background: #6c757d; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📱 Scan QR Code for Attendance</h1>
        <div class="session-selector">
            <label for="session-select"><strong>Select Session:</strong></label>
            <select id="session-select">
                <option value="">-- Choose a session --</option>
            </select>
        </div>
        <div id="reader"></div>
        <button onclick="startScanner()" id="start-btn">Start Scanner</button>
        <button onclick="stopScanner()" id="stop-btn" style="display:none; background:#dc3545;">Stop Scanner</button>
        <div id="result" class="result"></div>
        <div class="attendance-list" id="attendance-list"></div>
        <a href="/" style="text-decoration:none;"><button class="back-btn">← Back to Home</button></a>
    </div>

    <script>
        let html5QrcodeScanner = null;
        let isScanning = false;
        loadSessions();

        async function loadSessions() {
            try {
                const response = await fetch('/api/sessions/active');
                const data = await response.json();
                const select = document.getElementById('session-select');
                data.sessions.forEach(session => {
                    const option = document.createElement('option');
                    option.value = session.id;
                    option.textContent = `${session.session_name} (${session.session_code})`;
                    select.appendChild(option);
                });
            } catch (error) { console.error('Error loading sessions:', error); }
        }

        function startScanner() {
            const sessionId = document.getElementById('session-select').value;
            if (!sessionId) { showResult('Please select a session first!', false); return; }
            showResult('Requesting location access...', true);
            if ("geolocation" in navigator) {
                navigator.geolocation.getCurrentPosition(
                    function(position) {
                        window.currentLat = position.coords.latitude;
                        window.currentLng = position.coords.longitude;
                        showResult('Location acquired. Starting scanner...', true);
                        html5QrcodeScanner = new Html5Qrcode("reader");
                        const config = { fps: 10, qrbox: { width: 250, height: 250 } };
                        html5QrcodeScanner.start({ facingMode: "environment" }, config, onScanSuccess, onScanError)
                            .then(() => {
                                isScanning = true;
                                document.getElementById('result').style.display = 'none';
                                document.getElementById('start-btn').style.display = 'none';
                                document.getElementById('stop-btn').style.display = 'block';
                            }).catch(err => { showResult('Error starting camera: ' + err, false); });
                    },
                    function(error) { showResult('Location access denied! You must turn on location for attendance.', false); },
                    { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
                );
            } else { showResult('Geolocation is not supported by your browser.', false); }
        }

        function stopScanner() {
            if (html5QrcodeScanner) {
                html5QrcodeScanner.stop().then(() => {
                    isScanning = false;
                    document.getElementById('start-btn').style.display = 'block';
                    document.getElementById('stop-btn').style.display = 'none';
                });
            }
        }

        async function onScanSuccess(decodedText, decodedResult) {
            const sessionId = document.getElementById('session-select').value;
            try {
                const response = await fetch('/api/mark-attendance', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ qr_token: decodedText, session_id: parseInt(sessionId), latitude: window.currentLat, longitude: window.currentLng })
                });
                const data = await response.json();
                showResult(data.message, data.success);
                if (data.success) { loadAttendanceList(sessionId); }
            } catch (error) { showResult('Error marking attendance: ' + error, false); }
        }

        function onScanError(errorMessage) {}

        function showResult(message, success) {
            const resultDiv = document.getElementById('result');
            resultDiv.textContent = message;
            resultDiv.className = 'result ' + (success ? 'success' : 'error');
            resultDiv.style.display = 'block';
            setTimeout(() => { resultDiv.style.display = 'none'; }, 5000);
        }

        async function loadAttendanceList(sessionId) {
            try {
                const response = await fetch(`/api/session/${sessionId}/attendance`);
                const data = await response.json();
                const listDiv = document.getElementById('attendance-list');
                listDiv.innerHTML = '<h3>Today\'s Attendance</h3>';
                data.records.forEach(record => {
                    const item = document.createElement('div');
                    item.className = 'attendance-item';
                    item.innerHTML = `<span><strong>${record.student_name}</strong> (${record.student_id})</span><span>${record.check_in_time}</span>`;
                    listDiv.appendChild(item);
                });
            } catch (error) { console.error('Error loading attendance:', error); }
        }

        setInterval(() => {
            const sessionId = document.getElementById('session-select').value;
            if (sessionId) { loadAttendanceList(sessionId); }
        }, 10000);
    </script>
</body>
</html>
"""

ADMIN_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Dashboard</title>
    <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f5f5f5; }
        .navbar {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; padding: 20px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .container { max-width: 1400px; margin: 30px auto; padding: 0 20px; }
        .tabs { display: flex; gap: 10px; margin-bottom: 30px; flex-wrap: wrap; }
        .tab { padding: 15px 30px; background: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; transition: all 0.3s; }
        .tab.active { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
        .tab-content { display: none; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .tab-content.active { display: block; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background: #f8f9fa; font-weight: 600; }
        .btn { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; margin: 5px; }
        .btn-primary { background: #667eea; color: white; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-success { background: #28a745; color: white; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 5px; font-weight: 600; }
        .form-group input, .form-group textarea, .form-group select { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 1em; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; overflow-y: auto; }
        .modal-content { background: white; max-width: 600px; margin: 50px auto; padding: 30px; border-radius: 12px; }
        .close { float: right; font-size: 28px; font-weight: bold; cursor: pointer; }
    </style>
</head>
<body>
    <div class="navbar">
        <h1>🎯 Admin Dashboard</h1>
        <div>
            <span id="staff-status" style="margin-right:20px; font-weight:bold;">
                {% if admin.is_staff %} ✅ Staff Verified {% else %} ⚠️ Verification Required {% endif %}
            </span>
            {% if not admin.is_staff %}
            <button id="verify-staff-btn" class="btn btn-primary" style="background:#fff; color:#667eea;" onclick="showModal('staffVerifyModal')">🛡️ Verify Staff ID</button>
            {% endif %}
            <a href="/logout" style="color:white; text-decoration:none; margin-left:20px;">Logout</a>
        </div>
    </div>
    <div class="container">
        <div class="tabs">
            <button class="tab active" onclick="showTab('students', event)">Students</button>
            <button class="tab" onclick="showTab('pending-approval', event)">⏳ Pending Approval</button>
            <button class="tab" onclick="showTab('marks-management', event)">📝 Marks Management</button>
            <button class="tab" onclick="showTab('sessions', event)">Sessions</button>
            <button class="tab" onclick="showTab('attendance', event)">Attendance Records</button>
            <button class="tab" onclick="showTab('location-tracker', event)">📍 Location Tracker</button>
            <button class="tab" onclick="showTab('analytics', event)">Analytics</button>
        </div>
        <div id="students" class="tab-content active">
            <h2>Student Management</h2>
            <button class="btn btn-primary" onclick="showModal('addStudentModal')">+ Add Student</button>
            <button class="btn btn-success" onclick="exportStudents()">Export Students</button>
            <div id="students-list"></div>
        </div>
        <div id="pending-approval" class="tab-content">
            <h2>Students Awaiting Approval</h2>
            <div id="pending-students-list"></div>
        </div>
        <div id="marks-management" class="tab-content">
            <h2>Marks Management</h2>
            <div id="marks-students-list"></div>
        </div>
        <div id="sessions" class="tab-content">
            <h2>Session Management</h2>
            <div style="margin-bottom: 20px;">
                <button class="btn btn-primary" onclick="showModal('addSessionModal')">+ Create Custom Session</button>
                <button class="btn btn-success" onclick="showModal('quickCreateModal')">⚡ Quick Create 7 Periods</button>
            </div>
            <div id="sessions-list"></div>
        </div>
        <div id="attendance" class="tab-content">
            <h2>Attendance Records</h2>
            <button class="btn btn-success" onclick="exportAttendance()">Export All Records</button>
            <div id="attendance-list"></div>
        </div>
        <div id="analytics" class="tab-content">
            <h2>Analytics & Reports</h2>
            <div id="analytics-content"></div>
        </div>
        <div id="location-tracker" class="tab-content">
            <h2>📍 Live Location Tracker</h2>
            <div class="form-group">
                <label>Select Session to Track:</label>
                <select id="track-session-select" onchange="initTrackerMap()">
                    <option value="">-- Choose a session --</option>
                </select>
            </div>
            <div id="map" style="height: 500px; border-radius: 12px; border: 2px solid #ddd; margin-top: 15px;"></div>
            <div id="roaming-alerts" style="margin-top: 20px;"></div>
        </div>
    </div>

    <div id="addStudentModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('addStudentModal')">&times;</span>
            <h2>Add New Student</h2>
            <form id="addStudentForm" onsubmit="addStudent(event)">
                <div class="form-group"><label>Student ID:</label><input type="text" name="student_id" required></div>
                <div class="form-group"><label>Name:</label><input type="text" name="name" required></div>
                <div class="form-group"><label>Email:</label><input type="email" name="email" required></div>
                <button type="submit" class="btn btn-primary">Add Student</button>
            </form>
        </div>
    </div>

    <div id="marksModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('marksModal')">&times;</span>
            <h2 id="marksModalTitle">Edit Marks</h2>
            <form id="marksForm" onsubmit="saveMarks(event)">
                <input type="hidden" name="student_id" id="marksStudentId">
                <div class="form-group"><label>Subject:</label><input type="text" name="subject" required></div>
                <div class="form-group"><label>Internal Exam Marks:</label><input type="number" step="0.1" name="internal_marks" required></div>
                <div class="form-group"><label>Assignment Marks:</label><input type="number" step="0.1" name="assignment_marks" required></div>
                <button type="submit" class="btn btn-success">Save Marks</button>
            </form>
            <hr style="margin:20px 0;">
            <h3>Current Marks</h3>
            <div id="current-marks-list" style="margin-top:10px;"></div>
        </div>
    </div>

    <div id="staffVerifyModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('staffVerifyModal')">&times;</span>
            <h2>🛡️ Staff ID Verification</h2>
            <p>Please scan your Staff ID card QR code to confirm your identity.</p>
            <div id="staff-reader" style="width:100%; height:300px; border:2px solid #667eea; border-radius:8px; overflow:hidden; margin:20px 0;"></div>
            <button class="btn btn-primary" onclick="startStaffScanner()" id="start-staff-btn">Start Scanner</button>
        </div>
    </div>

    <div id="addSessionModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('addSessionModal')">&times;</span>
            <h2>Create New Session</h2>
            <form id="addSessionForm" onsubmit="addSession(event)">
                <div class="form-group"><label>Session Name:</label><input type="text" name="session_name" required></div>
                <div class="form-group"><label>Session Code:</label><input type="text" name="session_code" required></div>
                <div class="form-group"><label>Description:</label><textarea name="description" rows="3"></textarea></div>
                <div class="form-group"><label>Start Time:</label><input type="datetime-local" name="start_time" required></div>
                <div class="form-group"><label>End Time:</label><input type="datetime-local" name="end_time" required></div>
                <div class="form-group"><label>Classroom Latitude:</label><input type="number" step="any" name="latitude" id="sessionLat" placeholder="e.g. 28.6139" required></div>
                <div class="form-group"><label>Classroom Longitude:</label><input type="number" step="any" name="longitude" id="sessionLng" placeholder="e.g. 77.2090" required></div>
                <div class="form-group"><label>Allowed Radius (meters):</label><input type="number" step="any" name="radius" value="50" required></div>
                <button type="button" onclick="getCurrentAdminLocation()" style="width:100%; padding:10px; margin-bottom:15px; background:#17a2b8; color:white; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">📍 Use My Current Location</button>
                <button type="submit" class="btn btn-primary">Create Session</button>
            </form>
        </div>
    </div>

    <div id="quickCreateModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('quickCreateModal')">&times;</span>
            <h2>⚡ Quick Create 7 Periods</h2>
            <form id="quickCreateForm" onsubmit="quickCreate7(event)">
                <div class="form-group"><label>Date:</label><input type="date" name="date" id="qcDate" required></div>
                <div class="form-group"><label>Subject Prefix:</label><input type="text" name="prefix" id="qcPrefix" placeholder="e.g. CSE-305" required></div>
                <div class="form-group"><label>Classroom Latitude:</label><input type="number" step="any" name="latitude" id="qcLat" required></div>
                <div class="form-group"><label>Classroom Longitude:</label><input type="number" step="any" name="longitude" id="qcLng" required></div>
                <div class="form-group"><label>Allowed Radius (m):</label><input type="number" step="any" name="radius" value="50" required></div>
                <button type="button" onclick="getQCLocation()" style="width:100%; padding:10px; margin-bottom:15px; background:#17a2b8; color:white; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">📍 Use Current Location</button>
                <button type="submit" class="btn btn-success">Generate 7 Sessions</button>
            </form>
        </div>
    </div>

    <div id="sessionQRModal" class="modal">
        <div class="modal-content" style="text-align:center;">
            <span class="close" onclick="closeModal('sessionQRModal')">&times;</span>
            <h2 id="qrModalTitle">Session QR Code</h2>
            <div id="session-qr-display" style="margin: 20px 0;"></div>
            <p id="qrModalCode" style="font-weight:bold; font-size:1.2em;"></p>
            <button class="btn btn-primary" onclick="window.print()">Print Code</button>
        </div>
    </div>

    <script>
        loadStudents(); loadSessions(); loadAttendance(); loadAnalytics(); loadPendingStudents();
        let staffScanner = null;

        function showTab(tabName, event) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById(tabName).classList.add('active');
            if (tabName === 'marks-management') loadMarksStudents();
        }
        function showModal(modalId) { document.getElementById(modalId).style.display = 'block'; }
        function closeModal(modalId) { 
            document.getElementById(modalId).style.display = 'none'; 
            if (modalId === 'staffVerifyModal' && staffScanner) staffScanner.stop();
        }

        async function loadStudents() {
            const response = await fetch('/api/students');
            const data = await response.json();
            const list = document.getElementById('students-list');
            let html = '<table><tr><th>Student ID</th><th>Name</th><th>Email</th><th>QR Code</th><th>Actions</th></tr>';
            data.students.forEach(student => {
                html += `<tr><td>${student.student_id}</td><td>${student.name}</td><td>${student.email}</td>
                    <td><button class="btn btn-primary" onclick="showQRCode(${student.id})">View QR</button></td>
                    <td><button class="btn btn-danger" onclick="deleteStudent(${student.id})">Delete</button></td></tr>`;
            });
            list.innerHTML = html + '</table>';
        }

        async function loadPendingStudents() {
            const response = await fetch('/api/students/pending');
            const data = await response.json();
            const list = document.getElementById('pending-students-list');
            if (data.students.length === 0) {
                list.innerHTML = '<p style="padding:20px; color:#666;">No students awaiting approval.</p>';
                return;
            }
            let html = '<table><tr><th>Student ID</th><th>Name</th><th>Email</th><th>Actions</th></tr>';
            data.students.forEach(student => {
                html += `<tr><td>${student.student_id}</td><td>${student.name}</td><td>${student.email}</td>
                    <td><button class="btn btn-success" onclick="approveStudent(${student.id})">Approve</button></td></tr>`;
            });
            list.innerHTML = html + '</table>';
        }

        async function approveStudent(id) {
            const response = await fetch(`/api/students/${id}/approve`, {method: 'POST'});
            const result = await response.json();
            alert(result.message);
            loadPendingStudents(); loadStudents();
        }

        async function loadMarksStudents() {
            const response = await fetch('/api/students');
            const data = await response.json();
            const list = document.getElementById('marks-students-list');
            let html = '<table><tr><th>Student ID</th><th>Name</th><th>Actions</th></tr>';
            data.students.forEach(student => {
                html += `<tr><td>${student.student_id}</td><td>${student.name}</td>
                    <td><button class="btn btn-primary" onclick="openMarksModal(${student.id}, '${student.name}')">Manage Marks</button></td></tr>`;
            });
            list.innerHTML = html + '</table>';
        }

        async function openMarksModal(id, name) {
            document.getElementById('marksStudentId').value = id;
            document.getElementById('marksModalTitle').textContent = `Manage Marks: ${name}`;
            showModal('marksModal');
            loadCurrentMarks(id);
        }

        async function loadCurrentMarks(id) {
            const response = await fetch(`/api/marks?student_id=${id}`);
            const data = await response.json();
            const list = document.getElementById('current-marks-list');
            if (data.marks.length === 0) {
                list.innerHTML = '<p>No marks recorded yet.</p>';
                return;
            }
            let html = '<table><tr><th>Subject</th><th>Internal</th><th>Assignment</th><th>Total</th></tr>';
            data.marks.forEach(m => {
                html += `<tr><td>${m.subject}</td><td>${m.internal_marks}</td><td>${m.assignment_marks}</td><td>${m.total_marks}</td></tr>`;
            });
            list.innerHTML = html + '</table>';
        }

        async function saveMarks(e) {
            e.preventDefault();
            const formData = new FormData(e.target);
            const data = Object.fromEntries(formData);
            const response = await fetch('/api/marks', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            const result = await response.json();
            if (result.success) {
                alert('Marks saved successfully!');
                loadCurrentMarks(data.student_id);
                e.target.reset();
                document.getElementById('marksStudentId').value = data.student_id;
            }
        }

        function startStaffScanner() {
            staffScanner = new Html5Qrcode("staff-reader");
            staffScanner.start({ facingMode: "environment" }, { fps: 10, qrbox: 250 }, async (decodedText) => {
                await staffScanner.stop();
                const response = await fetch('/api/admin/verify-staff', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token: decodedText})
                });
                const result = await response.json();
                alert(result.message);
                if (result.success) {
                    location.reload();
                }
            });
            document.getElementById('start-staff-btn').style.display = 'none';
        }

        async function addStudent(event) {
            event.preventDefault();
            const formData = new FormData(event.target);
            const data = Object.fromEntries(formData);
            const response = await fetch('/api/students', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
            const result = await response.json();
            if (result.success) { closeModal('addStudentModal'); loadStudents(); event.target.reset(); }
        }

        async function loadSessions() {
            const response = await fetch('/api/sessions');
            const data = await response.json();
            const list = document.getElementById('sessions-list');
            let html = '<table><tr><th>Period</th><th>Code</th><th>Name</th><th>Start</th><th>End</th><th>Status</th><th>Attendees</th><th>Actions</th></tr>';
            data.sessions.forEach(session => {
                const status = session.is_ongoing ? '🟢 Active' : '🔴 Inactive';
                const period = session.period_number > 0 ? `P${session.period_number}` : '-';
                html += `<tr><td>${period}</td><td>${session.session_code}</td><td>${session.session_name}</td><td>${session.start_time}</td>
                    <td>${session.end_time}</td><td>${status}</td><td>${session.total_attendees}</td>
                    <td>
                        <button class="btn btn-primary" onclick="showSessionQR(${session.id}, '${session.session_name}', '${session.session_code}')">Show QR</button>
                        <button class="btn btn-danger" onclick="deleteSession(${session.id})">Delete</button>
                    </td></tr>`;
            });
            list.innerHTML = html + '</table>';
        }

        async function addSession(event) {
            event.preventDefault();
            const formData = new FormData(event.target);
            const data = Object.fromEntries(formData);
            const response = await fetch('/api/sessions', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
            const result = await response.json();
            if (result.success) { closeModal('addSessionModal'); loadSessions(); event.target.reset(); }
        }

        async function loadAttendance() {
            const response = await fetch('/api/attendance');
            const data = await response.json();
            const list = document.getElementById('attendance-list');
            let html = '<table><tr><th>Student</th><th>Session</th><th>Check-in</th><th>Status</th><th>Location</th></tr>';
            data.records.forEach(record => {
                html += `<tr><td>${record.student_name} (${record.student_id})</td><td>${record.session_name}</td>
                    <td>${record.check_in_time}</td><td>${record.status}</td><td>${record.location}</td></tr>`;
            });
            list.innerHTML = html + '</table>';
        }

        async function showSessionQR(id, title, code) {
            const response = await fetch(`/api/session/${id}/qr`);
            const data = await response.json();
            document.getElementById('qrModalTitle').textContent = title;
            document.getElementById('qrModalCode').textContent = `Code: ${code}`;
            document.getElementById('session-qr-display').innerHTML = `<img src="${data.qr_code}" style="max-width:300px;">`;
            showModal('sessionQRModal');
        }

        function getQCLocation() {
            if ("geolocation" in navigator) {
                navigator.geolocation.getCurrentPosition(p => {
                    document.getElementById('qcLat').value = p.coords.latitude;
                    document.getElementById('qcLng').value = p.coords.longitude;
                });
            }
        }

        function getCurrentAdminLocation() {
            if ("geolocation" in navigator) {
                navigator.geolocation.getCurrentPosition(
                    function(position) {
                        document.getElementById('sessionLat').value = position.coords.latitude;
                        document.getElementById('sessionLng').value = position.coords.longitude;
                        alert('Classroom location acquired successfully!');
                    },
                    function(error) { alert('Error acquiring location: ' + error.message); }
                );
            } else { alert("Geolocation is not supported by your browser."); }
        }

        async function loadAnalytics() {
            const response = await fetch('/api/analytics');
            const data = await response.json();
            document.getElementById('analytics-content').innerHTML = `
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px;">
                    <div style="background:#f8f9fa; padding:20px; border-radius:8px;"><h3>Total Students</h3><p style="font-size:2em; color:#667eea;">${data.total_students}</p></div>
                    <div style="background:#f8f9fa; padding:20px; border-radius:8px;"><h3>Total Sessions</h3><p style="font-size:2em; color:#667eea;">${data.total_sessions}</p></div>
                    <div style="background:#f8f9fa; padding:20px; border-radius:8px;"><h3>Total Attendance</h3><p style="font-size:2em; color:#667eea;">${data.total_attendance}</p></div>
                    <div style="background:#f8f9fa; padding:20px; border-radius:8px;"><h3>Avg Attendance</h3><p style="font-size:2em; color:#667eea;">${data.avg_attendance}%</p></div>
                </div>`;
            
            // Also update session selectors
            const sResponse = await fetch('/api/sessions');
            const sData = await sResponse.json();
            const selectors = [document.getElementById('track-session-select')];
            selectors.forEach(sel => {
                if(!sel) return;
                const val = sel.value;
                sel.innerHTML = '<option value="">-- Choose a session --</option>';
                sData.sessions.forEach(s => {
                    const opt = document.createElement('option');
                    opt.value = s.id;
                    opt.textContent = `${s.period_number > 0 ? 'P'+s.period_number : ''} ${s.session_name} (${s.session_code})`;
                    sel.appendChild(opt);
                });
                sel.value = val;
            });
        }

        async function quickCreate7(e) {
            e.preventDefault();
            const formData = new FormData(e.target);
            const data = Object.fromEntries(formData);
            const response = await fetch('/api/sessions/create-day', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            const result = await response.json();
            alert(result.message);
            if (result.success) { closeModal('quickCreateModal'); loadSessions(); loadAnalytics(); }
        }

        let trackerMap = null;
        let trackerMarkers = [];
        let classroomCircle = null;
        let trackerInterval = null;

        function initTrackerMap() {
            const sid = document.getElementById('track-session-select').value;
            if (!sid) return;
            
            if (!trackerMap) {
                trackerMap = L.map('map').setView([0, 0], 2);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(trackerMap);
            }
            
            if (trackerInterval) clearInterval(trackerInterval);
            loadLocationMap();
            trackerInterval = setInterval(loadLocationMap, 10000);
        }

        async function loadLocationMap() {
            const sid = document.getElementById('track-session-select').value;
            if (!sid) return;
            
            const response = await fetch(`/api/session/${sid}/locations`);
            const data = await response.json();
            
            // Clear old markers
            trackerMarkers.forEach(m => trackerMap.removeLayer(m));
            trackerMarkers = [];
            if (classroomCircle) trackerMap.removeLayer(classroomCircle);
            
            if (data.session) {
                const s = data.session;
                trackerMap.setView([s.latitude, s.longitude], 18);
                
                // Classroom marker
                const cMarker = L.marker([s.latitude, s.longitude], {
                    icon: L.divIcon({className: 'classroom-icon', html: '🔵', iconSize: [20, 20]})
                }).addTo(trackerMap).bindPopup("Classroom Center");
                trackerMarkers.push(cMarker);
                
                classroomCircle = L.circle([s.latitude, s.longitude], {
                    radius: s.radius, color: 'blue', fillOpacity: 0.1
                }).addTo(trackerMap);
            }
            
            let roamingHtml = '<h3>Roaming / Out of Range Students</h3><ul>';
            let roamingCount = 0;

            data.locations.forEach(loc => {
                let color = '🟢';
                if (loc.status === 'absent') { 
                    color = '🔴'; 
                    roamingHtml += `<li><strong>${loc.name}</strong> is currently at ${loc.distance}m away (Roaming)</li>`;
                    roamingCount++;
                }
                else if (loc.status === 'late') color = '🟡';
                
                const marker = L.marker([loc.lat, loc.lng], {
                    icon: L.divIcon({className: 'student-icon', html: color, iconSize: [20, 20]})
                }).addTo(trackerMap).bindPopup(`${loc.name} (${loc.student_id})<br>Status: ${loc.status}<br>Distance: ${loc.distance}m`);
                trackerMarkers.push(marker);
            });

            document.getElementById('roaming-alerts').innerHTML = roamingCount > 0 ? roamingHtml + '</ul>' : '<p>✅ All attendees are currently within the allowed range.</p>';
        }

        async function showQRCode(studentId) {
            const response = await fetch(`/api/student/${studentId}/qr`);
            const data = await response.json();
            const modal = document.createElement('div');
            modal.className = 'modal';
            modal.style.display = 'block';
            modal.innerHTML = `<div class="modal-content" style="text-align:center;">
                <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
                <h2>${data.name}</h2><p>${data.student_id}</p>
                <img src="${data.qr_code}" style="max-width:100%; margin:20px 0;">
                <p>Token: ${data.qr_token}</p></div>`;
            document.body.appendChild(modal);
        }

        async function deleteStudent(id) {
            if (confirm('Are you sure you want to delete this student?')) { await fetch(`/api/students/${id}`, {method: 'DELETE'}); loadStudents(); }
        }
        async function deleteSession(id) {
            if (confirm('Are you sure you want to delete this session?')) { await fetch(`/api/sessions/${id}`, {method: 'DELETE'}); loadSessions(); }
        }
        async function exportStudents() { window.location.href = '/export/students'; }
        async function exportAttendance() { window.location.href = '/export/attendance'; }
    </script>
</body>
</html>
"""

# ==================== ROUTES ====================

@app.route('/')
def index():
    stats = {
        'total_students': Student.query.count(),
        'total_sessions': AttendanceSession.query.count(),
        'total_records': AttendanceRecord.query.count()
    }
    return render_template_string(LANDING_PAGE, stats=stats)





@app.route('/sessions')
def view_sessions():
    sessions = AttendanceSession.query.order_by(AttendanceSession.start_time.desc()).all()
    sessions_page = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>View Sessions</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh; padding: 20px;
            }
            .container {
                max-width: 900px; margin: 0 auto; background: white;
                border-radius: 20px; padding: 30px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }
            h1 { color: #667eea; margin-bottom: 30px; text-align: center; }
            .session-card {
                background: #f8f9fa; border-radius: 12px; padding: 20px;
                margin-bottom: 15px; border-left: 4px solid #667eea;
                transition: transform 0.2s;
            }
            .session-card:hover { transform: translateX(5px); }
            .session-card h3 { color: #333; margin-bottom: 8px; }
            .session-info { color: #666; font-size: 0.9em; margin-bottom: 5px; }
            .badge {
                display: inline-block; padding: 4px 12px; border-radius: 20px;
                font-size: 0.8em; font-weight: 600; margin-top: 8px;
            }
            .badge-active { background: #d4edda; color: #155724; }
            .badge-inactive { background: #f8d7da; color: #721c24; }
            .no-sessions { text-align: center; color: #666; padding: 40px; font-size: 1.1em; }
            .back-btn {
                display: block; width: 100%; padding: 15px; margin-top: 20px;
                background: #6c757d; color: white; border: none; border-radius: 8px;
                font-size: 1em; font-weight: 600; cursor: pointer; text-align: center;
                text-decoration: none;
            }
            .back-btn:hover { opacity: 0.9; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📅 Attendance Sessions</h1>
            {% if sessions %}
                {% for s in sessions %}
                <div class="session-card">
                    <h3>{{ s.session_name }}</h3>
                    <p class="session-info">📌 Code: <strong>{{ s.session_code }}</strong></p>
                    {% if s.description %}
                    <p class="session-info">📝 {{ s.description }}</p>
                    {% endif %}
                    <p class="session-info">🕐 Start: {{ s.start_time.strftime('%Y-%m-%d %H:%M') }}</p>
                    <p class="session-info">🕐 End: {{ s.end_time.strftime('%Y-%m-%d %H:%M') }}</p>
                    <p class="session-info">👥 Attendees: {{ s.attendance_records|length }}</p>
                    {% if s.is_ongoing() %}
                        <span class="badge badge-active">🟢 Active</span>
                    {% else %}
                        <span class="badge badge-inactive">🔴 Inactive</span>
                    {% endif %}
                </div>
                {% endfor %}
            {% else %}
                <div class="no-sessions">No sessions have been created yet.</div>
            {% endif %}
            <a href="/" class="back-btn">← Back to Home</a>
        </div>
    </body>
    </html>
    """
    return render_template_string(sessions_page, sessions=sessions)


@app.route('/admin')
@login_required
def admin_dashboard():
    admin = Admin.query.get(session['admin_id'])
    if not admin:
        session.pop('admin_id', None)
        return redirect(url_for('admin_login'))
    return render_template_string(ADMIN_DASHBOARD, admin=admin)


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        data = request.json
        admin = Admin.query.filter_by(username=data['username']).first()
        if admin and admin.check_password(data['password']):
            if not is_valid_email(admin.email):
                return jsonify({'success': False, 'message': f'Admin access restricted to {ALLOWED_EMAIL_DOMAIN} accounts only.'}), 403
            session['admin_id'] = admin.id
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

    login_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Login</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                display: flex; align-items: center; justify-content: center;
                min-height: 100vh;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                font-family: 'Segoe UI', sans-serif;
            }
            .login-box {
                background: white; padding: 40px; border-radius: 12px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                width: 100%; max-width: 400px;
            }
            h2 { color: #667eea; margin-bottom: 30px; text-align: center; }
            input {
                width: 100%; padding: 12px; margin-bottom: 20px;
                border: 2px solid #ddd; border-radius: 6px; font-size: 1em;
                transition: border-color 0.3s; outline: none;
            }
            input:focus { border-color: #667eea; }
            button {
                width: 100%; padding: 12px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white; border: none; border-radius: 6px;
                cursor: pointer; font-size: 1em; font-weight: 600;
            }
            button:hover { opacity: 0.9; }
            /* ── Inline error message (replaces the browser alert) ── */
            .error-msg {
                display: none;
                background: #f8d7da;
                color: #842029;
                border: 1px solid #f5c2c7;
                padding: 12px 16px;
                border-radius: 6px;
                margin-bottom: 18px;
                font-size: 0.95em;
                text-align: center;
            }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h2>Admin Login</h2>
            <!-- Inline error banner -->
            <div class="error-msg" id="error-msg">⚠️ Incorrect username or password.</div>
            <form onsubmit="login(event)">
                <input type="text" id="username" placeholder="Username" required>
                <input type="password" id="password" placeholder="Password" required>
                <button type="submit">Login</button>
            </form>
            <div style="margin-top: 20px; text-align: center;">
                <p style="font-size: 0.9em; color: #666;">Don't have an account? <a href="/admin/register" style="color: #667eea; text-decoration: none;">Register here</a></p>
            </div>
        </div>
        <script>
            async function login(e) {
                e.preventDefault();
                const errorDiv = document.getElementById('error-msg');
                errorDiv.style.display = 'none';          // hide previous error

                const response = await fetch('/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: document.getElementById('username').value,
                        password: document.getElementById('password').value
                    })
                });

                const data = await response.json();
                if (data.success) {
                    window.location.href = '/admin';
                } else {
                    // Show inline error instead of alert()
                    errorDiv.style.display = 'block';
                }
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(login_template)


@app.route('/admin/register', methods=['GET', 'POST'])
def admin_register():
    if request.method == 'POST':
        data = request.json
        if not is_valid_email(data.get('email', '')):
            return jsonify({'success': False, 'message': f'Only {ALLOWED_EMAIL_DOMAIN} emails are allowed'}), 400
        if Admin.query.filter_by(username=data['username']).first():
            return jsonify({'success': False, 'message': 'Username already exists'}), 400
        
        admin = Admin(username=data['username'], email=data['email'])
        admin.set_password(data['password'])
        db.session.add(admin)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Admin registered successfully'})

    register_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Registration</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                display: flex; align-items: center; justify-content: center;
                min-height: 100vh;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                font-family: 'Segoe UI', sans-serif;
            }
            .register-box {
                background: white; padding: 40px; border-radius: 12px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                width: 100%; max-width: 400px;
            }
            h2 { color: #667eea; margin-bottom: 30px; text-align: center; }
            input {
                width: 100%; padding: 12px; margin-bottom: 20px;
                border: 2px solid #ddd; border-radius: 6px; font-size: 1em;
                transition: border-color 0.3s; outline: none;
            }
            input:focus { border-color: #667eea; }
            button {
                width: 100%; padding: 12px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white; border: none; border-radius: 6px;
                cursor: pointer; font-size: 1em; font-weight: 600;
            }
            button:hover { opacity: 0.9; }
            .links { margin-top: 20px; text-align: center; }
            .links a { color: #667eea; text-decoration: none; font-size: 0.9em; }
        </style>
    </head>
    <body>
        <div class="register-box">
            <h2>Admin Registration</h2>
            <form onsubmit="register(event)">
                <input type="text" id="username" placeholder="Username" required>
                <input type="email" id="email" placeholder="Email" required>
                <input type="password" id="password" placeholder="Password" required>
                <button type="submit">Register</button>
            </form>
            <div class="links">
                <p>Already have an account? <a href="/admin/login">Login here</a></p>
            </div>
        </div>
        <script>
            async function register(e) {
                e.preventDefault();
                const response = await fetch('/admin/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: document.getElementById('username').value,
                        email: document.getElementById('email').value,
                        password: document.getElementById('password').value
                    })
                });
                const data = await response.json();
                if (data.success) {
                    alert('Registration successful! Please login.');
                    window.location.href = '/admin/login';
                } else {
                    alert(data.message || 'Registration failed');
                }
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(register_template)


@app.route('/logout')
def logout():
    session.pop('admin_id', None)
    return redirect(url_for('index'))


# ==================== API ROUTES ====================

@app.route('/api/students', methods=['GET', 'POST'])
@login_required
def manage_students():
    if request.method == 'GET':
        students = Student.query.all()
        return jsonify({'students': [s.to_dict() for s in students]})
    elif request.method == 'POST':
        data = request.json
        if not is_valid_email(data.get('email', '')):
            return jsonify({'success': False, 'message': f'Only {ALLOWED_EMAIL_DOMAIN} emails are allowed'}), 400
        qr_token = secrets.token_urlsafe(32)
        student = Student(student_id=data['student_id'], name=data['name'], email=data['email'], qr_token=qr_token)
        try:
            db.session.add(student)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Student added successfully', 'student': student.to_dict()})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 400


@app.route('/api/students/<int:student_id>', methods=['DELETE'])
@login_required
def delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    db.session.delete(student)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Student deleted'})


@app.route('/api/student/<int:student_id>/qr')
@login_required
def get_student_qr(student_id):
    student = Student.query.get_or_404(student_id)
    return jsonify({'name': student.name, 'student_id': student.student_id, 'qr_code': student.generate_qr_code(), 'qr_token': student.qr_token})


@app.route('/api/sessions', methods=['GET', 'POST'])
@login_required
def manage_sessions():
    if request.method == 'GET':
        sessions = AttendanceSession.query.order_by(AttendanceSession.start_time.desc()).all()
        return jsonify({'sessions': [s.to_dict() for s in sessions]})
    elif request.method == 'POST':
        data = request.json
        session_obj = AttendanceSession(
            session_name=data['session_name'], session_code=data['session_code'],
            description=data.get('description', ''),
            start_time=datetime.fromisoformat(data['start_time']),
            end_time=datetime.fromisoformat(data['end_time']),
            latitude=float(data.get('latitude', 0.0)),
            longitude=float(data.get('longitude', 0.0)),
            radius=float(data.get('radius', 50.0))
        )
        try:
            db.session.add(session_obj)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Session created successfully', 'session': session_obj.to_dict()})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 400


@app.route('/api/sessions/<int:session_id>', methods=['DELETE'])
@login_required
def delete_session(session_id):
    session_obj = AttendanceSession.query.get_or_404(session_id)
    db.session.delete(session_obj)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Session deleted'})


@app.route('/api/sessions/create-day', methods=['POST'])
@staff_required
def create_day_sessions():
    data = request.json
    date_str = data.get('date')
    prefix = data.get('prefix', 'Class')
    lat = float(data.get('latitude', 0.0))
    lng = float(data.get('longitude', 0.0))
    radius = float(data.get('radius', 50.0))
    
    if not date_str:
        return jsonify({'success': False, 'message': 'Date is required'}), 400
        
    base_date = datetime.strptime(date_str, '%Y-%m-%d')
    slots = [
        ("09:00", "09:50"), ("09:51", "10:35"), ("10:56", "11:40"),
        ("11:41", "12:25"), ("13:01", "13:45"), ("13:46", "14:30"),
        ("14:31", "15:15")
    ]
    
    created = []
    try:
        for i, (start, end) in enumerate(slots, 1):
            s_time = datetime.combine(base_date.date(), datetime.strptime(start, '%H:%M').time())
            e_time = datetime.combine(base_date.date(), datetime.strptime(end, '%H:%M').time())
            s_code = f"{prefix}-P{i}-{base_date.strftime('%m%d')}"
            
            # Check if code exists
            if AttendanceSession.query.filter_by(session_code=s_code).first():
                continue
                
            session_obj = AttendanceSession(
                session_name=f"{prefix} - Period {i}",
                session_code=s_code,
                period_number=i,
                start_time=s_time,
                end_time=e_time,
                latitude=lat,
                longitude=lng,
                radius=radius
            )
            db.session.add(session_obj)
            created.append(s_code)
            
        db.session.commit()
        return jsonify({'success': True, 'message': f'Created {len(created)} sessions: {", ".join(created)}'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/api/session/<int:session_id>/qr')
@login_required
def get_session_qr_api(session_id):
    session_obj = AttendanceSession.query.get_or_404(session_id)
    # Generate QR for the session code
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(session_obj.session_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    img_base64 = base64.b64encode(buffer.getvalue()).decode()
    return jsonify({
        'session_name': session_obj.session_name,
        'session_code': session_obj.session_code,
        'qr_code': f"data:image/png;base64,{img_base64}"
    })

@app.route('/api/session/<int:session_id>/locations')
@login_required
def get_session_locations(session_id):
    session_obj = AttendanceSession.query.get_or_404(session_id)
    records = AttendanceRecord.query.filter_by(session_id=session_id).all()
    
    locations = []
    for r in records:
        dist = calculate_distance(r.latitude, r.longitude, session_obj.latitude, session_obj.longitude)
        locations.append({
            'student_id': r.student.student_id,
            'name': r.student.name,
            'lat': r.latitude,
            'lng': r.longitude,
            'status': r.status,
            'distance': round(dist, 1)
        })
        
    return jsonify({
        'session': {
            'latitude': session_obj.latitude,
            'longitude': session_obj.longitude,
            'radius': session_obj.radius
        },
        'locations': locations
    })
@app.route('/student/register', methods=['GET', 'POST'])
def student_register():
    if request.method == 'POST':
        data = request.json
        if not is_valid_email(data.get('email', '')):
            return jsonify({'success': False, 'message': f'Only {ALLOWED_EMAIL_DOMAIN} emails are allowed'}), 400
        if Student.query.filter_by(student_id=data['student_id']).first():
            return jsonify({'success': False, 'message': 'Student ID already exists'}), 400
        
        qr_token = secrets.token_urlsafe(32)
        student = Student(student_id=data['student_id'], name=data['name'], email=data['email'], qr_token=qr_token)
        student.set_password(data['password'])
        db.session.add(student)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Student registered successfully'})

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Student Registration</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
            .box { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); width: 100%; max-width: 400px; }
            h2 { color: #f5576c; margin-bottom: 30px; text-align: center; }
            input { width: 100%; padding: 12px; margin-bottom: 20px; border: 2px solid #ddd; border-radius: 6px; }
            button { width: 100%; padding: 12px; background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
            .links { margin-top: 20px; text-align: center; }
            .links a { color: #f5576c; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="box">
            <h2>Student Registration</h2>
            <form onsubmit="register(event)">
                <input type="text" id="student_id" placeholder="Student ID" required>
                <input type="text" id="name" placeholder="Full Name" required>
                <input type="email" id="email" placeholder="Email" required>
                <input type="password" id="password" placeholder="Password" required>
                <button type="submit">Register</button>
            </form>
            <div class="links"><p>Already registered? <a href="/student/login">Login here</a></p></div>
        </div>
        <script>
            async function register(e) {
                e.preventDefault();
                const response = await fetch('/student/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        student_id: document.getElementById('student_id').value,
                        name: document.getElementById('name').value,
                        email: document.getElementById('email').value,
                        password: document.getElementById('password').value
                    })
                });
                const data = await response.json();
                if (data.success) { alert('Registration successful!'); window.location.href = '/student/login'; }
                else { alert(data.message); }
            }
        </script>
    </body>
    </html>
    """)

@app.route('/student/login', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        data = request.json
        student = Student.query.filter_by(student_id=data['student_id']).first()
        if student and student.check_password(data['password']):
            if not is_valid_email(student.email):
                return jsonify({'success': False, 'message': f'Access restricted to {ALLOWED_EMAIL_DOMAIN} accounts only.'}), 403
            if not student.is_approved:
                return jsonify({'success': False, 'message': 'Your account is pending admin approval. Please try again later.'}), 403
            session['student_id'] = student.id
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Invalid credentials'}), 401
    
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Student Login</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
            .box { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); width: 100%; max-width: 400px; }
            h2 { color: #f5576c; margin-bottom: 30px; text-align: center; }
            input { width: 100%; padding: 12px; margin-bottom: 20px; border: 2px solid #ddd; border-radius: 6px; }
            button { width: 100%; padding: 12px; background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
            .links { margin-top: 20px; text-align: center; }
            .links a { color: #f5576c; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="box">
            <h2>Student Login</h2>
            <form onsubmit="login(event)">
                <input type="text" id="student_id" placeholder="Student ID" required>
                <input type="password" id="password" placeholder="Password" required>
                <button type="submit">Login</button>
            </form>
            <div class="links"><p>New student? <a href="/student/register">Register here</a></p></div>
        </div>
        <script>
            async function login(e) {
                e.preventDefault();
                const response = await fetch('/student/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        student_id: document.getElementById('student_id').value,
                        password: document.getElementById('password').value
                    })
                });
                const data = await response.json();
                if (data.success) { window.location.href = '/student-portal'; }
                else { alert(data.message); }
            }
        </script>
    </body>
    </html>
    """)

@app.route('/student-portal')
def student_portal():
    if 'student_id' not in session:
        return redirect(url_for('student_login'))
    student = Student.query.get(session['student_id'])
    if not student:
        session.pop('student_id', None)
        return redirect(url_for('student_login'))
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Student Portal</title>
        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', sans-serif; background: #f5f5f5; }
            .navbar { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; padding: 20px; display: flex; justify-content: space-between; align-items: center; }
            .container { max-width: 800px; margin: 40px auto; padding: 0 20px; text-align: center; }
            .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
            #reader { border: 3px solid #f5576c; border-radius: 12px; overflow: hidden; margin-top: 20px; }
            .btn { padding: 12px 25px; border-radius: 6px; border: none; font-weight: 600; cursor: pointer; text-decoration: none; display: inline-block; margin-top: 20px; }
            .btn-primary { background: #f5576c; color: white; }
            .result { padding: 15px; border-radius: 8px; margin-top: 20px; display: none; }
            .success { background: #d4edda; color: #155724; }
            .error { background: #f8d7da; color: #721c24; }
        </style>
    </head>
    <body>
        <div class="navbar"><h1>👤 Student Portal</h1><a href="/logout" style="color:white; text-decoration:none;">Logout</a></div>
        <div class="container">
            <div class="card">
                <h2>Welcome, {{ student.name }}!</h2>
                <p>Student ID: {{ student.student_id }}</p>
                <hr style="margin: 20px 0; opacity: 0.2;">
                <h3>My Marks (Internal & Assignments)</h3>
                <div id="marks-display" style="margin-top:20px;">
                    <p>Loading marks...</p>
                </div>
                <hr style="margin: 20px 0; opacity: 0.2;">
                <h3>Scan Session QR to Mark Attendance</h3>
                <div id="reader"></div>
                <div id="result" class="result"></div>
                <button id="start-btn" onclick="startScanner()" class="btn btn-primary">Start Scanner</button>
            </div>
        </div>
        <script>
            let html5QrcodeScanner = null;
            let currentLat = null, currentLng = null;

            function startScanner() {
                if ("geolocation" in navigator) {
                    navigator.geolocation.getCurrentPosition(p => {
                        currentLat = p.coords.latitude;
                        currentLng = p.coords.longitude;
                        document.getElementById('start-btn').style.display = 'none';
                        html5QrcodeScanner = new Html5Qrcode("reader");
                        html5QrcodeScanner.start({ facingMode: "environment" }, { fps: 10, qrbox: 250 }, onScanSuccess);
                    }, e => alert("Location access is required for attendance!"));
                }
            }

            async function onScanSuccess(decodedText) {
                if (html5QrcodeScanner) await html5QrcodeScanner.stop();
                const response = await fetch('/api/student/scan-attendance', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ session_code: decodedText, latitude: currentLat, longitude: currentLng })
                });
                const data = await response.json();
                const resDiv = document.getElementById('result');
                resDiv.textContent = data.message;
                resDiv.className = 'result ' + (data.success ? 'success' : 'error');
                resDiv.style.display = 'block';
                document.getElementById('start-btn').style.display = 'inline-block';
            }

            loadMyMarks();
            async function loadMyMarks() {
                const response = await fetch('/api/student/my-marks');
                const data = await response.json();
                const display = document.getElementById('marks-display');
                if (!data.marks || data.marks.length === 0) {
                    display.innerHTML = '<p>No marks recorded yet.</p>';
                    return;
                }
                let html = '<table style="width:100%; border-collapse: collapse; margin-top:10px;">';
                html += '<tr style="background:#f8f9fa;"><th style="padding:10px; border:1px solid #ddd;">Subject</th><th style="padding:10px; border:1px solid #ddd;">Internal</th><th style="padding:10px; border:1px solid #ddd;">Assignment</th><th style="padding:10px; border:1px solid #ddd;">Total</th></tr>';
                data.marks.forEach(m => {
                    html += `<tr style="border-bottom:1px solid #ddd;"><td style="padding:10px; border:1px solid #ddd;">${m.subject}</td><td style="padding:10px; border:1px solid #ddd;">${m.internal_marks}</td><td style="padding:10px; border:1px solid #ddd;">${m.assignment_marks}</td><td style="padding:10px; border:1px solid #ddd;">${m.total_marks}</td></tr>`;
                });
                display.innerHTML = html + '</table>';
            }
        </script>
    </body>
    </html>
    """, student=student)
@app.route('/api/student/scan-attendance', methods=['POST'])
def student_scan_attendance():
    if 'student_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        
    data = request.json
    session_code = data.get('session_code')
    lat = data.get('latitude')
    lng = data.get('longitude')
    
    student = Student.query.get(session['student_id'])
    if not student:
        return jsonify({'success': False, 'message': 'Student not found'}), 404
        
    if lat is None or lng is None:
        return jsonify({'success': False, 'message': 'Location access is required!'}), 400
        
    session_obj = AttendanceSession.query.filter_by(session_code=session_code).first()
    if not session_obj:
        return jsonify({'success': False, 'message': 'Invalid Session QR Code'}), 404
        
    if not session_obj.is_ongoing():
        return jsonify({'success': False, 'message': 'This session is not currently active'}), 400
        
    existing = AttendanceRecord.query.filter_by(student_id=student.id, session_id=session_obj.id).first()
    if existing:
        return jsonify({'success': False, 'message': 'You have already marked attendance for this session'}), 400
        
    distance = calculate_distance(lat, lng, session_obj.latitude, session_obj.longitude)
    
    if distance > session_obj.radius:
        status = 'absent'
        message = f'Marked ABSENT (Out of range: {int(distance)}m)'
    else:
        grace_period = timedelta(minutes=15)
        if datetime.now() > session_obj.start_time + grace_period:
            status = 'late'
            message = 'Attendance marked LATE'
        else:
            status = 'present'
            message = 'Attendance marked successfully'
            
    record = AttendanceRecord(
        student_id=student.id, session_id=session_obj.id,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent', '')[:200],
        latitude=lat, longitude=lng, status=status
    )
    db.session.add(record)
    db.session.commit()
    
    return jsonify({'success': True, 'message': message, 'status': status})

@app.route('/scan')
@login_required
def scan_page():
    return render_template_string(SCANNER_PAGE)
@app.route('/api/sessions/active')
def get_active_sessions():
    sessions = AttendanceSession.query.filter_by(is_active=True).all()
    active_sessions = [s for s in sessions if s.is_ongoing()]
    return jsonify({'sessions': [s.to_dict() for s in active_sessions]})


@app.route('/api/mark-attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    qr_token = data.get('qr_token')
    session_id = data.get('session_id')
    lat = data.get('latitude')
    lng = data.get('longitude')

    if lat is None or lng is None:
        return jsonify({'success': False, 'message': 'Location access is strictly required for attendance marking'}), 400

    student = Student.query.filter_by(qr_token=qr_token).first()
    if not student:
        return jsonify({'success': False, 'message': 'Invalid QR code'}), 404

    session_obj = AttendanceSession.query.get(session_id)
    if not session_obj or not session_obj.is_ongoing():
        return jsonify({'success': False, 'message': 'Session is not active'}), 400

    existing = AttendanceRecord.query.filter_by(student_id=student.id, session_id=session_id).first()
    if existing:
        return jsonify({'success': False, 'message': f'{student.name} already marked present'}), 400

    distance = calculate_distance(lat, lng, session_obj.latitude, session_obj.longitude)

    if distance > session_obj.radius:
        status = 'absent'
        message = f'Scanned successfully, but marked ABSENT for {student.name} (Out of location range by {int(distance)}m)'
    else:
        grace_period = timedelta(minutes=15)
        if datetime.now() > session_obj.start_time + grace_period:
            status = 'late'
            message = f'Attendance marked LATE for {student.name}'
        else:
            status = 'present'
            message = f'Attendance marked for {student.name}'

    record = AttendanceRecord(
        student_id=student.id, session_id=session_id,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent', '')[:200],
        latitude=lat, longitude=lng, status=status
    )
    db.session.add(record)
    db.session.commit()

    return jsonify({'success': True, 'message': message, 'student': student.to_dict(), 'status': record.status})


@app.route('/api/students/pending', methods=['GET'])
@login_required
def get_pending_students():
    students = Student.query.filter_by(is_approved=False).all()
    return jsonify({'students': [s.to_dict() for s in students]})

@app.route('/api/students/<int:student_id>/approve', methods=['POST'])
@login_required
def approve_student(student_id):
    student = Student.query.get_or_404(student_id)
    student.is_approved = True
    db.session.commit()
    return jsonify({'success': True, 'message': f'Student {student.name} approved'})

@app.route('/api/marks', methods=['GET', 'POST'])
@login_required
def manage_marks():
    if request.method == 'GET':
        student_id = request.args.get('student_id')
        if student_id:
            marks = Mark.query.filter_by(student_id=student_id).all()
        else:
            marks = Mark.query.all()
        return jsonify({'marks': [m.to_dict() for m in marks]})
    
    elif request.method == 'POST':
        admin = Admin.query.get(session['admin_id'])
        if not admin.is_staff:
            return jsonify({'success': False, 'message': 'Staff verification (ID card scan) required to upload/edit marks'}), 403
            
        data = request.json
        student_id = data.get('student_id')
        subject = data.get('subject')
        internal = float(data.get('internal_marks', 0))
        assignment = float(data.get('assignment_marks', 0))
        
        mark = Mark.query.filter_by(student_id=student_id, subject=subject).first()
        if mark:
            mark.internal_marks = internal
            mark.assignment_marks = assignment
        else:
            mark = Mark(student_id=student_id, subject=subject, internal_marks=internal, assignment_marks=assignment)
            db.session.add(mark)
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Marks updated successfully'})

# Removed redundant /api/marks/update endpoint

@app.route('/api/student/my-marks', methods=['GET'])
def get_my_marks():
    if 'student_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    marks = Mark.query.filter_by(student_id=session['student_id']).all()
    return jsonify({'marks': [m.to_dict() for m in marks]})

@app.route('/api/admin/verify-staff', methods=['POST'])
@login_required
def verify_staff():
    data = request.json
    token = data.get('token')
    if token and token.startswith('STAFF-'):
        admin = Admin.query.get(session['admin_id'])
        admin.is_staff = True
        db.session.commit()
        return jsonify({'success': True, 'message': 'Staff identity confirmed'})
    return jsonify({'success': False, 'message': 'Invalid Staff ID card'})


@app.route('/api/session/<int:session_id>/attendance')
def get_session_attendance(session_id):
    records = AttendanceRecord.query.filter_by(session_id=session_id).all()
    return jsonify({'records': [r.to_dict() for r in records]})


@app.route('/api/attendance')
@login_required
def get_all_attendance():
    records = AttendanceRecord.query.order_by(AttendanceRecord.check_in_time.desc()).all()
    return jsonify({'records': [r.to_dict_admin() for r in records]})


@app.route('/api/analytics')
@login_required
def get_analytics():
    total_students = Student.query.count()
    total_sessions = AttendanceSession.query.count()
    total_attendance = AttendanceRecord.query.count()
    if total_sessions > 0 and total_students > 0:
        avg_attendance = (total_attendance / (total_sessions * total_students)) * 100
    else:
        avg_attendance = 0
    return jsonify({'total_students': total_students, 'total_sessions': total_sessions, 'total_attendance': total_attendance, 'avg_attendance': round(avg_attendance, 1)})


@app.route('/export/students')
@login_required
def export_students():
    students = Student.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Student ID', 'Name', 'Email', 'QR Token', 'Created At'])
    for student in students:
        writer.writerow([student.student_id, student.name, student.email, student.qr_token, student.created_at.strftime('%Y-%m-%d %H:%M:%S')])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name=f'students_{datetime.now().strftime("%Y%m%d")}.csv')


@app.route('/export/attendance')
@login_required
def export_attendance():
    records = AttendanceRecord.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Student ID', 'Student Name', 'Session', 'Check-in Time', 'Status', 'Location'])
    for record in records:
        writer.writerow([record.student.student_id, record.student.name, record.session.session_name, record.check_in_time.strftime('%Y-%m-%d %H:%M:%S'), record.status, f"{record.latitude}, {record.longitude}" if record.latitude is not None else "N/A"])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name=f'attendance_{datetime.now().strftime("%Y%m%d")}.csv')


# ==================== INITIALIZATION ====================

def init_database():
    with app.app_context():
        db.create_all()
        
        # Ensure Student table has password_hash column
        for table, col, col_type in [
            ('student', 'password_hash', 'VARCHAR(128)'),
            ('student', 'is_approved', 'BOOLEAN DEFAULT 0'),
            ('admin', 'is_staff', 'BOOLEAN DEFAULT 0'),
            ('attendance_session', 'period_number', 'INTEGER DEFAULT 0'),
            ('attendance_session', 'latitude', 'FLOAT DEFAULT 0.0'),
            ('attendance_session', 'longitude', 'FLOAT DEFAULT 0.0'),
            ('attendance_session', 'radius', 'FLOAT DEFAULT 50.0'),
            ('attendance_record', 'latitude', 'FLOAT'),
            ('attendance_record', 'longitude', 'FLOAT'),
            ('attendance_record', 'status', 'VARCHAR(20) DEFAULT "present"')
        ]:
            try:
                db.session.execute(text(f'SELECT {col} FROM {table} LIMIT 1'))
            except Exception:
                db.session.rollback()
                try:
                    db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}'))
                    db.session.commit()
                    print(f"Added {col} column to {table} table.")
                except Exception as e:
                    print(f"Error updating database ({table}.{col}): {e}")
                    db.session.rollback()

        if not Admin.query.first():
            admin = Admin(username='admin', email='admin@nprcolleges.org')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("Default admin created - Username: admin, Password: admin123")


# ==================== MAIN ====================

if __name__ == '__main__':
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, io.UnsupportedOperation):
        pass
    init_database()
    print("\n" + "="*60)
    print("QR Code Attendance System Started!")
    print("="*60)
    print("Scanner: http://localhost:5000/scan")
    print("Admin:   http://localhost:5000/admin")
    print("  Username: admin")
    print("  Password: admin123")
    print("="*60 + "\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
