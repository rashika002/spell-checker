import os
import secrets
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import language_tool_python
from pymongo import MongoClient
from dotenv import load_dotenv
from textblob import TextBlob
from deep_translator import GoogleTranslator
import PyPDF2
import logging
import re

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(16))

# MongoDB setup
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client['spellingApp']
users_collection = db['users']
logs_collection = db['logs']

# Logging setup
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# LanguageTool setup
def get_language_tool(lang):
    try:
        return language_tool_python.LanguageTool(lang)
    except Exception as e:
        logger.error(f"Failed to initialize LanguageTool for {lang}: {e}")
        return language_tool_python.LanguageTool('en-US')

# Helper: Grammar Correction
def correct_grammar(text, lang='en'):
    try:
        tool = get_language_tool(lang)
        matches = tool.check(text)
        return language_tool_python.utils.correct(text, matches)
    except Exception as e:
        logger.error(f"GrammarTool error: {e}")
        return text

# Helper: Spell Check
def spell_check(text):
    try:
        words = text.split()
        corrected = [TextBlob(w).correct().string for w in words]
        return " ".join(corrected)
    except Exception as e:
        logger.error(f"Spell check error: {e}")
        return text

# Helper: Translation
def translate_text(text, target_lang):
    try:
        translated = GoogleTranslator(source='auto', target=target_lang).translate(text)
        if not translated:
            raise ValueError("Translation returned empty result")
        return translated
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text

# Helper: Tone Detection
def detect_tone(text):
    try:
        polarity = TextBlob(text).sentiment.polarity
        if polarity > 0.2:
            return "Positive 😊"
        elif polarity < -0.2:
            return "Negative 😞"
        else:
            return "Neutral 😐"
    except Exception as e:
        logger.error(f"Tone detection error: {e}")
        return "Unknown"

# Helper: File Content Extraction
def extract_file_content(file):
    try:
        if file.filename.endswith('.txt'):
            return file.read().decode('utf-8', errors='ignore')
        elif file.filename.endswith('.pdf'):
            pdf = PyPDF2.PdfReader(file)
            return "".join(page.extract_text() or "" for page in pdf.pages)
        else:
            return None
    except Exception as e:
        logger.error(f"File processing error: {e}")
        return None

# Initialize session results
def init_session_results():
    if 'results' not in session:
        session['results'] = {
            'spell': None,
            'grammar': None,
            'translate': None,
            'file': None
        }
    session.modified = True

# ---------------- ROUTES ----------------

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        phone = request.form.get('phone')

        if not all([username, password, first_name, last_name, email, phone]):
            flash("All fields are required.")
            return redirect(url_for('register'))

        if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
            flash("Invalid email address.")
            return redirect(url_for('register'))

        if not re.match(r'^\+?[0-9]{7,15}$', phone):
            flash("Invalid phone number (7-15 digits, optional +).")
            return redirect(url_for('register'))

        if users_collection.find_one({"username": username}):
            flash("Username already exists.")
            return redirect(url_for('register'))

        if users_collection.find_one({"email": email}):
            flash("Email already registered.")
            return redirect(url_for('register'))

        user_data = {
            "username": username,
            "password": generate_password_hash(password),
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone
        }
        users_collection.insert_one(user_data)
        logger.debug(f"Registered user: {username}")
        flash("Registered successfully.")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = users_collection.find_one({"username": username})

        if user and check_password_hash(user['password'], password):
            session['user'] = user['username']
            init_session_results()
            logger.debug(f"User {username} logged in")
            return redirect(url_for('dashboard'))
        flash("Invalid username or password.")
        logger.warning(f"Failed login attempt for {username}")
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash("Please log in to access the dashboard.")
        logger.warning("Unauthorized dashboard access")
        return redirect(url_for('login'))
    init_session_results()
    logger.debug(f"Rendering dashboard for {session['user']}: {session['results']}")
    return render_template('dashboard.html',
                         spell_results=session['results']['spell'],
                         grammar_results=session['results']['grammar'],
                         translate_results=session['results']['translate'],
                         file_results=session['results']['file'])

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('results', None)
    logger.debug("User logged out")
    flash("Logged out successfully.")
    return redirect(url_for('login'))

# ---------- Processing Endpoints ----------

@app.route('/spell', methods=['POST'])
def spell():
    if 'user' not in session:
        return jsonify({'error': 'Please log in to access this feature.'}), 401
    init_session_results()
    text = request.form.get('text', '').strip()
    if not text:
        return jsonify({'error': 'No text provided for spell check.'}), 400
    try:
        corrected = spell_check(text)
        tone = detect_tone(corrected)
        session['results']['spell'] = {'original': text, 'corrected': corrected, 'tone': tone}
        session.modified = True
        logs_collection.insert_one({
            'username': session['user'],
            'original': text,
            'corrected': corrected,
            'type': 'spell',
            'tone': tone
        })
        logger.debug(f"Spell check: {session['results']['spell']}")
        return jsonify({'results': session['results']['spell']})
    except Exception as e:
        logger.error(f"Spell check error: {e}")
        return jsonify({'error': f"Error processing spell check: {str(e)}"}), 500

@app.route('/grammar', methods=['POST'])
def grammar():
    if 'user' not in session:
        return jsonify({'error': 'Please log in to access this feature.'}), 401
    init_session_results()
    text = request.form.get('text', '').strip()
    lang = request.form.get('language', 'en')
    if not text:
        return jsonify({'error': 'No text provided for grammar correction.'}), 400
    try:
        corrected = correct_grammar(text, lang)
        tone = detect_tone(corrected)
        session['results']['grammar'] = {'original': text, 'corrected': corrected, 'tone': tone}
        session.modified = True
        logs_collection.insert_one({
            'username': session['user'],
            'original': text,
            'corrected': corrected,
            'type': 'grammar',
            'language': lang,
            'tone': tone
        })
        logger.debug(f"Grammar check: {session['results']['grammar']}")
        return jsonify({'results': session['results']['grammar']})
    except Exception as e:
        logger.error(f"Grammar check error: {e}")
        return jsonify({'error': f"Error processing grammar check: {str(e)}"}), 500

@app.route('/translate', methods=['POST'])
def translate():
    if 'user' not in session:
        return jsonify({'error': 'Please log in to access this feature.'}), 401
    init_session_results()
    text = request.form.get('text', '').strip()
    target_lang = request.form.get('language', 'en')
    if not text:
        return jsonify({'error': 'No text provided for translation.'}), 400
    try:
        translated = translate_text(text, target_lang)
        tone = detect_tone(translated)
        session['results']['translate'] = {'original': text, 'corrected': translated, 'tone': tone}
        session.modified = True
        logs_collection.insert_one({
            'username': session['user'],
            'original': text,
            'corrected': translated,
            'type': 'translation',
            'target_lang': target_lang,
            'tone': tone
        })
        logger.debug(f"Translate: {session['results']['translate']}")
        return jsonify({'results': session['results']['translate']})
    except Exception as e:
        logger.error(f"Translate error: {e}")
        return jsonify({'error': f"Error processing translation: {str(e)}"}), 500

@app.route('/upload', methods=['POST'])
def upload():
    if 'user' not in session:
        return jsonify({'error': 'Please log in to access this feature.'}), 401
    init_session_results()
    if 'file' not in request.files or not request.files['file'].filename:
        return jsonify({'error': 'No file selected.'}), 400
    file = request.files['file']
    lang = request.form.get('language', 'en')
    if not file.filename.endswith(('.txt', '.pdf')):
        return jsonify({'error': 'Only .txt or .pdf files are supported.'}), 400
    content = extract_file_content(file)
    if not content or not content.strip():
        return jsonify({'error': 'File is empty or unreadable.'}), 400
    try:
        if lang in ['en', 'hi']:
            result = correct_grammar(content, lang)
            process_type = 'grammar'
        else:
            result = translate_text(content, lang)
            process_type = 'translation'
        tone = detect_tone(result)
        session['results']['file'] = {'original': content, 'corrected': result, 'tone': tone}
        session.modified = True
        logs_collection.insert_one({
            'username': session['user'],
            'original': content,
            'corrected': result,
            'type': process_type,
            'language': lang,
            'tone': tone
        })
        logger.debug(f"File upload: {session['results']['file']}")
        return jsonify({'results': session['results']['file']})
    except Exception as e:
        logger.error(f"File upload error: {e}")
        return jsonify({'error': f"Error processing file upload: {str(e)}"}), 500

# ---------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True)