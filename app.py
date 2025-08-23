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
        
        # User interactions (likes, views, etc.)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_interactions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                interaction_type TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (video_id) REFERENCES videos (id)
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
        
        interaction_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO user_interactions (id, user_id, video_id, interaction_type)
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

# Initialize sample data
def init_sample_data():
    """Initialize sample data for demonstration"""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    cursor = conn.cursor()
    
    # Check if data exists
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        # Create sample users with proper password hashing
        sample_users = [
            ("creator1", "creator@scalevid.com", "creator123", UserRole.CREATOR),
            ("testuser", "user@scalevid.com", "user123", UserRole.CONSUMER)
        ]
        
        for username, email, password, role in sample_users:
            db_manager.create_user(username, email, password, role)
        
        # Get the creator ID for sample videos
        cursor.execute("SELECT id FROM users WHERE username = 'creator1'")
        creator_id = cursor.fetchone()[0]
        
        # Create sample videos
        sample_videos = [
            ('Introduction to Python Programming', 'Learn Python basics in this comprehensive tutorial', creator_id, 'education'),
            ('Amazing Travel Destinations', 'Explore the world\'s most beautiful places', creator_id, 'travel'),
            ('Cooking Masterclass: Italian Cuisine', 'Master the art of Italian cooking', creator_id, 'cooking'),
            ('Tech Review: Latest Smartphones', 'Comprehensive review of 2025 smartphones', creator_id, 'technology'),
            ('Fitness Workout for Beginners', 'Get started with your fitness journey', creator_id, 'fitness')
        ]
        
        for title, desc, creator, genre in sample_videos:
            video_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO videos (id, title, description, creator_id, file_path, genre, view_count, like_count, sentiment_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (video_id, title, desc, creator, f"sample_{video_id}.mp4", genre, 
                 np.random.randint(50, 5000), np.random.randint(5, 500), np.random.uniform(-0.5, 1.0)))
        
        conn.commit()
    
    conn.close()

init_sample_data()

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
        dcc.Store(id="upload-status-store", data={}),  # Store for upload status
        dcc.Interval(id="interval-component", interval=10000, n_intervals=0)
    ])

def create_nav_tabs(user_role):
    """Create navigation tabs based on user role"""
    tabs = [
        dcc.Tab(label="Dashboard", value="dashboard"),
        dcc.Tab(label="Browse Videos", value="browse")
    ]
    
    if user_role == UserRole.CREATOR.value:
        tabs.extend([
            dcc.Tab(label="My Videos", value="my-videos"),
            dcc.Tab(label="Upload", value="upload"),
            dcc.Tab(label="Analytics", value="creator-analytics")
        ])
    
    return tabs

def create_dashboard_content(user_data):
    """Create personalized dashboard"""
    if user_data['role'] == UserRole.CONSUMER.value:
        # Get recommendations
        recommendations = recommendation_engine.get_recommendations(user_data['id'], 6)
        
        return html.Div([
            html.H2("Recommended for You"),
            create_video_grid(recommendations),
            
            html.H2("Trending Now"),
            html.Div(id="dashboard-trending-videos")
        ])
    
    elif user_data['role'] == UserRole.CREATOR.value:
        return html.Div([
            html.H2("Creator Dashboard"),
            html.Div(id="creator-summary"),
            html.Div([
                html.Div(id="recent-uploads", className="dashboard-section"),
                html.Div(id="performance-summary", className="dashboard-section")
            ], className="dashboard-grid")
        ])

def create_video_grid(videos):
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
    [Input('main-tabs', 'value')],
    [State('user-store', 'data'),
     State('upload-status-store', 'data')]
)
def update_main_content(active_tab, user_data, upload_status):
    """Update main content based on selected tab"""
    if not user_data:
        return html.Div("Please login to continue.")
    
    if active_tab == 'dashboard':
        return create_dashboard_content(user_data)
    
    elif active_tab == 'browse':
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
                        {'label': 'Fitness', 'value': 'fitness'}
                    ],
                    value='all',
                    className="genre-filter"
                )
            ], className="search-controls"),
            
            html.Div(id='video-browse-results')
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
    return create_video_grid(videos_df.to_dict('records'))

@app.callback(
    Output('dashboard-trending-videos', 'children'),
    [Input('interval-component', 'n_intervals')],
    [State('user-store', 'data')]
)
def update_dashboard_trending(n_intervals, user_data):
    """Update trending videos on dashboard"""
    trending_df = db_manager.get_videos()
    if not trending_df.empty:
        trending_df['engagement_score'] = trending_df['view_count'] + trending_df['like_count'] * 2
        trending_videos = trending_df.nlargest(6, 'engagement_score').to_dict('records')
        return create_video_grid(trending_videos)
    else:
        return html.Div("No trending videos available.")

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

@app.callback(
    Output('creator-summary', 'children'),
    [Input('interval-component', 'n_intervals')],
    [State('user-store', 'data')]
)
def update_creator_summary(n_intervals, user_data):
    """Update creator dashboard summary"""
    if not user_data or user_data['role'] != UserRole.CREATOR.value:
        return html.Div()
    
    videos_df = db_manager.get_videos({'creator_id': user_data['id']})
    
    if videos_df.empty:
        return html.Div([
            html.P("Welcome to your creator dashboard!"),
            html.P("Upload your first video to get started with analytics.")
        ])
    
    total_views = videos_df['view_count'].sum()
    total_likes = videos_df['like_count'].sum()
    recent_video = videos_df.iloc[0] if len(videos_df) > 0 else None
    
    return html.Div([
        html.Div([
            html.Div([
                html.H4(f"{len(videos_df)}"),
                html.P("Videos Uploaded")
            ], className="summary-stat"),
            
            html.Div([
                html.H4(f"{total_views:,}"),
                html.P("Total Views")
            ], className="summary-stat"),
            
            html.Div([
                html.H4(f"{total_likes:,}"),
                html.P("Total Likes")
            ], className="summary-stat")
        ], className="summary-stats"),
        
        html.Div([
            html.H4("Latest Video"),
            html.P(f"'{recent_video['title']}' - {recent_video['view_count']} views") if recent_video is not None else html.P("No videos yet")
        ], className="latest-video")
    ])

# Video interaction callbacks
@app.callback(
    Output('url', 'pathname', allow_duplicate=True),
    [Input({'type': 'video-card', 'index': dash.dependencies.ALL}, 'n_clicks')],
    [State('user-store', 'data')],
    prevent_initial_call=True
)
def handle_video_click(n_clicks_list, user_data):
    """Handle video card clicks and track interactions"""
    if not any(n_clicks_list) or not user_data:
        return dash.no_update
    
    # Find which video was clicked
    ctx = callback_context
    if ctx.triggered:
        video_id = ctx.triggered[0]['prop_id'].split('"index":"')[1].split('"')[0]
        
        # Track the view interaction
        analytics_engine.track_interaction(user_data['id'], video_id, 'view')
    
    return dash.no_update

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

# CSS Styles
css_styles = """
/* Custom CSS Styles */
.auth-container {
    max-width: 400px;
    margin: 50px auto;
    padding: 20px;
    border: 1px solid #ddd;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.auth-title {
    text-align: center;
    color: #333;
    margin-bottom: 30px;
}

.auth-form {
    padding: 20px 0;
}

.auth-input, .auth-dropdown {
    width: 100%;
    margin-bottom: 15px;
    padding: 10px;
    border: 1px solid #ccc;
    border-radius: 4px;
    font-size: 14px;
}

.auth-button {
    width: 100%;
    padding: 12px;
    background-color: #007bff;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 16px;
    cursor: pointer;
    transition: background-color 0.2s;
}

.auth-button:hover {
    background-color: #0056b3;
}

.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 15px 30px;
    background-color: #f8f9fa;
    border-bottom: 2px solid #dee2e6;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.header-title {
    color: #007bff;
    margin: 0;
}

.user-info {
    margin-right: 15px;
    font-weight: 500;
    color: #495057;
}

.logout-button {
    padding: 8px 16px;
    background-color: #dc3545;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    transition: background-color 0.2s;
}

.logout-button:hover {
    background-color: #c82333;
}

.main-content {
    padding: 30px;
    min-height: calc(100vh - 200px);
}

.video-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 25px;
    padding: 20px 0;
}

.video-card {
    border: 1px solid #e0e0e0;
    border-radius: 12px;
    overflow: hidden;
    cursor: pointer;
    transition: all 0.3s ease;
    background: white;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.video-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 8px 25px rgba(0,0,0,0.15);
    border-color: #007bff;
}

.thumbnail-container {
    position: relative;
    background-color: #f8f9fa;
    height: 180px;
    display: flex;
    align-items: center;
    justify-content: center;
}

.video-thumbnail {
    max-width: 100%;
    max-height: 100%;
    object-fit: cover;
}

.video-duration {
    position: absolute;
    bottom: 8px;
    right: 8px;
    background-color: rgba(0,0,0,0.8);
    color: white;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 12px;
}

.video-info {
    padding: 15px;
}

.video-title {
    margin: 0 0 8px 0;
    color: #333;
    font-size: 16px;
    font-weight: 600;
    line-height: 1.3;
}

.video-creator {
    margin: 0 0 10px 0;
    color: #666;
    font-size: 13px;
}

.video-stats {
    display: flex;
    gap: 15px;
    margin-bottom: 10px;
}

.video-stat {
    font-size: 12px;
    color: #888;
}

.video-genre {
    font-size: 12px;
    color: #007bff;
    font-weight: 500;
}

.video-description {
    margin: 0;
    color: #666;
    font-size: 13px;
    line-height: 1.4;
}

.search-controls {
    display: flex;
    gap: 15px;
    margin-bottom: 25px;
    align-items: center;
}

.search-input {
    flex: 2;
    padding: 10px 15px;
    border: 1px solid #ccc;
    border-radius: 6px;
    font-size: 14px;
}

.genre-filter {
    flex: 1;
    min-width: 200px;
}

.upload-area {
    border: 3px dashed #ccc;
    border-radius: 12px;
    padding: 60px 40px;
    text-align: center;
    margin-bottom: 25px;
    background-color: #fafafa;
    transition: all 0.3s ease;
    cursor: pointer;
}

.upload-area:hover {
    border-color: #007bff;
    background-color: #f0f8ff;
}

.form-input, .form-textarea, .form-dropdown {
    width: 100%;
    margin-bottom: 15px;
    padding: 12px;
    border: 1px solid #ccc;
    border-radius: 6px;
    font-size: 14px;
    transition: border-color 0.2s;
}

.form-input:focus, .form-textarea:focus {
    outline: none;
    border-color: #007bff;
    box-shadow: 0 0 5px rgba(0,123,255,0.2);
}

.form-textarea {
    min-height: 100px;
    resize: vertical;
}

.upload-button {
    width: 100%;
    padding: 12px;
    background-color: #28a745;
    color: white;
    border: none;
    border-radius: 6px;
    font-size: 16px;
    font-weight: 500;
    cursor: pointer;
    transition: background-color 0.2s;
}

.upload-button:hover {
    background-color: #218838;
}

.metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 20px;
    margin-bottom: 30px;
}

.metric-card {
    padding: 25px 20px;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    text-align: center;
    background: white;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    transition: transform 0.2s;
}

.metric-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 8px rgba(0,0,0,0.15);
}

.metric-card h3 {
    margin: 0 0 10px 0;
    color: #007bff;
    font-size: 2em;
    font-weight: bold;
}

.metric-card p {
    margin: 0;
    color: #666;
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.dashboard-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 30px;
    margin-top: 20px;
}

.summary-stats {
    display: flex;
    gap: 20px;
    margin-bottom: 25px;
}

.summary-stat {
    padding: 20px;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    text-align: center;
    flex: 1;
    background: white;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.summary-stat h4 {
    margin: 0 0 8px 0;
    color: #007bff;
    font-size: 1.5em;
}

.summary-stat p {
    margin: 0;
    color: #666;
    font-size: 13px;
}

.latest-video {
    padding: 20px;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    background: white;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.latest-video h4 {
    margin: 0 0 10px 0;
    color: #333;
}

.latest-video p {
    margin: 0;
    color: #666;
}

.success-message {
    color: #28a745;
    font-weight: 500;
    margin: 10px 0;
}

.error-message {
    color: #dc3545;
    font-weight: 500;
    margin: 10px 0;
}

.no-videos {
    text-align: center;
    color: #666;
    font-style: italic;
    padding: 40px 20px;
}

/* Responsive design */
@media (max-width: 768px) {
    .header {
        flex-direction: column;
        padding: 10px 20px;
        gap: 10px;
    }
    
    .search-controls {
        flex-direction: column;
    }
    
    .video-grid {
        grid-template-columns: 1fr;
        gap: 20px;
    }
    
    .dashboard-grid {
        grid-template-columns: 1fr;
        gap: 20px;
    }
    
    .summary-stats {
        flex-direction: column;
        gap: 15px;
    }
    
    .metrics-grid {
        grid-template-columns: repeat(2, 1fr);
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
    print("Sample accounts:")
    print("- Consumer: username='testuser', password='user123'")
    print("- Creator: username='creator1', password='creator123'")
    
    # Start the application
    app.run_server(
        debug=True,
        host='0.0.0.0',
        port=8050
    )