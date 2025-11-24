from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import ollama
import hashlib

# ---- MEMORY STORE (per user) ----
chat_memory = {}  # { user_id: [ {role, content}, ... ] }

app = Flask(__name__)
CORS(app)

# --- CONFIG ---
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',      # Your MySQL Username
    'password': '57dvruksha',      # Your MySQL Password
    'database': 'career_nexus'
}

MODEL_NAME = "career-guru" # Ensure you created this using 'ollama create'

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

# --- ROUTES ---

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", 
                       (data['username'], hash_pass(data['password'])))
        conn.commit()
        return jsonify({"status": "success", "message": "Registered successfully"})
    except:
        return jsonify({"status": "error", "message": "Username taken"}), 400
    finally:
        conn.close()

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, username, has_onboarded FROM users WHERE username=%s AND password=%s", 
                   (data['username'], hash_pass(data['password'])))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return jsonify({"status": "success", "user": user})
    return jsonify({"status": "error", "message": "Invalid credentials"}), 401

@app.route('/submit_onboarding', methods=['POST'])
def submit_onboarding():
    data = request.json
    uid = data['user_id']
    
    # 1. Generate AI Analysis first
    prompt = f"""
    Analyze this user for a career path:
    Name: {data['full_name']}
    Role: {data['current_role']} ({data['experience']} years)
    Education: {data['education']}
    Skills: {data['hard_skills']} (Soft: {data['soft_skills']})
    Aptitude: Solves problems via {data['prob_solving']}, acts as {data['team_role']}, learns by {data['learning_style']}.
    Interests: {data['interests']}
    Dream: {data['dream_goal']}
    
    Output HTML format (<h2>, <ul>, <li>, <p> only). 
    Provide: 1. Executive Summary, 2. Top 3 Career Paths, 3. Skill Gap Analysis.
    """
    
    try:
        response = ollama.chat(model=MODEL_NAME, messages=[{'role': 'user', 'content': prompt}])
        analysis = response['message']['content']
        
        conn = get_db()
        cursor = conn.cursor()
        
        # 2. Save Profile
        sql = """
        INSERT INTO profiles (user_id, full_name, age, education, current_role, experience, interests, dream_goal,
                              prob_solving, team_role, environment, learning_style, hard_skills, soft_skills, missing_skill, analysis_result)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        vals = (uid, data['full_name'], data['age'], data['education'], data['current_role'], data['experience'], data['interests'], data['dream_goal'],
                data['prob_solving'], data['team_role'], data['environment'], data['learning_style'], data['hard_skills'], data['soft_skills'], data['missing_skill'], analysis)
        
        cursor.execute(sql, vals)
        
        # 3. Update User Status
        cursor.execute("UPDATE users SET has_onboarded = 1 WHERE id = %s", (uid,))
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success"})
    except Exception as e:
        print(e)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_dashboard', methods=['POST'])
def get_dashboard():
    uid = request.json['user_id']
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM profiles WHERE user_id = %s", (uid,))
    profile = cursor.fetchone()
    conn.close()
    return jsonify(profile)

# --- ADD THIS TO YOUR app.py ---

@app.route('/update_profile', methods=['POST'])
def update_profile():
    data = request.json
    uid = data['user_id']
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Update query
    sql = """
    UPDATE profiles 
    SET full_name=%s, age=%s, education=%s, current_role=%s, hard_skills=%s, dream_goal=%s
    WHERE user_id=%s
    """
    vals = (data['full_name'], data['age'], data['education'], data['current_role'], data['hard_skills'], data['dream_goal'], uid)
    
    try:
        cursor.execute(sql, vals)
        conn.commit()
        return jsonify({"status": "success", "message": "Profile Updated"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/chat', methods=['POST'])
def chat():
    uid = request.json['user_id']
    user_msg = request.json['message']

    # ---------------------------------
    # LOAD USER PROFILE FOR CONTEXT
    # ---------------------------------
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM profiles WHERE user_id=%s", (uid,))
    profile = cursor.fetchone()
    conn.close()

    profile_context = f"""
    User Profile:
    - Name: {profile['full_name']}
    - Age: {profile['age']}
    - Education: {profile['education']}
    - Current Role: {profile['current_role']}
    - Experience: {profile['experience']} years
    - Interests: {profile['interests']}
    - Dream Goal: {profile['dream_goal']}
    - Hard Skills: {profile['hard_skills']}
    - Soft Skills: {profile['soft_skills']}
    - Missing Skill: {profile['missing_skill']}
    - Problem Solving Style: {profile['prob_solving']}
    - Team Style: {profile['team_role']}
    - Ideal Work Environment: {profile['environment']}
    - Learning Style: {profile['learning_style']}
    """

    # ---------------------------------
    # INITIALIZE MEMORY IF USER = NEW
    # ---------------------------------
    if uid not in chat_memory:
        chat_memory[uid] = []
    
    # ---------------------------------
    # ADD USER MESSAGE TO MEMORY
    # ---------------------------------
    chat_memory[uid].append({"role": "user", "content": user_msg})

    # ---------------------------------
    # TRIM MEMORY (only last 10 messages)
    # ---------------------------------
    chat_memory[uid] = chat_memory[uid][-10:]

    # ---------------------------------
    # BUILD MESSAGE STACK FOR OLLAMA
    # ---------------------------------
    messages = [
        {"role": "system", "content": "You are a personalized AI career mentor who remembers previous messages."},
        {"role": "assistant", "content": f"Here is the user's profile for reference:\n{profile_context}"}
    ]

    # Add conversation history
    messages.extend(chat_memory[uid])

    # ---------------------------------
    # GET AI RESPONSE
    # ---------------------------------
    response = ollama.chat(model=MODEL_NAME, messages=messages)
    ai_reply = response["message"]["content"]

    # ---------------------------------
    # STORE AI REPLY IN MEMORY
    # ---------------------------------
    chat_memory[uid].append({"role": "assistant", "content": ai_reply})

    return jsonify({"reply": ai_reply})



if __name__ == '__main__':
    app.run(debug=True, port=5000)