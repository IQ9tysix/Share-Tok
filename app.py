import dash
from dash import dcc, html, Input, Output, State, callback_context
from dash.dash_table import DataTable
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import base64
import io
import os
import uuid
import hashlib
from typing import Dict, List, Optional
import logging
import sqlite3
import time
import threading
from dataclasses import dataclass
from enum import Enum

# Configuration
class Config:
    UPLOAD_FOLDER = 'uploads'
    DATABASE_PATH = 'video_platform.db'
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB for demo
    ALLOWED_VIDEO_FORMATS = {'.mp4', '.avi', '.mov', '.wmv'}

# Data Models
class UserRole(Enum):
    CONSUMER = "consumer"
    CREATOR = "creator"

class VideoStatus(Enum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

@dataclass
class User:
    id: str
    username: str
    email: str
    password_hash: str
    role: UserRole
    created_at: datetime
    is_active: bool = True

@dataclass
class Video:
    id: str
    title: str
    description: str
    creator_id: str
    file_path: str
    duration: float
    file_size: int
    format: str
    upload_date: datetime
    status: VideoStatus
    view_count: int = 0
    like_count: int = 0
    dislike_count: int = 0
    genre: str = ""
    age_rating: str = "PG"
    sentiment_score: float = 0.0
    content_tags: list = None

# Database Manager
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if users table exists and has correct schema
        cursor.execute("PRAGMA table_info(users)")
        existing_columns = [row[1] for row in cursor.fetchall()]
        
        if 'password_hash' not in existing_columns:
            cursor.execute("DROP TABLE IF EXISTS users")
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Videos table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                creator_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                duration REAL DEFAULT 0.0,
                file_size INTEGER,
                format TEXT,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'ready',
                view_count INTEGER DEFAULT 0,
                like_count INTEGER DEFAULT 0,
                dislike_count INTEGER DEFAULT 0,
                genre TEXT DEFAULT '',
                age_rating TEXT DEFAULT 'PG',
                sentiment_score REAL DEFAULT 0.0,
                content_tags TEXT DEFAULT '[]',
                FOREIGN KEY (creator_id) REFERENCES users (id)
            )
        ''')
        
        # Comments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                sentiment_score REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # User interactions (likes, views, saves, etc.)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_interactions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                interaction_type TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (video_id) REFERENCES videos (id),
                UNIQUE(user_id, video_id, interaction_type)
            )
        ''')
        
        # Saved videos table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS saved_videos (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (video_id) REFERENCES videos (id),
                UNIQUE(user_id, video_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def create_user(self, username: str, email: str, password: str, role: UserRole) -> str:
        """Create a new user account"""
        user_id = str(uuid.uuid4())
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO users (id, username, email, password_hash, role)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, email, password_hash, role.value))
            conn.commit()
            return user_id
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict]:
        """Authenticate user login"""
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, username, email, role, is_active
            FROM users
            WHERE username = ? AND password_hash = ? AND is_active = TRUE
        """, (username, password_hash))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'id': result[0],
                'username': result[1],
                'email': result[2],
                'role': result[3],
                'is_active': result[4]
            }
        return None
    
    def get_videos(self, filters: Dict = None) -> pd.DataFrame:
        """Get videos with optional filters"""
        conn = sqlite3.connect(self.db_path)
        
        query = """
            SELECT v.*, u.username as creator_name
            FROM videos v
            JOIN users u ON v.creator_id = u.id
            WHERE v.status = 'ready'
        """
        params = []
        
        if filters:
            if filters.get('genre') and filters['genre'] != 'all':
                query += " AND v.genre = ?"
                params.append(filters['genre'])
            
            if filters.get('search'):
                query += " AND (v.title LIKE ? OR v.description LIKE ?)"
                search_term = f"%{filters['search']}%"
                params.extend([search_term, search_term])
            
            if filters.get('creator_id'):
                query += " AND v.creator_id = ?"
                params.append(filters['creator_id'])
        
        query += " ORDER BY v.upload_date DESC"
        
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        return df
    
    def get_video_by_id(self, video_id: str) -> Optional[Dict]:
        """Get a specific video by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT v.*, u.username as creator_name
            FROM videos v
            JOIN users u ON v.creator_id = u.id
            WHERE v.id = ? AND v.status = 'ready'
        """, (video_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            columns = ['id', 'title', 'description', 'creator_id', 'file_path', 'duration',
                      'file_size', 'format', 'upload_date', 'status', 'view_count', 
                      'like_count', 'dislike_count', 'genre', 'age_rating', 
                      'sentiment_score', 'content_tags', 'creator_name']
            return dict(zip(columns, result))
        return None
    
    def get_user_saved_videos(self, user_id: str) -> pd.DataFrame:
        """Get user's saved videos"""
        conn = sqlite3.connect(self.db_path)
        
        query = """
            SELECT v.*, u.username as creator_name, sv.saved_at
            FROM saved_videos sv
            JOIN videos v ON sv.video_id = v.id
            JOIN users u ON v.creator_id = u.id
            WHERE sv.user_id = ? AND v.status = 'ready'
            ORDER BY sv.saved_at DESC
        """
        
        df = pd.read_sql(query, conn, params=[user_id])
        conn.close()
        return df
    
    def check_user_interaction(self, user_id: str, video_id: str, interaction_type: str) -> bool:
        """Check if user has already performed this interaction"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) FROM user_interactions
            WHERE user_id = ? AND video_id = ? AND interaction_type = ?
        """, (user_id, video_id, interaction_type))
        
        result = cursor.fetchone()[0] > 0
        conn.close()
        return result
    
    def check_video_saved(self, user_id: str, video_id: str) -> bool:
        """Check if video is saved by user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) FROM saved_videos
            WHERE user_id = ? AND video_id = ?
        """, (user_id, video_id))
        
        result = cursor.fetchone()[0] > 0
        conn.close()
        return result

# Advanced Features Implementation

# 1. Content Analysis and Recommendation Engine
class RecommendationEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    def get_recommendations(self, user_id: str, limit: int = 10) -> List[Dict]:
        """Get personalized video recommendations based on user behavior"""
        conn = sqlite3.connect(self.db.db_path)
        
        # Get user's interaction history
        user_history = pd.read_sql("""
            SELECT v.genre, v.sentiment_score, COUNT(*) as interaction_count
            FROM user_interactions ui
            JOIN videos v ON ui.video_id = v.id
            WHERE ui.user_id = ? AND ui.interaction_type IN ('view', 'like')
            GROUP BY v.genre, v.sentiment_score
            ORDER BY interaction_count DESC
        """, conn, params=[user_id])
        
        if user_history.empty:
            # Return trending videos for new users
            trending = pd.read_sql("""
                SELECT v.*, u.username as creator_name
                FROM videos v
                JOIN users u ON v.creator_id = u.id
                WHERE v.status = 'ready'
                ORDER BY (v.view_count + v.like_count * 2) DESC
                LIMIT ?
            """, conn, params=[limit])
        else:
            # Content-based filtering
            preferred_genres = user_history['genre'].tolist()
            avg_sentiment = user_history['sentiment_score'].mean()
            
            genre_filter = "'" + "','".join(preferred_genres) + "'"
            
            trending = pd.read_sql(f"""
                SELECT v.*, u.username as creator_name,
                       ABS(v.sentiment_score - ?) as sentiment_diff
                FROM videos v
                JOIN users u ON v.creator_id = u.id
                WHERE v.status = 'ready' AND v.genre IN ({genre_filter})
                ORDER BY sentiment_diff ASC, v.view_count DESC
                LIMIT ?
            """, conn, params=[avg_sentiment, limit])
        
        conn.close()
        return trending.to_dict('records')

# 2. Real-time Analytics Dashboard
class AnalyticsEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    def track_interaction(self, user_id: str, video_id: str, interaction_type: str):
        """Track user interactions for analytics"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        try:
            interaction_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT OR IGNORE INTO user_interactions (id, user_id, video_id, interaction_type)
                VALUES (?, ?, ?, ?)
            """, (interaction_id, user_id, video_id, interaction_type))
            
            # Update video metrics
            if interaction_type == 'view':
                cursor.execute("UPDATE videos SET view_count = view_count + 1 WHERE id = ?", (video_id,))
            elif interaction_type == 'like':
                cursor.execute("UPDATE videos SET like_count = like_count + 1 WHERE id = ?", (video_id,))
            elif interaction_type == 'dislike':
                cursor.execute("UPDATE videos SET dislike_count = dislike_count + 1 WHERE id = ?", (video_id,))
            
            conn.commit()
        except sqlite3.IntegrityError:
            # Interaction already exists
            pass
        finally:
            conn.close()
    
    def remove_interaction(self, user_id: str, video_id: str, interaction_type: str):
        """Remove user interaction (for unlike, etc.)"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM user_interactions
            WHERE user_id = ? AND video_id = ? AND interaction_type = ?
        """, (user_id, video_id, interaction_type))
        
        # Update video metrics
        if interaction_type == 'like':
            cursor.execute("UPDATE videos SET like_count = CASE WHEN like_count > 0 THEN like_count - 1 ELSE 0 END WHERE id = ?", (video_id,))
        elif interaction_type == 'dislike':
            cursor.execute("UPDATE videos SET dislike_count = CASE WHEN dislike_count > 0 THEN dislike_count - 1 ELSE 0 END WHERE id = ?", (video_id,))
        
        conn.commit()
        conn.close()
    
    def save_video(self, user_id: str, video_id: str):
        """Save video for user"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        try:
            save_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO saved_videos (id, user_id, video_id)
                VALUES (?, ?, ?)
            """, (save_id, user_id, video_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def unsave_video(self, user_id: str, video_id: str):
        """Remove video from saved list"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM saved_videos
            WHERE user_id = ? AND video_id = ?
        """, (user_id, video_id))
        
        conn.commit()
        conn.close()

# 3. Sentiment Analysis for Comments
class SentimentAnalyzer:
    def __init__(self):
        # Simple rule-based sentiment analysis for demo
        self.positive_words = ['good', 'great', 'awesome', 'love', 'amazing', 'excellent', 'fantastic']
        self.negative_words = ['bad', 'terrible', 'hate', 'awful', 'horrible', 'worst', 'boring']
    
    def analyze_sentiment(self, text: str) -> float:
        """Analyze sentiment of text (-1 to 1 scale)"""
        if not text:
            return 0.0
        
        text_lower = text.lower()
        positive_count = sum(1 for word in self.positive_words if word in text_lower)
        negative_count = sum(1 for word in self.negative_words if word in text_lower)
        
        total_words = len(text.split())
        if total_words == 0:
            return 0.0
        
        # Simple calculation
        sentiment = (positive_count - negative_count) / max(total_words, 1)
        return max(-1, min(1, sentiment * 5))  # Scale and clamp

# Initialize components
db_manager = DatabaseManager(Config.DATABASE_PATH)
recommendation_engine = RecommendationEngine(db_manager)
analytics_engine = AnalyticsEngine(db_manager)
sentiment_analyzer = SentimentAnalyzer()

# Create directories
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

# Initialize Dash app
app = dash.Dash(__name__, suppress_callback_exceptions=True)

# Add this line to expose the server for gunicorn
server = app.server

# Layout Components
def create_auth_layout():
    """Create authentication layout"""
    return html.Div([
        html.Div([
            html.H1("ScaleVid Platform", className="auth-title"),
            
            # Login/Register toggle
            dcc.Tabs(id="auth-tabs", value="login", children=[
                dcc.Tab(label="Login", value="login", children=[
                    html.Div([
                        dcc.Input(id="login-username", type="text", placeholder="Username", className="auth-input"),
                        dcc.Input(id="login-password", type="password", placeholder="Password", className="auth-input"),
                        html.Button("Login", id="login-btn", className="auth-button"),
                        html.Div(id="login-message", className="auth-message")
                    ], className="auth-form")
                ]),
                
                dcc.Tab(label="Register", value="register", children=[
                    html.Div([
                        dcc.Input(id="reg-username", type="text", placeholder="Username", className="auth-input"),
                        dcc.Input(id="reg-email", type="email", placeholder="Email", className="auth-input"),
                        dcc.Input(id="reg-password", type="password", placeholder="Password", className="auth-input"),
                        dcc.Dropdown(
                            id="reg-role",
                            options=[
                                {'label': 'Consumer (Watch videos)', 'value': 'consumer'},
                                {'label': 'Creator (Upload videos)', 'value': 'creator'}
                            ],
                            placeholder="Select Role",
                            className="auth-dropdown"
                        ),
                        html.Button("Register", id="register-btn", className="auth-button"),
                        html.Div(id="register-message", className="auth-message")
                    ], className="auth-form")
                ])
            ])
        ], className="auth-container")
    ])

def create_main_layout(user_data):
    """Create main application layout based on user role"""
    return html.Div([
        # Header
        html.Div([
            html.H1("ScaleVid Platform", className="header-title"),
            html.Div([
                html.Span(f"Welcome, {user_data['username']} ({user_data['role'].title()})", className="user-info"),
                html.Button("Logout", id="logout-btn", className="logout-button")
            ], className="header-controls")
        ], className="header"),
        
        # Navigation
        html.Div([
            dcc.Tabs(id="main-tabs", value="dashboard", children=create_nav_tabs(user_data['role']))
        ]),
        
        # Content
        html.Div(id="main-content", className="main-content"),
        
        # Stores
        dcc.Store(id="user-store", data=user_data),
        dcc.Store(id="upload-status-store", data={}),
        dcc.Store(id="current-video-store", data={}),
        dcc.Interval(id="interval-component", interval=10000, n_intervals=0)
    ])

def create_nav_tabs(user_role):
    """Create navigation tabs based on user role"""
    tabs = [
        dcc.Tab(label="Browse Videos", value="browse")
    ]
    
    if user_role == UserRole.CONSUMER.value:
        tabs.extend([
            dcc.Tab(label="Saved Videos", value="saved")
        ])
    
    if user_role == UserRole.CREATOR.value:
        tabs.extend([
            dcc.Tab(label="My Videos", value="my-videos"),
            dcc.Tab(label="Upload", value="upload"),
            dcc.Tab(label="Analytics", value="creator-analytics")
        ])
    
    return tabs

def create_video_grid(videos, user_data=None):
    """Create video grid display"""
    if not videos:
        return html.Div("No videos found.", className="no-videos")
    
    video_cards = []
    for video in videos:
        card = html.Div([
            html.Div([
                html.Img(src="/assets/video-thumbnail.png", className="video-thumbnail"),
                html.Div(f"{video.get('duration', 0):.0f}s", className="video-duration")
            ], className="thumbnail-container"),
            
            html.Div([
                html.H4(video['title'], className="video-title"),
                html.P(f"By {video['creator_name']}", className="video-creator"),
                html.Div([
                    html.Span(f"👁 {video['view_count']:,}", className="video-stat"),
                    html.Span(f"👍 {video['like_count']:,}", className="video-stat"),
                    html.Span(f"🏷 {video['genre']}", className="video-genre")
                ], className="video-stats"),
                html.P(video['description'][:100] + "..." if len(video['description']) > 100 else video['description'], 
                      className="video-description")
            ], className="video-info")
        ], 
        className="video-card", 
        id={'type': 'video-card', 'index': video['id']},
        n_clicks=0)
        
        video_cards.append(card)
    
    return html.Div(video_cards, className="video-grid")

def create_video_player_layout(video_data, user_data):
    """Create video player layout"""
    if not video_data:
        return html.Div("Video not found.", className="error-message")
    
    # Check user interactions
    is_liked = db_manager.check_user_interaction(user_data['id'], video_data['id'], 'like')
    is_disliked = db_manager.check_user_interaction(user_data['id'], video_data['id'], 'dislike')
    is_saved = db_manager.check_video_saved(user_data['id'], video_data['id'])
    
    return html.Div([
        html.Button("← Back to Browse", id="back-to-browse", className="back-button"),
        
        html.Div([
            # Video Player Section
            html.Div([
                html.Video(
                    src=f"/uploads/{os.path.basename(video_data['file_path'])}",
                    controls=True,
                    className="video-player",
                    style={'width': '100%', 'maxHeight': '500px'}
                ),
                
                html.Div([
                    html.H2(video_data['title'], className="video-player-title"),
                    html.P(f"By {video_data['creator_name']} • {video_data['view_count']:,} views • {video_data['genre']}", 
                          className="video-player-info"),
                    
                    # Interaction buttons
                    html.Div([
                        html.Button(
                            f"👍 {video_data['like_count']:,}",
                            id="like-btn",
                            className=f"interaction-btn {'liked' if is_liked else ''}",
                            **{'data-video-id': video_data['id']}
                        ),
                        html.Button(
                            f"👎 {video_data['dislike_count']:,}",
                            id="dislike-btn",
                            className=f"interaction-btn {'disliked' if is_disliked else ''}",
                            **{'data-video-id': video_data['id']}
                        ),
                        html.Button(
                            "💾 Save" if not is_saved else "✅ Saved",
                            id="save-btn",
                            className=f"interaction-btn {'saved' if is_saved else ''}",
                            **{'data-video-id': video_data['id']}
                        )
                    ], className="interaction-buttons"),
                    
                    html.Div([
                        html.H4("Description"),
                        html.P(video_data['description'])
                    ], className="video-description-section")
                ], className="video-player-details")
            ], className="video-player-container"),
            
            # Comments Section
            html.Div([
                html.H4("Comments"),
                html.Div([
                    dcc.Textarea(
                        id="comment-input",
                        placeholder="Add a comment...",
                        className="comment-input"
                    ),
                    html.Button("Post Comment", id="post-comment-btn", className="post-comment-btn")
                ], className="comment-form"),
                
                html.Div(id="comments-list", className="comments-list")
            ], className="comments-section")
        ], className="video-page-content")
    ])

# Main layout
app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    dcc.Store(id='session-store', storage_type='session'),
    html.Div(id='app-content')
])

# Callbacks

@app.callback(
    Output('app-content', 'children'),
    [Input('url', 'pathname'),
     Input('session-store', 'data')]
)
def display_page(pathname, session_data):
    """Main page router"""
    if not session_data or not session_data.get('authenticated'):
        return create_auth_layout()
    else:
        return create_main_layout(session_data['user'])

@app.callback(
    [Output('session-store', 'data'),
     Output('login-message', 'children')],
    [Input('login-btn', 'n_clicks')],
    [State('login-username', 'value'),
     State('login-password', 'value')],
    prevent_initial_call=True
)
def handle_login(n_clicks, username, password):
    """Handle user login"""
    if not n_clicks or not username or not password:
        return dash.no_update, ""
    
    user = db_manager.authenticate_user(username, password)
    if user:
        return {
            'authenticated': True,
            'user': user
        }, ""
    else:
        return dash.no_update, html.Div("Invalid credentials", className="error-message")

@app.callback(
    [Output('session-store', 'data', allow_duplicate=True),
     Output('register-message', 'children')],
    [Input('register-btn', 'n_clicks')],
    [State('reg-username', 'value'),
     State('reg-email', 'value'),
     State('reg-password', 'value'),
     State('reg-role', 'value')],
    prevent_initial_call=True
)
def handle_register(n_clicks, username, email, password, role):
    """Handle user registration"""
    if not n_clicks or not all([username, email, password, role]):
        return dash.no_update, ""
    
    user_role = UserRole(role)
    user_id = db_manager.create_user(username, email, password, user_role)
    
    if user_id:
        user_data = {
            'id': user_id,
            'username': username,
            'email': email,
            'role': role,
            'is_active': True
        }
        return {
            'authenticated': True,
            'user': user_data
        }, ""
    else:
        return dash.no_update, html.Div("Registration failed. Username or email may already exist.", className="error-message")

@app.callback(
    Output('session-store', 'data', allow_duplicate=True),
    [Input('logout-btn', 'n_clicks')],
    prevent_initial_call=True
)
def handle_logout(n_clicks):
    """Handle user logout"""
    if n_clicks:
        return {}
    return dash.no_update

@app.callback(
    Output('main-content', 'children'),
    [Input('main-tabs', 'value'),
     Input('back-to-browse', 'n_clicks')],
    [State('user-store', 'data'),
     State('upload-status-store', 'data'),
     State('current-video-store', 'data')]
)
def update_main_content(active_tab, back_clicks, user_data, upload_status, current_video):
    """Update main content based on selected tab"""
    if not user_data:
        return html.Div("Please login to continue.")
    
    ctx = callback_context
    if ctx.triggered and ctx.triggered[0]['prop_id'] == 'back-to-browse.n_clicks':
        active_tab = 'browse'
    
    # Check if we should show video player
    if current_video and current_video.get('show_player'):
        video_data = db_manager.get_video_by_id(current_video['video_id'])
        if video_data:
            return create_video_player_layout(video_data, user_data)
    
    if active_tab == 'browse':
        return html.Div([
            html.H2("Browse Videos"),
            
            # Search and filters
            html.Div([
                dcc.Input(id='search-input', type='text', placeholder='Search videos...', className="search-input"),
                dcc.Dropdown(
                    id='genre-filter',
                    options=[
                        {'label': 'All Genres', 'value': 'all'},
                        {'label': 'Education', 'value': 'education'},
                        {'label': 'Technology', 'value': 'technology'},
                        {'label': 'Travel', 'value': 'travel'},
                        {'label': 'Cooking', 'value': 'cooking'},
                        {'label': 'Fitness', 'value': 'fitness'},
                        {'label': 'Entertainment', 'value': 'entertainment'}
                    ],
                    value='all',
                    className="genre-filter"
                )
            ], className="search-controls"),
            
            html.Div(id='video-browse-results')
        ])
    
    elif active_tab == 'saved':
        saved_videos_df = db_manager.get_user_saved_videos(user_data['id'])
        return html.Div([
            html.H2("Saved Videos"),
            create_video_grid(saved_videos_df.to_dict('records'), user_data) if not saved_videos_df.empty 
            else html.Div("No saved videos yet.", className="no-videos")
        ])
    
    elif active_tab == 'upload' and user_data['role'] == UserRole.CREATOR.value:
        return html.Div([
            html.H2("Upload Video"),
            
            # Display upload status if exists
            html.Div(id="upload-status-display"),
            
            html.Div([
                dcc.Upload(
                    id='upload-video',
                    children=html.Div([
                        'Drag and Drop or ',
                        html.A('Select Files')
                    ]),
                    className="upload-area",
                    multiple=False,
                    accept='video/*'
                ),
                
                html.Div([
                    dcc.Input(id='video-title', type='text', placeholder='Video Title', className="form-input"),
                    dcc.Textarea(id='video-description', placeholder='Video Description', className="form-textarea"),
                    dcc.Dropdown(
                        id='video-genre',
                        options=[
                            {'label': 'Education', 'value': 'education'},
                            {'label': 'Technology', 'value': 'technology'},
                            {'label': 'Travel', 'value': 'travel'},
                            {'label': 'Cooking', 'value': 'cooking'},
                            {'label': 'Fitness', 'value': 'fitness'},
                            {'label': 'Entertainment', 'value': 'entertainment'}
                        ],
                        placeholder='Select Genre',
                        className="form-dropdown"
                    ),
                    dcc.Dropdown(
                        id='video-rating',
                        options=[
                            {'label': 'G - General Audience', 'value': 'G'},
                            {'label': 'PG - Parental Guidance', 'value': 'PG'},
                            {'label': '12+ - Ages 12 and up', 'value': '12+'},
                            {'label': '18+ - Adults only', 'value': '18+'}
                        ],
                        value='PG',
                        className="form-dropdown"
                    ),
                    html.Button("Upload Video", id="upload-submit", className="upload-button")
                ], id="upload-form", style={'display': 'none'})
            ])
        ])
    
    elif active_tab == 'my-videos' and user_data['role'] == UserRole.CREATOR.value:
        my_videos_df = db_manager.get_videos({'creator_id': user_data['id']})
        return html.Div([
            html.H2("My Videos"),
            html.Div([
                DataTable(
                    data=my_videos_df.to_dict('records'),
                    columns=[
                        {'name': 'Title', 'id': 'title'},
                        {'name': 'Genre', 'id': 'genre'},
                        {'name': 'Views', 'id': 'view_count', 'type': 'numeric'},
                        {'name': 'Likes', 'id': 'like_count', 'type': 'numeric'},
                        {'name': 'Upload Date', 'id': 'upload_date'},
                        {'name': 'Status', 'id': 'status'}
                    ],
                    style_cell={'textAlign': 'left'},
                    style_data_conditional=[
                        {
                            'if': {'filter_query': '{status} = ready'},
                            'backgroundColor': '#d4edda'
                        }
                    ]
                ) if not my_videos_df.empty else html.Div("No videos uploaded yet.")
            ])
        ])
    
    elif active_tab == 'creator-analytics' and user_data['role'] == UserRole.CREATOR.value:
        return html.Div([
            html.H2("Creator Analytics"),
            html.Div([
                html.Div(id='creator-metrics-cards', className="metrics-grid"),
                dcc.Graph(id='creator-performance-chart')
            ])
        ])
    
    else:
        return html.Div("Page not found or insufficient permissions.")

@app.callback(
    Output('video-browse-results', 'children'),
    [Input('search-input', 'value'),
     Input('genre-filter', 'value'),
     Input('interval-component', 'n_intervals')],
    [State('user-store', 'data')]
)
def update_video_browse(search_query, genre_filter, n_intervals, user_data):
    """Update video browse results"""
    filters = {}
    if search_query:
        filters['search'] = search_query
    if genre_filter and genre_filter != 'all':
        filters['genre'] = genre_filter
    
    videos_df = db_manager.get_videos(filters)
    return create_video_grid(videos_df.to_dict('records'), user_data)

# Video interaction callbacks
@app.callback(
    [Output('current-video-store', 'data'),
     Output('main-tabs', 'value')],
    [Input({'type': 'video-card', 'index': dash.dependencies.ALL}, 'n_clicks')],
    [State('user-store', 'data'),
     State('current-video-store', 'data')],
    prevent_initial_call=True
)
def handle_video_click(n_clicks_list, user_data, current_video_store):
    """Handle video card clicks and track interactions"""
    if not any(n_clicks_list) or not user_data:
        return dash.no_update, dash.no_update
    
    # Find which video was clicked
    ctx = callback_context
    if ctx.triggered:
        video_id = ctx.triggered[0]['prop_id'].split('"index":"')[1].split('"')[0]
        
        # Track the view interaction
        analytics_engine.track_interaction(user_data['id'], video_id, 'view')
        
        # Set current video and show player
        return {'video_id': video_id, 'show_player': True}, 'browse'
    
    return dash.no_update, dash.no_update

# Like/Dislike/Save callbacks
@app.callback(
    [Output('like-btn', 'children'),
     Output('like-btn', 'className'),
     Output('dislike-btn', 'children'),
     Output('dislike-btn', 'className')],
    [Input('like-btn', 'n_clicks'),
     Input('dislike-btn', 'n_clicks')],
    [State('user-store', 'data'),
     State('current-video-store', 'data')],
    prevent_initial_call=True
)
def handle_like_dislike(like_clicks, dislike_clicks, user_data, current_video_store):
    """Handle like/dislike interactions"""
    if not user_data or not current_video_store:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    
    video_id = current_video_store['video_id']
    ctx = callback_context
    
    if not ctx.triggered:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    # Get current video data
    video_data = db_manager.get_video_by_id(video_id)
    if not video_data:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    
    # Check current interaction states
    is_liked = db_manager.check_user_interaction(user_data['id'], video_id, 'like')
    is_disliked = db_manager.check_user_interaction(user_data['id'], video_id, 'dislike')
    
    if trigger_id == 'like-btn':
        if is_liked:
            # Unlike
            analytics_engine.remove_interaction(user_data['id'], video_id, 'like')
            is_liked = False
        else:
            # Like (remove dislike if exists)
            if is_disliked:
                analytics_engine.remove_interaction(user_data['id'], video_id, 'dislike')
                is_disliked = False
            analytics_engine.track_interaction(user_data['id'], video_id, 'like')
            is_liked = True
    
    elif trigger_id == 'dislike-btn':
        if is_disliked:
            # Remove dislike
            analytics_engine.remove_interaction(user_data['id'], video_id, 'dislike')
            is_disliked = False
        else:
            # Dislike (remove like if exists)
            if is_liked:
                analytics_engine.remove_interaction(user_data['id'], video_id, 'like')
                is_liked = False
            analytics_engine.track_interaction(user_data['id'], video_id, 'dislike')
            is_disliked = True
    
    # Get updated video data
    updated_video_data = db_manager.get_video_by_id(video_id)
    
    like_text = f"👍 {updated_video_data['like_count']:,}"
    dislike_text = f"👎 {updated_video_data['dislike_count']:,}"
    
    like_class = f"interaction-btn {'liked' if is_liked else ''}"
    dislike_class = f"interaction-btn {'disliked' if is_disliked else ''}"
    
    return like_text, like_class, dislike_text, dislike_class

@app.callback(
    [Output('save-btn', 'children'),
     Output('save-btn', 'className')],
    [Input('save-btn', 'n_clicks')],
    [State('user-store', 'data'),
     State('current-video-store', 'data')],
    prevent_initial_call=True
)
def handle_save_video(save_clicks, user_data, current_video_store):
    """Handle save/unsave video"""
    if not user_data or not current_video_store or not save_clicks:
        return dash.no_update, dash.no_update
    
    video_id = current_video_store['video_id']
    is_saved = db_manager.check_video_saved(user_data['id'], video_id)
    
    if is_saved:
        # Unsave video
        analytics_engine.unsave_video(user_data['id'], video_id)
        return "💾 Save", "interaction-btn"
    else:
        # Save video
        analytics_engine.save_video(user_data['id'], video_id)
        return "✅ Saved", "interaction-btn saved"

@app.callback(
    [Output('upload-form', 'style'),
     Output('upload-status-store', 'data'),
     Output('upload-status-display', 'children')],
    [Input('upload-video', 'contents'),
     Input('upload-submit', 'n_clicks')],
    [State('upload-video', 'filename'),
     State('video-title', 'value'),
     State('video-description', 'value'),
     State('video-genre', 'value'),
     State('video-rating', 'value'),
     State('user-store', 'data'),
     State('upload-status-store', 'data')],
    prevent_initial_call=True
)
def handle_video_upload(contents, submit_clicks, filename, title, description, genre, rating, user_data, current_status):
    """Handle video upload process"""
    ctx = callback_context
    
    if not ctx.triggered:
        return dash.no_update, dash.no_update, dash.no_update
    
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    # Show form when file is selected
    if trigger_id == 'upload-video' and contents:
        if not filename:
            return dash.no_update, current_status, html.Div("Please select a valid file.", className="error-message")
        
        # Validate file type
        if not any(filename.lower().endswith(ext) for ext in Config.ALLOWED_VIDEO_FORMATS):
            return dash.no_update, current_status, html.Div(f"Unsupported file format. Allowed: {', '.join(Config.ALLOWED_VIDEO_FORMATS)}", className="error-message")
        
        status_message = html.Div(f"File selected: {filename}", className="success-message")
        return {'display': 'block'}, {'file_selected': True, 'filename': filename}, status_message
    
    # Handle form submission
    if trigger_id == 'upload-submit' and submit_clicks and contents:
        if not all([title, description, genre, rating]):
            return dash.no_update, current_status, html.Div("Please fill in all fields.", className="error-message")
        
        try:
            # Decode and validate file
            content_type, content_string = contents.split(',')
            decoded = base64.b64decode(content_string)
            
            if len(decoded) > Config.MAX_FILE_SIZE:
                return dash.no_update, current_status, html.Div("File too large. Maximum size: 100MB", className="error-message")
            
            # Save file
            video_id = str(uuid.uuid4())
            file_extension = filename.split('.')[-1].lower()
            file_path = os.path.join(Config.UPLOAD_FOLDER, f"{video_id}.{file_extension}")
            
            with open(file_path, 'wb') as f:
                f.write(decoded)
            
            # Simple content analysis for demo
            content_tags = []
            if 'tutorial' in title.lower() or 'learn' in description.lower():
                content_tags.append('educational')
            if 'fun' in title.lower() or 'entertainment' in description.lower():
                content_tags.append('entertaining')
            
            # Analyze sentiment of description
            sentiment_score = sentiment_analyzer.analyze_sentiment(description)
            
            # Create database entry
            conn = sqlite3.connect(Config.DATABASE_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO videos (id, title, description, creator_id, file_path, 
                                  file_size, format, genre, age_rating, sentiment_score, content_tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_id, title, description, user_data['id'], file_path,
                len(decoded), file_extension, genre, rating, sentiment_score, json.dumps(content_tags)
            ))
            
            conn.commit()
            conn.close()
            
            success_status = {
                'upload_complete': True,
                'video_title': title,
                'sentiment_score': sentiment_score,
                'content_tags': content_tags
            }
            
            success_message = html.Div([
                html.H4("Upload Successful!", className="success-message"),
                html.P(f"Video '{title}' has been uploaded and is ready for viewing."),
                html.P(f"Sentiment Analysis Score: {sentiment_score:.2f}"),
                html.P(f"Content Tags: {', '.join(content_tags) if content_tags else 'None'}"),
                html.Button("Upload Another Video", id="upload-another-btn", className="upload-button", style={'margin-top': '10px'})
            ])
            
            return {'display': 'none'}, success_status, success_message
            
        except Exception as e:
            error_message = html.Div(f"Upload failed: {str(e)}", className="error-message")
            return dash.no_update, current_status, error_message
    
    return dash.no_update, current_status, dash.no_update

@app.callback(
    [Output('creator-metrics-cards', 'children'),
     Output('creator-performance-chart', 'figure')],
    [Input('interval-component', 'n_intervals')],
    [State('user-store', 'data')]
)
def update_creator_analytics(n_intervals, user_data):
    """Update creator analytics dashboard"""
    if not user_data or user_data['role'] != UserRole.CREATOR.value:
        return [], {}
    
    # Get creator's video performance
    videos_df = db_manager.get_videos({'creator_id': user_data['id']})
    
    if videos_df.empty:
        return [html.Div("No videos available for analysis.")], {}
    
    # Calculate metrics
    total_videos = len(videos_df)
    total_views = videos_df['view_count'].sum()
    total_likes = videos_df['like_count'].sum()
    avg_sentiment = videos_df['sentiment_score'].mean()
    
    # Create metrics cards
    metrics_cards = [
        html.Div([
            html.H3(str(total_videos)),
            html.P("Total Videos")
        ], className="metric-card"),
        
        html.Div([
            html.H3(f"{total_views:,}"),
            html.P("Total Views")
        ], className="metric-card"),
        
        html.Div([
            html.H3(f"{total_likes:,}"),
            html.P("Total Likes")
        ], className="metric-card"),
        
        html.Div([
            html.H3(f"{avg_sentiment:.2f}"),
            html.P("Avg. Sentiment")
        ], className="metric-card")
    ]
    
    # Create performance chart
    fig = go.Figure()
    
    # Add views bar chart
    fig.add_trace(go.Bar(
        name='Views',
        x=videos_df['title'],
        y=videos_df['view_count'],
        yaxis='y',
        offsetgroup=1
    ))
    
    # Add likes bar chart
    fig.add_trace(go.Bar(
        name='Likes',
        x=videos_df['title'],
        y=videos_df['like_count'],
        yaxis='y',
        offsetgroup=2
    ))
    
    # Add sentiment line chart on secondary axis
    fig.add_trace(go.Scatter(
        name='Sentiment Score',
        x=videos_df['title'],
        y=videos_df['sentiment_score'],
        yaxis='y2',
        mode='lines+markers',
        line=dict(color='orange', width=3)
    ))
    
    fig.update_layout(
        title='Video Performance Analytics',
        xaxis=dict(title='Videos'),
        yaxis=dict(title='Count', side='left'),
        yaxis2=dict(title='Sentiment Score', side='right', overlaying='y', range=[-1, 1]),
        barmode='group',
        height=400,
        hovermode='x unified'
    )
    
    return metrics_cards, fig

# Reset upload form callback
@app.callback(
    [Output('upload-video', 'contents'),
     Output('video-title', 'value'),
     Output('video-description', 'value'),
     Output('video-genre', 'value'),
     Output('video-rating', 'value')],
    [Input('upload-another-btn', 'n_clicks')],
    prevent_initial_call=True
)
def reset_upload_form(n_clicks):
    """Reset upload form for new upload"""
    if n_clicks:
        return None, "", "", None, "PG"
    return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

# Comments functionality
@app.callback(
    Output('comments-list', 'children'),
    [Input('post-comment-btn', 'n_clicks'),
     Input('interval-component', 'n_intervals')],
    [State('comment-input', 'value'),
     State('user-store', 'data'),
     State('current-video-store', 'data')],
    prevent_initial_call=True
)
def handle_comments(post_clicks, n_intervals, comment_text, user_data, current_video_store):
    """Handle comment posting and display"""
    if not current_video_store or not current_video_store.get('video_id'):
        return []
    
    video_id = current_video_store['video_id']
    
    ctx = callback_context
    if ctx.triggered and ctx.triggered[0]['prop_id'] == 'post-comment-btn.n_clicks' and post_clicks:
        if comment_text and comment_text.strip() and user_data:
            # Add comment to database
            conn = sqlite3.connect(Config.DATABASE_PATH)
            cursor = conn.cursor()
            
            comment_id = str(uuid.uuid4())
            sentiment_score = sentiment_analyzer.analyze_sentiment(comment_text)
            
            cursor.execute("""
                INSERT INTO comments (id, video_id, user_id, content, sentiment_score)
                VALUES (?, ?, ?, ?, ?)
            """, (comment_id, video_id, user_data['id'], comment_text.strip(), sentiment_score))
            
            conn.commit()
            conn.close()
    
    # Load and display comments
    conn = sqlite3.connect(Config.DATABASE_PATH)
    comments_df = pd.read_sql("""
        SELECT c.*, u.username
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.video_id = ?
        ORDER BY c.created_at DESC
    """, conn, params=[video_id])
    conn.close()
    
    if comments_df.empty:
        return [html.Div("No comments yet. Be the first to comment!", className="no-comments")]
    
    comment_elements = []
    for _, comment in comments_df.iterrows():
        comment_elements.append(
            html.Div([
                html.Div([
                    html.Strong(comment['username']),
                    html.Span(f" • {comment['created_at']}", className="comment-time")
                ], className="comment-header"),
                html.P(comment['content'], className="comment-content"),
                html.Div([
                    html.Span(f"Sentiment: {comment['sentiment_score']:.2f}", className="comment-sentiment")
                ], className="comment-meta")
            ], className="comment-item")
        )
    
    return comment_elements

@app.callback(
    Output('comment-input', 'value'),
    [Input('post-comment-btn', 'n_clicks')],
    [State('comment-input', 'value')],
    prevent_initial_call=True
)
def clear_comment_input(n_clicks, comment_text):
    """Clear comment input after posting"""
    if n_clicks and comment_text:
        return ""
    return dash.no_update


# CSS Styles
css_styles = """
/* Custom CSS Styles */
/* TikTok-Style CSS for ScaleVid Platform */

/* Import modern fonts */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

/* CSS Variables for TikTok-like theme */
:root {
    --primary-bg: #000000;
    --secondary-bg: #161823;
    --card-bg: #212332;
    --accent-color: #fe2c55;
    --accent-hover: #ff1744;
    --secondary-accent: #25f4ee;
    --text-primary: #ffffff;
    --text-secondary: #a8a8b3;
    --text-muted: #6a6b74;
    --border-color: #2f2f2f;
    --success-color: #25d366;
    --warning-color: #ffab00;
    --error-color: #ff3b30;
    --gradient-primary: linear-gradient(135deg, #fe2c55 0%, #ff6b35 100%);
    --gradient-secondary: linear-gradient(135deg, #25f4ee 0%, #7c3aed 100%);
    --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.3);
    --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.4);
    --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.5);
    --border-radius: 12px;
    --border-radius-sm: 8px;
    --border-radius-lg: 20px;
    --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    --header-height: 70px;
}

/* Global Reset and Base Styles */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--primary-bg);
    color: var(--text-primary);
    line-height: 1.6;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

/* Scrollbar Styling */
::-webkit-scrollbar {
    width: 8px;
}

::-webkit-scrollbar-track {
    background: var(--secondary-bg);
}

::-webkit-scrollbar-thumb {
    background: var(--accent-color);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--accent-hover);
}

/* Authentication Layout */
.auth-container {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #000000 0%, #161823 50%, #000000 100%);
    position: relative;
    overflow: hidden;
}

.auth-container::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: 
        radial-gradient(circle at 20% 80%, rgba(254, 44, 85, 0.15) 0%, transparent 50%),
        radial-gradient(circle at 80% 20%, rgba(37, 244, 238, 0.15) 0%, transparent 50%);
    animation: float 20s ease-in-out infinite alternate;
}

@keyframes float {
    0% { transform: translateY(0px) rotate(0deg); }
    100% { transform: translateY(-20px) rotate(2deg); }
}

.auth-title {
    font-size: 3rem;
    font-weight: 900;
    text-align: center;
    margin-bottom: 2rem;
    background: var(--gradient-primary);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.02em;
}

/* Tabs Styling */
.dash-table-container .Select-control,
._dash-undo-redo {
    background: var(--card-bg) !important;
}

[id*="auth-tabs"] .tab {
    background: transparent !important;
    border: 2px solid transparent !important;
    color: var(--text-secondary) !important;
    font-weight: 600 !important;
    padding: 12px 24px !important;
    margin: 0 8px !important;
    border-radius: var(--border-radius) !important;
    transition: var(--transition) !important;
}

[id*="auth-tabs"] .tab--selected {
    background: var(--gradient-primary) !important;
    color: white !important;
    border-color: var(--accent-color) !important;
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
}

[id*="main-tabs"] .tab {
    background: var(--secondary-bg) !important;
    border: 2px solid var(--border-color) !important;
    color: var(--text-secondary) !important;
    font-weight: 600 !important;
    padding: 12px 20px !important;
    margin: 0 4px !important;
    border-radius: var(--border-radius-sm) !important;
    transition: var(--transition) !important;
}

[id*="main-tabs"] .tab--selected {
    background: var(--accent-color) !important;
    color: white !important;
    border-color: var(--accent-color) !important;
    transform: translateY(-1px);
    box-shadow: var(--shadow-sm);
}

/* Form Elements */
.auth-form {
    background: var(--card-bg);
    padding: 3rem;
    border-radius: var(--border-radius-lg);
    box-shadow: var(--shadow-lg);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.1);
    min-width: 400px;
    position: relative;
    z-index: 1;
}

.auth-input,
.form-input,
.search-input {
    width: 100%;
    padding: 16px 20px;
    margin-bottom: 16px;
    background: var(--secondary-bg);
    border: 2px solid var(--border-color);
    border-radius: var(--border-radius);
    color: var(--text-primary);
    font-size: 16px;
    font-weight: 500;
    transition: var(--transition);
    outline: none;
}

.auth-input:focus,
.form-input:focus,
.search-input:focus {
    border-color: var(--accent-color);
    box-shadow: 0 0 0 3px rgba(254, 44, 85, 0.1);
    transform: translateY(-1px);
}

.auth-input::placeholder,
.form-input::placeholder,
.search-input::placeholder {
    color: var(--text-muted);
}

/* Textarea */
.form-textarea,
.comment-input {
    width: 100%;
    padding: 16px 20px;
    margin-bottom: 16px;
    background: var(--secondary-bg);
    border: 2px solid var(--border-color);
    border-radius: var(--border-radius);
    color: var(--text-primary);
    font-size: 16px;
    font-weight: 500;
    transition: var(--transition);
    outline: none;
    resize: vertical;
    min-height: 100px;
    font-family: inherit;
}

.form-textarea:focus,
.comment-input:focus {
    border-color: var(--accent-color);
    box-shadow: 0 0 0 3px rgba(254, 44, 85, 0.1);
}

/* Dropdown Styling */
.auth-dropdown .Select-control,
.form-dropdown .Select-control,
.genre-filter .Select-control {
    background: var(--secondary-bg) !important;
    border: 2px solid var(--border-color) !important;
    border-radius: var(--border-radius) !important;
    padding: 8px !important;
    color: var(--text-primary) !important;
    min-height: 56px !important;
    transition: var(--transition) !important;
}

.auth-dropdown .Select-control:hover,
.form-dropdown .Select-control:hover,
.genre-filter .Select-control:hover {
    border-color: var(--accent-color) !important;
}

.auth-dropdown .Select-menu-outer,
.form-dropdown .Select-menu-outer,
.genre-filter .Select-menu-outer {
    background: var(--card-bg) !important;
    border: 2px solid var(--border-color) !important;
    border-radius: var(--border-radius) !important;
    box-shadow: var(--shadow-md) !important;
    z-index: 1000 !important;
}

.auth-dropdown .Select-option,
.form-dropdown .Select-option,
.genre-filter .Select-option {
    background: transparent !important;
    color: var(--text-primary) !important;
    padding: 12px 16px !important;
    transition: var(--transition) !important;
}

.auth-dropdown .Select-option:hover,
.form-dropdown .Select-option:hover,
.genre-filter .Select-option:hover {
    background: var(--accent-color) !important;
    color: white !important;
}

/* Buttons */
.auth-button,
.upload-button,
.post-comment-btn,
.logout-button,
.back-button {
    width: 100%;
    padding: 16px 24px;
    background: var(--gradient-primary);
    border: none;
    border-radius: var(--border-radius);
    color: white;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    transition: var(--transition);
    outline: none;
    position: relative;
    overflow: hidden;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.auth-button:hover,
.upload-button:hover,
.post-comment-btn:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
}

.auth-button:active,
.upload-button:active,
.post-comment-btn:active {
    transform: translateY(0);
}

.logout-button {
    width: auto;
    padding: 8px 16px;
    font-size: 14px;
    background: var(--error-color);
    margin-left: 16px;
}

.back-button {
    width: auto;
    padding: 12px 20px;
    background: var(--secondary-bg);
    color: var(--text-primary);
    border: 2px solid var(--border-color);
    margin-bottom: 20px;
}

/* Header */
.header {
    height: var(--header-height);
    background: var(--secondary-bg);
    border-bottom: 1px solid var(--border-color);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    position: sticky;
    top: 0;
    z-index: 100;
    backdrop-filter: blur(20px);
}

.header-title {
    font-size: 1.8rem;
    font-weight: 900;
    background: var(--gradient-primary);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

.header-controls {
    display: flex;
    align-items: center;
}

.user-info {
    color: var(--text-secondary);
    font-weight: 500;
}

/* Main Content */
.main-content {
    min-height: calc(100vh - var(--header-height));
    padding: 24px;
    background: var(--primary-bg);
}

/* Search Controls */
.search-controls {
    display: flex;
    gap: 16px;
    margin-bottom: 24px;
    align-items: center;
    flex-wrap: wrap;
}

.search-input {
    flex: 1;
    min-width: 300px;
    margin-bottom: 0;
}

.genre-filter {
    min-width: 200px;
}

/* Video Grid */
.video-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 20px;
    margin-top: 20px;
}

.video-card {
    background: var(--card-bg);
    border-radius: var(--border-radius);
    overflow: hidden;
    cursor: pointer;
    transition: var(--transition);
    border: 1px solid var(--border-color);
    position: relative;
    group: hover;
}

.video-card:hover {
    transform: translateY(-4px);
    box-shadow: var(--shadow-lg);
    border-color: var(--accent-color);
}

.thumbnail-container {
    position: relative;
    width: 100%;
    height: 200px;
    overflow: hidden;
    background: var(--secondary-bg);
}

.video-thumbnail {
    width: 100%;
    height: 100%;
    object-fit: cover;
    transition: var(--transition);
}

.video-card:hover .video-thumbnail {
    transform: scale(1.05);
}

.video-duration {
    position: absolute;
    bottom: 8px;
    right: 8px;
    background: rgba(0, 0, 0, 0.8);
    color: white;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
}

.video-info {
    padding: 16px;
}

.video-title {
    font-size: 16px;
    font-weight: 700;
    margin-bottom: 8px;
    color: var(--text-primary);
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

.video-creator {
    color: var(--text-secondary);
    font-size: 14px;
    font-weight: 500;
    margin-bottom: 12px;
}

.video-stats {
    display: flex;
    gap: 12px;
    margin-bottom: 12px;
    flex-wrap: wrap;
}

.video-stat {
    color: var(--text-muted);
    font-size: 12px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 4px;
}

.video-genre {
    background: var(--gradient-secondary);
    color: white;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
}

.video-description {
    color: var(--text-secondary);
    font-size: 14px;
    line-height: 1.4;
}

/* Video Player Layout */
.video-page-content {
    max-width: 1200px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 32px;
    align-items: start;
}

.video-player-container {
    background: var(--card-bg);
    border-radius: var(--border-radius);
    padding: 24px;
    border: 1px solid var(--border-color);
}

.video-player {
    width: 100%;
    border-radius: var(--border-radius);
    background: #000;
}

.video-player-title {
    font-size: 24px;
    font-weight: 800;
    margin: 20px 0 8px 0;
    color: var(--text-primary);
}

.video-player-info {
    color: var(--text-secondary);
    font-size: 16px;
    font-weight: 500;
    margin-bottom: 20px;
}

/* Interaction Buttons */
.interaction-buttons {
    display: flex;
    gap: 12px;
    margin-bottom: 24px;
    flex-wrap: wrap;
}

.interaction-btn {
    padding: 12px 20px;
    background: var(--secondary-bg);
    border: 2px solid var(--border-color);
    border-radius: var(--border-radius);
    color: var(--text-primary);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: var(--transition);
    display: flex;
    align-items: center;
    gap: 8px;
}

.interaction-btn:hover {
    border-color: var(--accent-color);
    transform: translateY(-1px);
}

.interaction-btn.liked {
    background: var(--accent-color);
    border-color: var(--accent-color);
    color: white;
}

.interaction-btn.disliked {
    background: var(--error-color);
    border-color: var(--error-color);
    color: white;
}

.interaction-btn.saved {
    background: var(--success-color);
    border-color: var(--success-color);
    color: white;
}

/* Comments Section */
.comments-section {
    background: var(--card-bg);
    border-radius: var(--border-radius);
    padding: 24px;
    border: 1px solid var(--border-color);
}

.comment-form {
    margin-bottom: 24px;
}

.comment-input {
    margin-bottom: 12px;
    min-height: 80px;
}

.post-comment-btn {
    width: auto;
    padding: 10px 20px;
    font-size: 14px;
}

.comments-list {
    max-height: 400px;
    overflow-y: auto;
    padding-right: 8px;
}

.comment-item {
    background: var(--secondary-bg);
    padding: 16px;
    border-radius: var(--border-radius-sm);
    margin-bottom: 12px;
    border-left: 3px solid var(--accent-color);
}

.comment-header {
    display: flex;
    align-items: center;
    margin-bottom: 8px;
}

.comment-time {
    color: var(--text-muted);
    font-size: 12px;
    margin-left: 8px;
}

.comment-content {
    color: var(--text-primary);
    font-size: 14px;
    line-height: 1.5;
    margin-bottom: 8px;
}

.comment-sentiment {
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 600;
}

/* Upload Area */
.upload-area {
    border: 3px dashed var(--border-color);
    border-radius: var(--border-radius);
    padding: 60px 40px;
    text-align: center;
    background: var(--secondary-bg);
    margin-bottom: 24px;
    cursor: pointer;
    transition: var(--transition);
    position: relative;
    overflow: hidden;
}

.upload-area::before {
    content: '📹';
    font-size: 4rem;
    display: block;
    margin-bottom: 16px;
    opacity: 0.5;
}

.upload-area:hover {
    border-color: var(--accent-color);
    background: var(--card-bg);
    transform: translateY(-2px);
}

.upload-area:hover::before {
    opacity: 1;
    transform: scale(1.1);
}

/* Analytics */
.metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 20px;
    margin-bottom: 32px;
}

.metric-card {
    background: var(--card-bg);
    padding: 24px;
    border-radius: var(--border-radius);
    text-align: center;
    border: 1px solid var(--border-color);
    transition: var(--transition);
}

.metric-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
    border-color: var(--accent-color);
}

.metric-card h3 {
    font-size: 2.5rem;
    font-weight: 900;
    color: var(--accent-color);
    margin-bottom: 8px;
}

.metric-card p {
    color: var(--text-secondary);
    font-weight: 600;
    text-transform: uppercase;
    font-size: 12px;
    letter-spacing: 1px;
}

/* Data Table Styling */
.dash-table-container {
    background: var(--card-bg) !important;
    border-radius: var(--border-radius) !important;
    overflow: hidden !important;
    border: 1px solid var(--border-color) !important;
}

.dash-table-container .dash-spreadsheet-container {
    background: transparent !important;
}

.dash-table-container .dash-table {
    background: transparent !important;
    color: var(--text-primary) !important;
}

.dash-table-container .dash-header {
    background: var(--secondary-bg) !important;
    color: var(--text-primary) !important;
    font-weight: 700 !important;
}

.dash-table-container .dash-cell {
    background: transparent !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--border-color) !important;
}

.dash-table-container .dash-cell:hover {
    background: var(--secondary-bg) !important;
}

/* Messages */
.success-message {
    color: var(--success-color);
    background: rgba(37, 211, 102, 0.1);
    padding: 12px 16px;
    border-radius: var(--border-radius-sm);
    border: 1px solid rgba(37, 211, 102, 0.3);
    margin-bottom: 16px;
}

.error-message {
    color: var(--error-color);
    background: rgba(255, 59, 48, 0.1);
    padding: 12px 16px;
    border-radius: var(--border-radius-sm);
    border: 1px solid rgba(255, 59, 48, 0.3);
    margin-bottom: 16px;
}

.no-videos,
.no-comments {
    text-align: center;
    color: var(--text-muted);
    padding: 40px 20px;
    background: var(--card-bg);
    border-radius: var(--border-radius);
    border: 2px dashed var(--border-color);
}

/* Loading States */
.loading {
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 40px;
}

.loading::after {
    content: '';
    width: 40px;
    height: 40px;
    border: 3px solid var(--border-color);
    border-top: 3px solid var(--accent-color);
    border-radius: 50%;
    animation: spin 1s linear infinite;
}

@keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}

/* Responsive Design */
@media (max-width: 768px) {
    .auth-form {
        padding: 2rem;
        min-width: 300px;
        margin: 20px;
    }
    
    .auth-title {
        font-size: 2rem;
    }
    
    .header {
        padding: 0 16px;
    }
    
    .header-title {
        font-size: 1.5rem;
    }
    
    .main-content {
        padding: 16px;
    }
    
    .video-grid {
        grid-template-columns: 1fr;
        gap: 16px;
    }
    
    .video-page-content {
        grid-template-columns: 1fr;
        gap: 20px;
    }
    
    .search-controls {
        flex-direction: column;
    }
    
    .search-input {
        min-width: 100%;
    }
    
    .metrics-grid {
        grid-template-columns: repeat(2, 1fr);
    }
    
    .interaction-buttons {
        justify-content: center;
    }
}

@media (max-width: 480px) {
    .auth-form {
        padding: 1.5rem;
        min-width: 280px;
    }
    
    .metrics-grid {
        grid-template-columns: 1fr;
    }
    
    .video-card {
        margin: 0 -8px;
    }
    
    .header-controls {
        flex-direction: column;
        align-items: flex-end;
        gap: 8px;
    }
    
    .logout-button {
        margin-left: 0;
    }
}

/* Dark mode enhancements */
@media (prefers-color-scheme: dark) {
    :root {
        --primary-bg: #000000;
        --secondary-bg: #111111;
    }
}

/* High contrast mode support */
@media (prefers-contrast: high) {
    :root {
        --border-color: #444444;
        --text-secondary: #cccccc;
    }
}

/* Reduced motion support */
@media (prefers-reduced-motion: reduce) {
    * {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }
}


"""



if __name__ == '__main__':
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("Starting ScaleVid Platform...")
    print("Create your account to get started!")
    print("Creators can upload videos, Consumers can watch and interact with videos.")
    
    # Start the application
    app.run_server(
        debug=True,
        host='0.0.0.0',
        port=8050
    )