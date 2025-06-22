from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import firebase_admin
from firebase_admin import credentials, firestore
import hashlib
import uuid
from datetime import datetime, timedelta
import calendar
import json

app = Flask(__name__)
app.secret_key = "your-secret-key-here"  # Change this in production

# Initialize Firebase
cred = credentials.Certificate(
    "smartshiftroster-firebase-adminsdk-fbsvc-89a8220b0d.json"
)  # Update with your Firebase credentials
firebase_admin.initialize_app(cred)
db = firestore.client()


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password, hashed):
    return hash_password(password) == hashed


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        data = request.get_json()

        # Check if organization exists
        org_ref = (
            db.collection("organizations")
            .where("name", "==", data["company_name"])
            .limit(1)
        )
        existing_org = list(org_ref.stream())

        if existing_org:
            org_id = existing_org[0].id
            org_data = existing_org[0].to_dict()

            # Check if user is trying to register as superadmin
            if data["role"] == "superadmin":
                return jsonify({"error": "Organization already has a superadmin"}), 400

            # Create pending user for admin approval
            user_id = str(uuid.uuid4())
            user_data = {
                "id": user_id,
                "email": data["email"],
                "password": hash_password(data["password"]),
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "role": data["role"],
                "designation": data["designation"],
                "org_id": org_id,
                "status": "pending",
                "created_at": datetime.now(),
            }

            db.collection("pending_users").document(user_id).set(user_data)
            return jsonify({"message": "Registration submitted for approval"}), 200

        else:
            # Create new organization and superadmin user
            org_id = str(uuid.uuid4())
            org_data = {
                "id": org_id,
                "name": data["company_name"],
                "country": data["country"],
                "timezone": data["timezone"],
                "created_at": datetime.now(),
            }

            user_id = str(uuid.uuid4())
            user_data = {
                "id": user_id,
                "email": data["email"],
                "password": hash_password(data["password"]),
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "role": "superadmin",
                "designation": data["designation"],
                "org_id": org_id,
                "status": "active",
                "created_at": datetime.now(),
            }

            # Save to Firestore
            db.collection("organizations").document(org_id).set(org_data)
            db.collection("users").document(user_id).set(user_data)

            return (
                jsonify(
                    {"message": "Organization and superadmin created successfully"}
                ),
                201,
            )

    return render_template("register.html")

@app.route("/add_user", methods=["GET", "POST"])
def add_user():
    if "user_id" not in session or session["user_role"] not in ["admin", "superadmin"]:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        data = request.get_json()
        user_id = str(uuid.uuid4())
        user_data = {
            "id": user_id,
            "email": data["email"],
            "password": hash_password(data["password"]),
            "first_name": data["first_name"],
            "last_name": data["last_name"],
            "role": data["role"],
            "designation": data["designation"],
            "org_id": session["org_id"],
            "status": "inactive",  # User status will be 'inactive' by default
            "created_at": datetime.now(),
        }
        # Save user data to "pending_users" collection
        db.collection("pending_users").document(user_id).set(user_data)
        return jsonify({"message": "User created and awaiting admin approval."}), 201

    return render_template("add_user.html")



@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.get_json()

        # Find user by email
        users_ref = db.collection("users").where("email", "==", data["email"]).limit(1)
        users = list(users_ref.stream())

        if users and verify_password(data["password"], users[0].to_dict()["password"]):
            user_data = users[0].to_dict()
            if user_data["status"] == "active":
                session["user_id"] = user_data["id"]
                session["user_role"] = user_data["role"]
                session["org_id"] = user_data["org_id"]
                return jsonify({"message": "Login successful"}), 200
            else:
                return jsonify({"error": "Account pending approval"}), 400

        return jsonify({"error": "Invalid credentials"}), 401

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Check if user has any rosters
    rosters_ref = db.collection("rosters").where("org_id", "==", session["org_id"])
    rosters = list(rosters_ref.stream())

    if not rosters:
        return redirect(url_for("create_roster"))

    # Get current month calendar data
    today = datetime.now()
    cal = calendar.monthcalendar(today.year, today.month)
    
    # Calculate days in current month
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    # Get roster data for current month
    roster_data = {}
    for roster in rosters:
        roster_dict = roster.to_dict()
        if "schedule" in roster_dict:
            roster_data.update(roster_dict["schedule"])

    # Get all users in organization
    users_ref = db.collection("users").where("org_id", "==", session["org_id"])
    users = [user.to_dict() for user in users_ref.stream()]

    return render_template(
        "dashboard.html",
        calendar_data=cal,
        roster_data=roster_data,
        users=users,
        current_month=today.month,
        current_year=today.year,
        today=today.day,
        days_in_month=days_in_month,  # Add this line
        calendar=calendar
    )

@app.route("/create_roster", methods=["GET", "POST"])
def create_roster():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        data = request.get_json()

        roster_id = str(uuid.uuid4())
        roster_data = {
            "id": roster_id,
            "name": data["roster_name"],
            "org_id": session["org_id"],
            "selected_users": data["selected_users"],
            "schedule_type": data["schedule_type"],  # 24/7 or custom
            "rotation_type": data["rotation_type"],  # rotating or fixed
            "shift_hours": int(data["shift_hours"]),
            "min_users_per_shift": int(data["min_users_per_shift"]),
            "created_by": session["user_id"],
            "created_at": datetime.now(),
        }

        if data["creation_mode"] == "auto":
            # Auto-generate schedule
            roster_data["schedule"] = generate_auto_schedule(roster_data)

        db.collection("rosters").document(roster_id).set(roster_data)
        return jsonify({"message": "Roster created successfully"}), 201

    # Get all users in organization for selection
    users_ref = db.collection("users").where("org_id", "==", session["org_id"])
    users = [user.to_dict() for user in users_ref.stream()]

    return render_template("create_roster.html", users=users)


def generate_auto_schedule(roster_data):
    """Generate automatic schedule based on roster conditions"""
    schedule = {}
    users = roster_data["selected_users"]
    shifts_per_day = 24 // roster_data["shift_hours"]
    min_users = roster_data["min_users_per_shift"]

    # Generate for current month
    today = datetime.now()
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    user_index = 0
    for day in range(1, days_in_month + 1):
        date_key = f"{today.year}-{today.month:02d}-{day:02d}"
        schedule[date_key] = []

        for shift in range(shifts_per_day):
            shift_users = []
            for _ in range(min_users):
                if user_index >= len(users):
                    user_index = 0
                shift_users.append(users[user_index])
                user_index += 1

            shift_start = shift * roster_data["shift_hours"]
            shift_end = (shift + 1) * roster_data["shift_hours"]

            schedule[date_key].append(
                {
                    "shift_time": f"{shift_start:02d}:00-{shift_end:02d}:00",
                    "users": shift_users,
                }
            )

    return schedule


@app.route("/pending_users")
def pending_users():
    if "user_id" not in session or session["user_role"] not in ["admin", "superadmin"]:
        return redirect(url_for("dashboard"))

    pending_ref = db.collection("pending_users").where(
        "org_id", "==", session["org_id"]
    )
    pending = [user.to_dict() for user in pending_ref.stream()]

    return render_template("pending_users.html", pending_users=pending)


@app.route("/approve_user/<user_id>")
def approve_user(user_id):
    if "user_id" not in session or session["user_role"] not in ["admin", "superadmin"]:
        return redirect(url_for("dashboard"))

    # Move user from pending to active
    pending_ref = db.collection("pending_users").document(user_id)
    pending_user = pending_ref.get()

    if pending_user.exists:
        user_data = pending_user.to_dict()
        user_data["status"] = "active"

        # Add to users collection
        db.collection("users").document(user_id).set(user_data)

        # Remove from pending
        pending_ref.delete()

    return redirect(url_for("pending_users"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=3000, host="0.0.0.0")
