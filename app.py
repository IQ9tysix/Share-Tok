import dash
from dash import dcc, html, Input, Output, State, callback_context, ALL
from dash.dash_table import DataTable
import dash_bootstrap_components as dbc
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
from typing import Dict, List, Optional, Union
import logging
import sqlite3
import time
import threading
from dataclasses import dataclass
from enum import Enum
from flask import send_from_directory
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

# Enhanced Database Manager with better error handling
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.ensure_database_directory()
        self.init_database()
    
    def ensure_database_directory(self):
        """Ensure the database directory exists"""
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)
        except Exception as e:
            logging.error(f"Error creating database directory: {e}")
    
    def get_connection(self):
        """Get database connection with error handling and proper configuration"""
        try:
            conn = sqlite3.connect(
                self.db_path,
                timeout=30,  # 30 second timeout
                check_same_thread=False  # Allow use across threads
            )
            conn.execute("PRAGMA foreign_keys = ON")  # Enable foreign keys
            conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
            return conn
        except Exception as e:
            logging.error(f"Database connection error: {e}")
            raise
    
    def reset_database(self):
        """Reset/recreate the entire database - use with caution"""
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
                logging.info("Removed existing database file")
            
            # Remove WAL files if they exist
            for suffix in ['-wal', '-shm']:
                wal_file = self.db_path + suffix
                if os.path.exists(wal_file):
                    os.remove(wal_file)
            
            self.init_database()
            logging.info("Database reset and reinitialized successfully")
        except Exception as e:
            logging.error(f"Error resetting database: {e}")
            raise
    
    def init_database(self):
        """Initialize database with required tables"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                # Create tables in order (respecting foreign key dependencies)
                self._create_users_table(cursor)
                self._create_videos_table(cursor)
                self._create_comments_table(cursor)
                self._create_user_interactions_table(cursor)
                self._create_saved_videos_table(cursor)
                
                conn.commit()
                logging.info("Database initialized successfully")
                
                # Verify tables were created
                self._verify_tables(cursor)
                
                conn.close()
                return  # Success, exit retry loop
                
            except Exception as e:
                logging.error(f"Database initialization attempt {attempt + 1} failed: {e}")
                if conn:
                    try:
                        conn.rollback()
                        conn.close()
                    except:
                        pass
                
                if attempt == max_retries - 1:
                    # Last attempt failed, try to reset
                    logging.warning("All initialization attempts failed, trying to reset database")
                    try:
                        self.reset_database()
                        return
                    except Exception as reset_error:
                        logging.error(f"Database reset also failed: {reset_error}")
                        raise Exception(f"Failed to initialize database after {max_retries} attempts")
    
    def _create_users_table(self, cursor):
        """Create users table"""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('consumer', 'creator')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
    
    def _create_videos_table(self, cursor):
        """Create videos table"""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                creator_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                duration REAL DEFAULT 0.0,
                file_size INTEGER DEFAULT 0,
                format TEXT DEFAULT '',
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'ready' CHECK (status IN ('uploading', 'processing', 'ready', 'failed')),
                view_count INTEGER DEFAULT 0,
                like_count INTEGER DEFAULT 0,
                dislike_count INTEGER DEFAULT 0,
                genre TEXT DEFAULT '',
                age_rating TEXT DEFAULT 'PG' CHECK (age_rating IN ('G', 'PG', '12+', '18+')),
                sentiment_score REAL DEFAULT 0.0,
                content_tags TEXT DEFAULT '[]',
                FOREIGN KEY (creator_id) REFERENCES users (id) ON DELETE CASCADE
            )
        ''')
    
    def _create_comments_table(self, cursor):
        """Create comments table"""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                sentiment_score REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        ''')
    
    def _create_user_interactions_table(self, cursor):
        """Create user interactions table"""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_interactions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                interaction_type TEXT NOT NULL CHECK (interaction_type IN ('view', 'like', 'dislike', 'share')),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE,
                UNIQUE(user_id, video_id, interaction_type)
            )
        ''')
    
    def _create_saved_videos_table(self, cursor):
        """Create saved videos table"""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS saved_videos (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE,
                UNIQUE(user_id, video_id)
            )
        ''')
    
    def _verify_tables(self, cursor):
        """Verify that all required tables exist"""
        required_tables = ['users', 'videos', 'comments', 'user_interactions', 'saved_videos']
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = [row[0] for row in cursor.fetchall()]
        
        missing_tables = set(required_tables) - set(existing_tables)
        if missing_tables:
            raise Exception(f"Missing tables: {missing_tables}")
        
        logging.info(f"All required tables verified: {existing_tables}")
    
    def create_user(self, username: str, email: str, password: str, role: UserRole) -> Optional[str]:
        """Create a new user account with enhanced error handling"""
        if not all([username, email, password]) or not isinstance(role, UserRole):
            logging.warning("Invalid user creation parameters")
            return None
        
        user_id = str(uuid.uuid4())
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO users (id, username, email, password_hash, role, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, username, email, password_hash, role.value, datetime.now(), True))
            
            conn.commit()
            logging.info(f"User created successfully: {username}")
            return user_id
            
        except sqlite3.IntegrityError as e:
            logging.warning(f"User creation failed - integrity error: {e}")
            return None
        except sqlite3.OperationalError as e:
            logging.error(f"Database operational error during user creation: {e}")
            return None
        except Exception as e:
            logging.error(f"Unexpected error during user creation: {e}")
            return None
        finally:
            if conn:
                conn.close()
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict]:
        """Authenticate user login with enhanced error handling"""
        if not username or not password:
            return None
        
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        conn = None
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, username, email, role, is_active, created_at
                FROM users
                WHERE username = ? AND password_hash = ? AND is_active = TRUE
            """, (username, password_hash))
            
            result = cursor.fetchone()
            
            if result:
                return {
                    'id': result[0],
                    'username': result[1],
                    'email': result[2],
                    'role': result[3],
                    'is_active': result[4],
                    'created_at': result[5]
                }
            return None
            
        except sqlite3.OperationalError as e:
            logging.error(f"Database operational error during authentication: {e}")
            return None
        except Exception as e:
            logging.error(f"Authentication error: {e}")
            return None
        finally:
            if conn:
                conn.close()
    
    def add_video(self, title: str, description: str, creator_id: str, file_path: str, 
                  genre: str = '', age_rating: str = 'PG') -> Optional[str]:
        """Add a new video to the database"""
        if not all([title, creator_id, file_path]):
            logging.warning("Invalid video creation parameters")
            return None
        
        video_id = str(uuid.uuid4())
        conn = None
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Get file info
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            file_format = os.path.splitext(file_path)[1].lower()
            
            cursor.execute("""
                INSERT INTO videos (id, title, description, creator_id, file_path, 
                                  file_size, format, genre, age_rating, status, upload_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (video_id, title, description, creator_id, file_path, 
                  file_size, file_format, genre, age_rating, 'ready', datetime.now()))
            
            conn.commit()
            logging.info(f"Video added successfully: {title}")
            return video_id
            
        except sqlite3.IntegrityError as e:
            logging.warning(f"Video creation failed - integrity error: {e}")
            return None
        except Exception as e:
            logging.error(f"Error adding video: {e}")
            return None
        finally:
            if conn:
                conn.close()
    
    def get_videos(self, filters: Dict = None) -> pd.DataFrame:
        """Get videos with optional filters and enhanced error handling"""
        conn = None
        
        try:
            conn = self.get_connection()
            
            query = """
                SELECT v.id, v.title, v.description, v.creator_id, v.file_path, v.duration,
                       v.file_size, v.format, v.upload_date, v.status, v.view_count,
                       v.like_count, v.dislike_count, v.genre, v.age_rating,
                       v.sentiment_score, v.content_tags, u.username as creator_name
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
            logging.info(f"Retrieved {len(df)} videos")
            return df
            
        except sqlite3.OperationalError as e:
            logging.error(f"Database operational error fetching videos: {e}")
            return pd.DataFrame()
        except Exception as e:
            logging.error(f"Error fetching videos: {e}")
            return pd.DataFrame()
        finally:
            if conn:
                conn.close()
    
    def get_video_by_id(self, video_id: str) -> Optional[Dict]:
        """Get a specific video by ID with enhanced error handling"""
        if not video_id:
            return None
        
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT v.id, v.title, v.description, v.creator_id, v.file_path, v.duration,
                       v.file_size, v.format, v.upload_date, v.status, v.view_count,
                       v.like_count, v.dislike_count, v.genre, v.age_rating,
                       v.sentiment_score, v.content_tags, u.username as creator_name
                FROM videos v
                JOIN users u ON v.creator_id = u.id
                WHERE v.id = ? AND v.status = 'ready'
            """, (video_id,))
            
            result = cursor.fetchone()
            
            if result:
                columns = ['id', 'title', 'description', 'creator_id', 'file_path', 'duration',
                          'file_size', 'format', 'upload_date', 'status', 'view_count', 
                          'like_count', 'dislike_count', 'genre', 'age_rating', 
                          'sentiment_score', 'content_tags', 'creator_name']
                return dict(zip(columns, result))
            return None
            
        except sqlite3.OperationalError as e:
            logging.error(f"Database operational error fetching video {video_id}: {e}")
            return None
        except Exception as e:
            logging.error(f"Error fetching video {video_id}: {e}")
            return None
        finally:
            if conn:
                conn.close()
    
    def get_user_saved_videos(self, user_id: str) -> pd.DataFrame:
        """Get user's saved videos with enhanced error handling"""
        if not user_id:
            return pd.DataFrame()
        
        conn = None
        try:
            conn = self.get_connection()
            
            query = """
                SELECT v.id, v.title, v.description, v.creator_id, v.file_path, v.duration,
                       v.file_size, v.format, v.upload_date, v.status, v.view_count,
                       v.like_count, v.dislike_count, v.genre, v.age_rating,
                       v.sentiment_score, v.content_tags, u.username as creator_name, 
                       sv.saved_at
                FROM saved_videos sv
                JOIN videos v ON sv.video_id = v.id
                JOIN users u ON v.creator_id = u.id
                WHERE sv.user_id = ? AND v.status = 'ready'
                ORDER BY sv.saved_at DESC
            """
            
            df = pd.read_sql(query, conn, params=[user_id])
            return df
            
        except sqlite3.OperationalError as e:
            logging.error(f"Database operational error fetching saved videos: {e}")
            return pd.DataFrame()
        except Exception as e:
            logging.error(f"Error fetching saved videos: {e}")
            return pd.DataFrame()
        finally:
            if conn:
                conn.close()
    
    def check_user_interaction(self, user_id: str, video_id: str, interaction_type: str) -> bool:
        """Check if user has already performed this interaction"""
        if not all([user_id, video_id, interaction_type]):
            return False
        
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT COUNT(*) FROM user_interactions
                WHERE user_id = ? AND video_id = ? AND interaction_type = ?
            """, (user_id, video_id, interaction_type))
            
            result = cursor.fetchone()[0] > 0
            return result
            
        except Exception as e:
            logging.error(f"Error checking user interaction: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    def check_video_saved(self, user_id: str, video_id: str) -> bool:
        """Check if video is saved by user"""
        if not all([user_id, video_id]):
            return False
        
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT COUNT(*) FROM saved_videos
                WHERE user_id = ? AND video_id = ?
            """, (user_id, video_id))
            
            result = cursor.fetchone()[0] > 0
            return result
            
        except Exception as e:
            logging.error(f"Error checking saved video: {e}")
            return False
        finally:
            if conn:
                conn.close()

# Enhanced Analytics Engine
class AnalyticsEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    def track_interaction(self, user_id: str, video_id: str, interaction_type: str):
        """Track user interactions for analytics with better error handling"""
        if not all([user_id, video_id, interaction_type]):
            logging.warning(f"Invalid interaction parameters: {user_id}, {video_id}, {interaction_type}")
            return
        
        conn = None
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            interaction_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT OR IGNORE INTO user_interactions (id, user_id, video_id, interaction_type, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (interaction_id, user_id, video_id, interaction_type, datetime.now()))
            
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
            logging.info(f"Interaction already exists: {user_id}, {video_id}, {interaction_type}")
        except Exception as e:
            logging.error(f"Error tracking interaction: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
    
    def remove_interaction(self, user_id: str, video_id: str, interaction_type: str):
        """Remove user interaction (for unlike, etc.)"""
        if not all([user_id, video_id, interaction_type]):
            return
        
        conn = None
        try:
            conn = self.db.get_connection()
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
            
        except Exception as e:
            logging.error(f"Error removing interaction: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
    
    def save_video(self, user_id: str, video_id: str):
        """Save video for user"""
        if not all([user_id, video_id]):
            return False
        
        conn = None
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            save_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO saved_videos (id, user_id, video_id, saved_at)
                VALUES (?, ?, ?, ?)
            """, (save_id, user_id, video_id, datetime.now()))
            
            conn.commit()
            return True
            
        except sqlite3.IntegrityError:
            logging.info(f"Video already saved: {user_id}, {video_id}")
            return False
        except Exception as e:
            logging.error(f"Error saving video: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    def unsave_video(self, user_id: str, video_id: str):
        """Remove video from saved list"""
        if not all([user_id, video_id]):
            return
        
        conn = None
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM saved_videos
                WHERE user_id = ? AND video_id = ?
            """, (user_id, video_id))
            
            conn.commit()
            
        except Exception as e:
            logging.error(f"Error unsaving video: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

# Enhanced Recommendation Engine  
class RecommendationEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    def get_recommendations(self, user_id: str, limit: int = 10) -> List[Dict]:
        """Get personalized video recommendations based on user behavior"""
        if not user_id:
            return []
        
        conn = None
        try:
            conn = self.db.get_connection()
            
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
                    SELECT v.id, v.title, v.description, v.creator_id, v.file_path, v.duration,
                           v.file_size, v.format, v.upload_date, v.status, v.view_count,
                           v.like_count, v.dislike_count, v.genre, v.age_rating,
                           v.sentiment_score, v.content_tags, u.username as creator_name
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
                
                if preferred_genres:
                    placeholders = ','.join(['?' for _ in preferred_genres])
                    trending = pd.read_sql(f"""
                        SELECT v.id, v.title, v.description, v.creator_id, v.file_path, v.duration,
                               v.file_size, v.format, v.upload_date, v.status, v.view_count,
                               v.like_count, v.dislike_count, v.genre, v.age_rating,
                               v.sentiment_score, v.content_tags, u.username as creator_name,
                               ABS(v.sentiment_score - ?) as sentiment_diff
                        FROM videos v
                        JOIN users u ON v.creator_id = u.id
                        WHERE v.status = 'ready' AND v.genre IN ({placeholders})
                        ORDER BY sentiment_diff ASC, v.view_count DESC
                        LIMIT ?
                    """, conn, params=[avg_sentiment] + preferred_genres + [limit])
                else:
                    trending = pd.DataFrame()
            
            return trending.to_dict('records') if not trending.empty else []
            
        except Exception as e:
            logging.error(f"Error getting recommendations: {e}")
            return []
        finally:
            if conn:
                conn.close()

class SentimentAnalyzer:
    def __init__(self):
        # Simple rule-based sentiment analysis for demo
        self.positive_words = ['good', 'great', 'awesome', 'love', 'amazing', 'excellent', 'fantastic', 'wonderful', 'perfect', 'brilliant']
        self.negative_words = ['bad', 'terrible', 'hate', 'awful', 'horrible', 'worst', 'boring', 'stupid', 'annoying', 'disappointing']
    
    def analyze_sentiment(self, text: str) -> float:
        """Analyze sentiment of text (-1 to 1 scale)"""
        if not text or not isinstance(text, str):
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

# Initialize components with better error handling
def initialize_components():
    """Initialize all database and engine components"""
    try:
        # Ensure uploads directory exists
        if not os.path.exists(Config.UPLOAD_FOLDER):
            os.makedirs(Config.UPLOAD_FOLDER)
        
        db_manager = DatabaseManager(Config.DATABASE_PATH)
        recommendation_engine = RecommendationEngine(db_manager)
        analytics_engine = AnalyticsEngine(db_manager)
        sentiment_analyzer = SentimentAnalyzer()
        
        logging.info("All components initialized successfully")
        return db_manager, recommendation_engine, analytics_engine, sentiment_analyzer
        
    except Exception as e:
        logging.error(f"Failed to initialize components: {e}")
        raise

def create_video_serving_route(app):
    
    @app.server.route('/uploads/<filename>')
    def uploaded_file(filename):
        uploads_dir = os.path.join(os.getcwd(), 'uploads')
        return send_from_directory(uploads_dir, filename)


# Initialize components
db_manager, recommendation_engine, analytics_engine, sentiment_analyzer = initialize_components()

# Create directories
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

# Initialize Dash app
app = dash.Dash(__name__, suppress_callback_exceptions=True, external_stylesheets=[dbc.themes.BOOTSTRAP])
server = app.server

# ADD THIS LINE:
create_video_serving_route(app)

# Layout Components
def create_auth_layout():
    """Create authentication layout"""
    return html.Div([
        html.Div([
            html.H1("ShareTok Platform", style={
                'textAlign': 'center',
                'color': '#333',
                'marginBottom': '30px',
                'fontFamily': 'Arial, sans-serif'
            }),
            
            dcc.Tabs(
                id="auth-tabs", 
                value="login",
                style={'marginBottom': '20px'},
                children=[
                    dcc.Tab(
                        label="Login", 
                        value="login",
                        style={'padding': '12px 24px', 'border': '1px solid #ddd'}
                    ),
                    dcc.Tab(
                        label="Register", 
                        value="register",
                        style={'padding': '12px 24px', 'border': '1px solid #ddd'}
                    )
                ]
            ),
            
            html.Div(id='auth-content')
        ], style={
            'width': '400px',
            'margin': '50px auto',
            'padding': '40px',
            'border': '1px solid #ddd',
            'borderRadius': '8px',
            'backgroundColor': 'white',
            'boxShadow': '0 4px 6px rgba(0, 0, 0, 0.1)'
        })
    ], style={
        'backgroundColor': '#f5f5f5',
        'minHeight': '100vh',
        'padding': '20px'
    })

def create_main_layout(user_data):
    """Create main application layout"""
    
    # Define tabs based on user role
    if user_data['role'] == UserRole.CREATOR.value:
        tab_children = [
            dcc.Tab(label="Browse", value="browse", id="browse-tab"),
            dcc.Tab(label="Upload", value="upload", id="upload-tab"),
            dcc.Tab(label="My Videos", value="my-videos", id="my-videos-tab"),
            dcc.Tab(label="Analytics", value="creator-analytics", id="creator-analytics-tab")
        ]
    else:
        tab_children = [
            dcc.Tab(label="Browse", value="browse", id="browse-tab"),
            dcc.Tab(label="Saved", value="saved", id="saved-tab")
        ]

    return html.Div([
        # Header
        html.Div([
            html.Div([
                html.H1("ShareTok Platform", style={
                    'margin': '0',
                    'color': 'white',
                    'fontSize': '24px'
                }),
                html.Div([
                    html.Span(f"Welcome, {user_data['username']} ({user_data['role'].title()})", 
                             style={'color': 'white', 'marginRight': '20px'}),
                    html.Button("Logout", id="logout-btn", style={
                        'backgroundColor': '#dc3545',
                        'color': 'white',
                        'border': 'none',
                        'padding': '8px 16px',
                        'borderRadius': '4px',
                        'cursor': 'pointer'
                    })
                ])
            ], style={
                'display': 'flex',
                'justifyContent': 'space-between',
                'alignItems': 'center'
            })
        ], style={
            'backgroundColor': '#007bff',
            'padding': '15px 30px',
            'marginBottom': '0'
        }),
        
        # Navigation Tabs
        dcc.Tabs(
            id="main-tabs",
            value="browse",
            children=tab_children,
            style={'marginBottom': '20px'}
        ),
        
        # Main Content Area
        html.Div(id="main-content", style={'padding': '0 30px 30px 30px'}),
        
        # Store components
        dcc.Store(id='user-store', data=user_data),
        dcc.Store(id='current-video-store', data={}),
        dcc.Store(id='navigation-store', data={'current_page': 'browse'}),
        dcc.Store(id='upload-status-store', data={}),
        
        # Interval component for periodic updates
        dcc.Interval(
            id='interval-component',
            interval=5*1000,  # 5 seconds
            n_intervals=0
        )
    ])


def create_video_grid(videos, user_data):
    """Create video grid layout"""
    if not videos:
        return html.Div("No videos available.", style={
            'textAlign': 'center',
            'padding': '40px',
            'color': '#666'
        })
    
    video_cards = []
    for video in videos:
        try:
            card = html.Div([
                html.Div([
                    html.Video(
                        src=f"/uploads/{os.path.basename(video.get('file_path', ''))}",
                        style={
                            'width': '100%',
                            'height': '160px',
                            'objectFit': 'cover',
                            'backgroundColor': '#000'
                        },
                        controls=False,
                        preload='metadata'
                    )
                ], style={'position': 'relative', 'overflow': 'hidden'}),
                
                html.Div([
                    html.H4(video.get('title', 'No Title'), style={
                        'margin': '8px 0 4px 0',
                        'fontSize': '16px',
                        'fontWeight': 'bold'
                    }),
                    html.P(f"By {video.get('creator_name', 'Unknown')}", style={
                        'margin': '0 0 8px 0',
                        'color': '#666',
                        'fontSize': '14px'
                    }),
                    html.Div([
                        html.Span(f"👁 {video.get('view_count', 0):,}", style={
                            'marginRight': '12px',
                            'fontSize': '12px',
                            'color': '#666'
                        }),
                        html.Span(f"👍 {video.get('like_count', 0):,}", style={
                            'marginRight': '12px',
                            'fontSize': '12px',
                            'color': '#666'
                        }),
                        html.Span(f"🏷 {video.get('genre', 'Unknown')}", style={
                            'fontSize': '12px',
                            'color': '#007bff',
                            'backgroundColor': '#e7f3ff',
                            'padding': '2px 6px',
                            'borderRadius': '4px'
                        })
                    ], style={'marginBottom': '8px'}),
                    html.P(
                        (video.get('description', '')[:100] + "..." 
                         if len(video.get('description', '')) > 100 
                         else video.get('description', '')), 
                        style={
                            'margin': '0',
                            'fontSize': '12px',
                            'color': '#666',
                            'lineHeight': '1.4'
                        }
                    )
                ], style={'padding': '12px'})
            ], 
            style={
                'border': '1px solid #ddd',
                'borderRadius': '8px',
                'backgroundColor': 'white',
                'cursor': 'pointer',
                'transition': 'transform 0.2s',
                'margin': '10px',
                'width': '280px',
                'boxShadow': '0 2px 4px rgba(0,0,0,0.1)'
            }, 
            id={'type': 'video-card', 'index': video.get('id', '')},
            n_clicks=0)
            
            video_cards.append(card)
        except Exception as e:
            logging.error(f"Error creating video card: {e}")
            continue
    
    return html.Div(video_cards, style={
        'display': 'flex',
        'flexWrap': 'wrap',
        'justifyContent': 'flex-start',
        'padding': '10px'
    })

def create_video_player_layout(video_data, user_data):
    """Create video player layout"""
    if not video_data:
        return html.Div([
            html.Div("Video not found.", style={
                'textAlign': 'center',
                'padding': '40px',
                'color': '#dc3545',
                'fontSize': '18px'
            }),
            html.Button("← Back to Browse", 
                       id="back-to-browse-btn", 
                       style={
                           'backgroundColor': '#6c757d',
                           'color': 'white',
                           'border': 'none',
                           'padding': '10px 20px',
                           'borderRadius': '4px',
                           'cursor': 'pointer'
                       })
        ])
    
    try:
        # Check user interactions
        is_liked = db_manager.check_user_interaction(user_data['id'], video_data['id'], 'like')
        is_disliked = db_manager.check_user_interaction(user_data['id'], video_data['id'], 'dislike')
        is_saved = db_manager.check_video_saved(user_data['id'], video_data['id'])
        
        return html.Div([
            html.Button("← Back to Browse", 
                       id="back-to-browse-btn", 
                       style={
                           'backgroundColor': '#6c757d',
                           'color': 'white',
                           'border': 'none',
                           'padding': '10px 20px',
                           'borderRadius': '4px',
                           'cursor': 'pointer',
                           'marginBottom': '20px'
                       }),
            
            html.Div([
                # Video Player Section
                html.Div([
                    html.Video(
                        src=f"/uploads/{os.path.basename(video_data.get('file_path', ''))}",
                        controls=True,
                        style={
                            'width': '100%',
                            'maxHeight': '500px',
                            'backgroundColor': '#000',
                            'borderRadius': '8px'
                        }
                    ),
                    
                    html.Div([
                        html.H2(video_data.get('title', 'No Title'), style={
                            'margin': '20px 0 10px 0',
                            'fontSize': '24px',
                            'fontWeight': 'bold'
                        }),
                        html.P(f"By {video_data.get('creator_name', 'Unknown')} • {video_data.get('view_count', 0):,} views • {video_data.get('genre', 'Unknown')}", 
                              style={
                                  'color': '#666',
                                  'margin': '0 0 20px 0'
                              }),
                        
                        # Interaction buttons
                        html.Div([
                            html.Button(
                                f"👍 {video_data.get('like_count', 0):,}",
                                id="like-btn",
                                style={
                                    'backgroundColor': '#28a745' if is_liked else '#f8f9fa',
                                    'color': 'white' if is_liked else '#333',
                                    'border': '1px solid #ddd',
                                    'padding': '8px 16px',
                                    'borderRadius': '4px',
                                    'cursor': 'pointer',
                                    'marginRight': '10px'
                                },
                                **{'data-video-id': video_data['id']}
                            ),
                            html.Button(
                                f"👎 {video_data.get('dislike_count', 0):,}",
                                id="dislike-btn",
                                style={
                                    'backgroundColor': '#dc3545' if is_disliked else '#f8f9fa',
                                    'color': 'white' if is_disliked else '#333',
                                    'border': '1px solid #ddd',
                                    'padding': '8px 16px',
                                    'borderRadius': '4px',
                                    'cursor': 'pointer',
                                    'marginRight': '10px'
                                },
                                **{'data-video-id': video_data['id']}
                            ),
                            html.Button(
                                "💾 Save" if not is_saved else "✅ Saved",
                                id="save-btn",
                                style={
                                    'backgroundColor': '#17a2b8' if is_saved else '#f8f9fa',
                                    'color': 'white' if is_saved else '#333',
                                    'border': '1px solid #ddd',
                                    'padding': '8px 16px',
                                    'borderRadius': '4px',
                                    'cursor': 'pointer'
                                },
                                **{'data-video-id': video_data['id']}
                            )
                        ], style={'marginBottom': '20px'}),
                        
                        html.Div([
                            html.H4("Description", style={'margin': '0 0 10px 0'}),
                            html.P(video_data.get('description', 'No description available'), style={
                                'lineHeight': '1.6',
                                'color': '#333'
                            })
                        ], style={
                            'backgroundColor': '#f8f9fa',
                            'padding': '15px',
                            'borderRadius': '8px',
                            'border': '1px solid #ddd'
                        })
                    ])
                ], style={'flex': '2', 'marginRight': '20px'}),
                
                # Comments Section
                html.Div([
                    html.H4("Comments", style={'margin': '0 0 15px 0'}),
                    html.Div([
                        dcc.Textarea(
                            id="comment-input",
                            placeholder="Add a comment...",
                            style={
                                'width': '100%',
                                'height': '80px',
                                'padding': '10px',
                                'border': '1px solid #ddd',
                                'borderRadius': '4px',
                                'resize': 'vertical'
                            }
                        ),
                        html.Button("Post Comment", id="post-comment-btn", style={
                            'backgroundColor': '#007bff',
                            'color': 'white',
                            'border': 'none',
                            'padding': '8px 16px',
                            'borderRadius': '4px',
                            'cursor': 'pointer',
                            'marginTop': '10px'
                        })
                    ]),
                    
                    html.Div(id="comments-list", style={'marginTop': '20px'})
                ], style={'flex': '1'})
            ], style={'display': 'flex'})
        ])
    except Exception as e:
        logging.error(f"Error creating video player layout: {e}")
        return html.Div([
            html.Div("Error loading video player.", style={
                'textAlign': 'center',
                'padding': '40px',
                'color': '#dc3545'
            }),
            html.Button("← Back to Browse", 
                       id="back-to-browse-btn", 
                       style={
                           'backgroundColor': '#6c757d',
                           'color': 'white',
                           'border': 'none',
                           'padding': '10px 20px',
                           'borderRadius': '4px',
                           'cursor': 'pointer'
                       })
        ])

def create_browse_content():
    """Create browse videos content"""
    return html.Div([
        html.H2("Browse Videos", style={'marginBottom': '20px'}),
        
        # Search and filters
        html.Div([
            dcc.Input(
                id='search-input', 
                type='text', 
                placeholder='Search videos...', 
                style={
                    'width': '300px',
                    'padding': '8px',
                    'marginRight': '15px',
                    'border': '1px solid #ddd',
                    'borderRadius': '4px'
                }
            ),
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
                style={'width': '200px'}
            )
        ], style={
            'display': 'flex', 
            'alignItems': 'center', 
            'marginBottom': '20px'
        }),
        
        html.Div(id='video-browse-results')
    ])

def create_saved_content(user_data):
    """Create saved videos content"""
    try:
        saved_videos_df = db_manager.get_user_saved_videos(user_data['id'])
        return html.Div([
            html.H2("Saved Videos", style={'marginBottom': '20px'}),
            create_video_grid(saved_videos_df.to_dict('records'), user_data) if not saved_videos_df.empty 
            else html.Div("No saved videos yet.", style={
                'textAlign': 'center',
                'padding': '40px',
                'color': '#666',
                'fontSize': '18px'
            })
        ])
    except Exception as e:
        logging.error(f"Error creating saved content: {e}")
        return html.Div("Error loading saved videos.")

def create_upload_content():
    """Create upload content"""
    return html.Div([
        html.H2("Upload Video", style={'marginBottom': '20px'}),
        
        html.Div(id="upload-status-display", style={'marginBottom': '20px'}),
        
        html.Div([
            dcc.Upload(
                id='upload-video',
                children=html.Div([
                    'Drag and Drop or ',
                    html.A('Select Files')
                ]),
                style={
                    'width': '100%',
                    'height': '60px',
                    'lineHeight': '60px',
                    'borderWidth': '1px',
                    'borderStyle': 'dashed',
                    'borderRadius': '5px',
                    'textAlign': 'center',
                    'margin': '10px 0',
                    'cursor': 'pointer'
                },
                multiple=False,
                accept='video/*'
            ),
            
            html.Div([
                dcc.Input(
                    id='video-title', 
                    type='text', 
                    placeholder='Video Title', 
                    style={
                        'width': '100%',
                        'padding': '10px',
                        'margin': '10px 0',
                        'border': '1px solid #ddd',
                        'borderRadius': '4px'
                    }
                ),
                dcc.Textarea(
                    id='video-description', 
                    placeholder='Video Description', 
                    style={
                        'width': '100%',
                        'height': '100px',
                        'padding': '10px',
                        'margin': '10px 0',
                        'border': '1px solid #ddd',
                        'borderRadius': '4px',
                        'resize': 'vertical'
                    }
                ),
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
                    style={'margin': '10px 0'}
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
                    style={'margin': '10px 0'}
                ),
                html.Button("Upload Video", id="upload-submit-btn", style={
                    'backgroundColor': '#28a745',
                    'color': 'white',
                    'border': 'none',
                    'padding': '12px 24px',
                    'borderRadius': '4px',
                    'cursor': 'pointer',
                    'fontSize': '16px'
                })
            ], id="upload-form", style={'display': 'none'})
        ])
    ])

def create_my_videos_content(user_data):
    """Create my videos content"""
    try:
        my_videos_df = db_manager.get_videos({'creator_id': user_data['id']})
        return html.Div([
            html.H2("My Videos", style={'marginBottom': '20px'}),
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
                    style_cell={'textAlign': 'left', 'padding': '10px'},
                    style_header={'backgroundColor': '#f8f9fa', 'fontWeight': 'bold'},
                    style_data_conditional=[
                        {
                            'if': {'filter_query': '{status} = ready'},
                            'backgroundColor': '#d4edda'
                        }
                    ]
                ) if not my_videos_df.empty else html.Div("No videos uploaded yet.", style={
                    'textAlign': 'center',
                    'padding': '40px',
                    'color': '#666'
                })
            ])
        ])
    except Exception as e:
        logging.error(f"Error creating my videos content: {e}")
        return html.Div("Error loading your videos.")

def create_analytics_content():
    """Create analytics content"""
    return html.Div([
        html.H2("Creator Analytics", style={'marginBottom': '20px'}),
        html.Div([
            html.Div(id='creator-metrics-cards', style={
                'display': 'flex',
                'gap': '20px',
                'marginBottom': '30px'
            }),
            dcc.Graph(id='creator-performance-chart')
        ])
    ])

# Main layout
app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    dcc.Store(id='session-store', storage_type='session'),
    # Add a hidden div that will contain dynamic content including back button
    html.Div(id='dynamic-content', style={'display': 'none'}),
    html.Div(id='app-content')
])

# Main page router
@app.callback(
    Output('app-content', 'children'),
    [Input('url', 'pathname'),
     Input('session-store', 'data')]
)
def display_page(pathname, session_data):
    """Main page router"""
    try:
        if not session_data or not session_data.get('authenticated'):
            return create_auth_layout()
        else:
            return create_main_layout(session_data['user'])
    except Exception as e:
        logging.error(f"Error in display_page: {e}")
        return create_auth_layout()

# Authentication content callback
@app.callback(
    Output('auth-content', 'children'),
    [Input('auth-tabs', 'value')],
    prevent_initial_call=False
)
def render_auth_content(active_tab):
    """Render authentication content based on active tab"""
    try:
        input_style = {
            'width': '100%',
            'padding': '10px',
            'margin': '10px 0',
            'border': '1px solid #ddd',
            'borderRadius': '4px'
        }
        
        button_style = {
            'width': '100%',
            'padding': '12px',
            'backgroundColor': '#007bff',
            'color': 'white',
            'border': 'none',
            'borderRadius': '4px',
            'cursor': 'pointer',
            'marginTop': '10px'
        }
        
        if active_tab == "login":
            return html.Div([
                dcc.Input(
                    id="login-username", 
                    type="text", 
                    placeholder="Username", 
                    style=input_style
                ),
                dcc.Input(
                    id="login-password", 
                    type="password", 
                    placeholder="Password", 
                    style=input_style
                ),
                html.Button(
                    "Login", 
                    id="login-btn", 
                    style=button_style
                ),
                html.Div(id="login-message", style={'marginTop': '10px'})
            ])
        
        elif active_tab == "register":
            return html.Div([
                dcc.Input(
                    id="reg-username", 
                    type="text", 
                    placeholder="Username", 
                    style=input_style
                ),
                dcc.Input(
                    id="reg-email", 
                    type="email", 
                    placeholder="Email", 
                    style=input_style
                ),
                dcc.Input(
                    id="reg-password", 
                    type="password", 
                    placeholder="Password", 
                    style=input_style
                ),
                dcc.Dropdown(
                    id="reg-role",
                    options=[
                        {'label': 'Consumer (Watch videos)', 'value': 'consumer'},
                        {'label': 'Creator (Upload videos)', 'value': 'creator'}
                    ],
                    placeholder="Select Role",
                    style={'margin': '10px 0'}
                ),
                html.Button(
                    "Register", 
                    id="register-btn", 
                    style=button_style
                ),
                html.Div(id="register-message", style={'marginTop': '10px'})
            ])
        
        return html.Div("Select login or register.")
    
    except Exception as e:
        logging.error(f"Error rendering auth content: {e}")
        return html.Div("Error loading authentication form.", style={
            'color': '#dc3545',
            'textAlign': 'center',
            'padding': '20px'
        })

# Login callback
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
    try:
        if not n_clicks or not username or not password:
            return dash.no_update, ""
        
        user = db_manager.authenticate_user(username, password)
        if user:
            return {
                'authenticated': True,
                'user': user
            }, ""
        else:
            return dash.no_update, html.Div("Invalid credentials", style={'color': '#dc3545'})
    except Exception as e:
        logging.error(f"Login error: {e}")
        return dash.no_update, html.Div("Login failed. Please try again.", style={'color': '#dc3545'})

# Registration callback
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
    try:
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
            return dash.no_update, html.Div("Registration failed. Username or email may already exist.", 
                                          style={'color': '#dc3545'})
    except Exception as e:
        logging.error(f"Registration error: {e}")
        return dash.no_update, html.Div("Registration failed. Please try again.", 
                                      style={'color': '#dc3545'})

# Logout callback
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

# Video card click handler - separate callback
@app.callback(
    [Output('current-video-store', 'data'),
     Output('navigation-store', 'data')],
    [Input({'type': 'video-card', 'index': ALL}, 'n_clicks')],
    [State('user-store', 'data'),
     State('current-video-store', 'data')],
    prevent_initial_call=True
)
def handle_video_card_clicks(video_clicks, user_data, current_video):
    """Handle video card clicks to show video player"""
    try:
        if not user_data or not any(video_clicks):
            return dash.no_update, dash.no_update
        
        ctx = callback_context
        if ctx.triggered and 'video-card' in ctx.triggered[0]['prop_id']:
            trigger_id = ctx.triggered[0]['prop_id']
            if '"index":"' in trigger_id:
                video_id = trigger_id.split('"index":"')[1].split('"')[0]
                if video_id:
                    # Track the view interaction
                    analytics_engine.track_interaction(user_data['id'], video_id, 'view')
                    
                    return (
                        {'video_id': video_id, 'show_player': True},
                        {'show_video_player': True, 'video_id': video_id}
                    )
        
        return dash.no_update, dash.no_update
    except Exception as e:
        logging.error(f"Error handling video card click: {e}")
        return dash.no_update, dash.no_update

# Back button handler - separate callback using pattern matching
@app.callback(
    Output('navigation-store', 'data', allow_duplicate=True),
    [Input({'type': 'back-btn', 'index': ALL}, 'n_clicks')],
    prevent_initial_call=True
)
def handle_back_button(back_clicks):
    """Handle back button clicks"""
    try:
        if any(back_clicks):
            return {'show_video_player': False, 'back_to_browse': True}
        return dash.no_update
    except Exception as e:
        logging.error(f"Error handling back button: {e}")
        return dash.no_update

# Main content handler - updated to handle navigation
@app.callback(
    Output('main-content', 'children'),
    [Input('main-tabs', 'value'),
     Input('navigation-store', 'data')],
    [State('user-store', 'data'),
     State('current-video-store', 'data')],
    prevent_initial_call=False
)
def update_main_content(active_tab, navigation_data, user_data, current_video):
    """Handle main content updates based on tab selection and navigation"""
    try:
        if not user_data:
            return html.Div("Please login to continue.")
        
        # Handle video player navigation
        if navigation_data:
            if navigation_data.get('show_video_player') and navigation_data.get('video_id'):
                video_id = navigation_data['video_id']
                video_data = db_manager.get_video_by_id(video_id)
                if video_data:
                    # Create video player with updated back button
                    player_layout = create_video_player_layout(video_data, user_data)
                    # Update the back button to use pattern matching
                    if hasattr(player_layout, 'children'):
                        for child in player_layout.children:
                            if hasattr(child, 'id') and child.id == 'back-to-browse-btn':
                                child.id = {'type': 'back-btn', 'index': 'video-player'}
                    return player_layout
            elif navigation_data.get('back_to_browse'):
                # Force return to browse tab
                return create_browse_content()
        
        # Handle tab-based content
        if active_tab == 'browse':
            return create_browse_content()
        
        elif active_tab == 'saved' and user_data['role'] == UserRole.CONSUMER.value:
            return create_saved_content(user_data)
        
        elif active_tab == 'upload' and user_data['role'] == UserRole.CREATOR.value:
            return create_upload_content()
        
        elif active_tab == 'my-videos' and user_data['role'] == UserRole.CREATOR.value:
            return create_my_videos_content(user_data)
        
        elif active_tab == 'creator-analytics' and user_data['role'] == UserRole.CREATOR.value:
            return create_analytics_content()
        
        else:
            # Default to browse
            return create_browse_content()
            
    except Exception as e:
        logging.error(f"Error in main content handler: {e}")
        error_content = html.Div([
            html.H3("An error occurred"),
            html.P("Please refresh the page or try again.")
        ], style={'textAlign': 'center', 'padding': '40px'})
        return error_content

# Browse results callback
@app.callback(
    Output('video-browse-results', 'children'),
    [Input('main-tabs', 'value'),
     Input('search-input', 'value'),
     Input('genre-filter', 'value'),
     Input('interval-component', 'n_intervals')],
    [State('user-store', 'data')],
    prevent_initial_call=True
)
def update_video_browse_results(active_tab, search_query, genre_filter, n_intervals, user_data):
    """Update video browse results"""
    try:
        if active_tab != 'browse' or not user_data:
            return dash.no_update
            
        filters = {}
        if search_query:
            filters['search'] = search_query
        if genre_filter and genre_filter != 'all':
            filters['genre'] = genre_filter
        
        videos_df = db_manager.get_videos(filters)
        return create_video_grid(videos_df.to_dict('records'), user_data)
    except Exception as e:
        logging.error(f"Error updating video browse: {e}")
        return html.Div("Error loading videos.")

# Video upload callback
@app.callback(
    [Output('upload-form', 'style'),
     Output('upload-status-display', 'children')],
    [Input('upload-video', 'contents'),
     Input('upload-submit-btn', 'n_clicks')],
    [State('upload-video', 'filename'),
     State('video-title', 'value'),
     State('video-description', 'value'),
     State('video-genre', 'value'),
     State('video-rating', 'value'),
     State('user-store', 'data')],
    prevent_initial_call=True
)
def handle_video_upload(contents, submit_clicks, filename, title, description, genre, rating, user_data):
    """Handle video upload process"""
    try:
        ctx = callback_context
        
        if not ctx.triggered:
            return dash.no_update, dash.no_update
        
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        # Show form when file is selected
        if trigger_id == 'upload-video' and contents:
            if not filename:
                return dash.no_update, html.Div("Please select a valid file.", style={'color': '#dc3545'})
            
            # Validate file type
            if not any(filename.lower().endswith(ext) for ext in Config.ALLOWED_VIDEO_FORMATS):
                return dash.no_update, html.Div(
                    f"Unsupported file format. Allowed: {', '.join(Config.ALLOWED_VIDEO_FORMATS)}", 
                    style={'color': '#dc3545'}
                )
            
            status_message = html.Div(f"File selected: {filename}", style={'color': '#28a745'})
            return {'display': 'block'}, status_message
        
        # Handle form submission
        if trigger_id == 'upload-submit-btn' and submit_clicks and contents:
            if not all([title, description, genre, rating]):
                return dash.no_update, html.Div("Please fill in all fields.", style={'color': '#dc3545'})
            
            try:
                # Decode and validate file
                content_type, content_string = contents.split(',')
                decoded = base64.b64decode(content_string)
                
                if len(decoded) > Config.MAX_FILE_SIZE:
                    return dash.no_update, html.Div("File too large. Maximum size: 100MB", style={'color': '#dc3545'})
                
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
                conn = db_manager.get_connection()
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
                
                success_message = html.Div([
                    html.H4("Upload Successful!", style={'color': '#28a745', 'margin': '0 0 10px 0'}),
                    html.P(f"Video '{title}' has been uploaded and is ready for viewing."),
                    html.P(f"Sentiment Analysis Score: {sentiment_score:.2f}"),
                    html.P(f"Content Tags: {', '.join(content_tags) if content_tags else 'None'}"),
                    html.Button("Upload Another Video", 
                               id="upload-another-btn", 
                               style={
                                   'backgroundColor': '#28a745',
                                   'color': 'white',
                                   'border': 'none',
                                   'padding': '8px 16px',
                                   'borderRadius': '4px',
                                   'cursor': 'pointer',
                                   'marginTop': '10px'
                               })
                ])
                
                return {'display': 'none'}, success_message
                
            except Exception as e:
                error_message = html.Div(f"Upload failed: {str(e)}", style={'color': '#dc3545'})
                return dash.no_update, error_message
        
        return dash.no_update, dash.no_update
    except Exception as e:
        logging.error(f"Error in video upload: {e}")
        return dash.no_update, html.Div("Upload failed. Please try again.", style={'color': '#dc3545'})

# Like/Dislike buttons callback
@app.callback(
    [Output('like-btn', 'children'),
     Output('like-btn', 'style'),
     Output('dislike-btn', 'children'),
     Output('dislike-btn', 'style')],
    [Input('like-btn', 'n_clicks'),
     Input('dislike-btn', 'n_clicks')],
    [State('user-store', 'data'),
     State('current-video-store', 'data')],
    prevent_initial_call=True
)
def handle_like_dislike(like_clicks, dislike_clicks, user_data, current_video_store):
    """Handle like/dislike interactions"""
    try:
        if not user_data or not current_video_store or not current_video_store.get('video_id'):
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
        
        like_text = f"👍 {updated_video_data.get('like_count', 0):,}"
        dislike_text = f"👎 {updated_video_data.get('dislike_count', 0):,}"
        
        like_style = {
            'backgroundColor': '#28a745' if is_liked else '#f8f9fa',
            'color': 'white' if is_liked else '#333',
            'border': '1px solid #ddd',
            'padding': '8px 16px',
            'borderRadius': '4px',
            'cursor': 'pointer',
            'marginRight': '10px'
        }
        
        dislike_style = {
            'backgroundColor': '#dc3545' if is_disliked else '#f8f9fa',
            'color': 'white' if is_disliked else '#333',
            'border': '1px solid #ddd',
            'padding': '8px 16px',
            'borderRadius': '4px',
            'cursor': 'pointer',
            'marginRight': '10px'
        }
        
        return like_text, like_style, dislike_text, dislike_style
    except Exception as e:
        logging.error(f"Error handling like/dislike: {e}")
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update

# Save button callback
@app.callback(
    [Output('save-btn', 'children'),
     Output('save-btn', 'style')],
    [Input('save-btn', 'n_clicks')],
    [State('user-store', 'data'),
     State('current-video-store', 'data')],
    prevent_initial_call=True
)
def handle_save_video(save_clicks, user_data, current_video_store):
    """Handle save/unsave video"""
    try:
        if not user_data or not current_video_store or not save_clicks or not current_video_store.get('video_id'):
            return dash.no_update, dash.no_update
        
        video_id = current_video_store['video_id']
        is_saved = db_manager.check_video_saved(user_data['id'], video_id)
        
        if is_saved:
            # Unsave video
            analytics_engine.unsave_video(user_data['id'], video_id)
            return "💾 Save", {
                'backgroundColor': '#f8f9fa',
                'color': '#333',
                'border': '1px solid #ddd',
                'padding': '8px 16px',
                'borderRadius': '4px',
                'cursor': 'pointer'
            }
        else:
            # Save video
            analytics_engine.save_video(user_data['id'], video_id)
            return "✅ Saved", {
                'backgroundColor': '#17a2b8',
                'color': 'white',
                'border': '1px solid #ddd',
                'padding': '8px 16px',
                'borderRadius': '4px',
                'cursor': 'pointer'
            }
    except Exception as e:
        logging.error(f"Error handling save video: {e}")
        return dash.no_update, dash.no_update

# Comments callback
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
    try:
        if not current_video_store or not current_video_store.get('video_id'):
            return []
        
        video_id = current_video_store['video_id']
        
        ctx = callback_context
        if ctx.triggered and ctx.triggered[0]['prop_id'] == 'post-comment-btn.n_clicks' and post_clicks:
            if comment_text and comment_text.strip() and user_data:
                # Add comment to database
                conn = db_manager.get_connection()
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
        conn = db_manager.get_connection()
        comments_df = pd.read_sql("""
            SELECT c.*, u.username
            FROM comments c
            JOIN users u ON c.user_id = u.id
            WHERE c.video_id = ?
            ORDER BY c.created_at DESC
        """, conn, params=[video_id])
        conn.close()
        
        if comments_df.empty:
            return [html.Div("No comments yet. Be the first to comment!", style={
                'textAlign': 'center',
                'padding': '20px',
                'color': '#666',
                'fontStyle': 'italic'
            })]
        
        comment_elements = []
        for _, comment in comments_df.iterrows():
            comment_elements.append(
                html.Div([
                    html.Div([
                        html.Strong(comment['username'], style={'color': '#007bff'}),
                        html.Span(f" • {comment['created_at']}", style={
                            'color': '#666',
                            'fontSize': '12px',
                            'marginLeft': '8px'
                        })
                    ]),
                    html.P(comment['content'], style={
                        'margin': '8px 0',
                        'lineHeight': '1.5'
                    }),
                    html.Div([
                        html.Span(f"Sentiment: {comment['sentiment_score']:.2f}", style={
                            'fontSize': '11px',
                            'color': '#999',
                            'backgroundColor': '#f8f9fa',
                            'padding': '2px 6px',
                            'borderRadius': '4px'
                        })
                    ])
                ], style={
                    'padding': '12px',
                    'borderBottom': '1px solid #eee',
                    'marginBottom': '8px'
                })
            )
        
        return comment_elements
    except Exception as e:
        logging.error(f"Error handling comments: {e}")
        return [html.Div("Error loading comments.")]

# Clear comment input callback
@app.callback(
    Output('comment-input', 'value'),
    [Input('post-comment-btn', 'n_clicks')],
    [State('comment-input', 'value')],
    prevent_initial_call=True
)
def clear_comment_input(n_clicks, comment_text):
    """Clear comment input after posting"""
    try:
        if n_clicks and comment_text:
            return ""
        return dash.no_update
    except Exception as e:
        logging.error(f"Error clearing comment input: {e}")
        return dash.no_update

# Creator analytics callback
@app.callback(
    [Output('creator-metrics-cards', 'children'),
     Output('creator-performance-chart', 'figure')],
    [Input('main-tabs', 'value'),
     Input('interval-component', 'n_intervals')],
    [State('user-store', 'data')],
    prevent_initial_call=True
)
def update_creator_analytics(active_tab, n_intervals, user_data):
    """Update creator analytics dashboard"""
    try:
        if active_tab != 'creator-analytics' or not user_data or user_data['role'] != UserRole.CREATOR.value:
            return dash.no_update, dash.no_update
        
        # Get creator's video performance
        videos_df = db_manager.get_videos({'creator_id': user_data['id']})
        
        if videos_df.empty:
            return [html.Div("No videos available for analysis.", style={
                'textAlign': 'center',
                'padding': '40px',
                'color': '#666'
            })], {}
        
        # Calculate metrics
        total_videos = len(videos_df)
        total_views = videos_df['view_count'].sum()
        total_likes = videos_df['like_count'].sum()
        avg_sentiment = videos_df['sentiment_score'].mean()
        
        # Create metrics cards
        card_style = {
            'backgroundColor': 'white',
            'padding': '20px',
            'borderRadius': '8px',
            'border': '1px solid #ddd',
            'textAlign': 'center',
            'minWidth': '150px'
        }
        
        metrics_cards = [
            html.Div([
                html.H3(str(total_videos), style={'margin': '0 0 5px 0', 'fontSize': '24px', 'color': '#007bff'}),
                html.P("Total Videos", style={'margin': '0', 'color': '#666'})
            ], style=card_style),
            
            html.Div([
                html.H3(f"{total_views:,}", style={'margin': '0 0 5px 0', 'fontSize': '24px', 'color': '#28a745'}),
                html.P("Total Views", style={'margin': '0', 'color': '#666'})
            ], style=card_style),
            
            html.Div([
                html.H3(f"{total_likes:,}", style={'margin': '0 0 5px 0', 'fontSize': '24px', 'color': '#dc3545'}),
                html.P("Total Likes", style={'margin': '0', 'color': '#666'})
            ], style=card_style),
            
            html.Div([
                html.H3(f"{avg_sentiment:.2f}", style={'margin': '0 0 5px 0', 'fontSize': '24px', 'color': '#17a2b8'}),
                html.P("Avg. Sentiment", style={'margin': '0', 'color': '#666'})
            ], style=card_style)
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
    except Exception as e:
        logging.error(f"Error updating creator analytics: {e}")
        return [html.Div("Error loading analytics.")], {}

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

if __name__ == '__main__':
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('scalevid.log'),
            logging.StreamHandler()
        ]
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