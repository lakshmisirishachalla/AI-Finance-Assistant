import os
import sqlite3
from datetime import date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

# Optional machine learning
try:
    from sklearn.feature_extraction.text import CountVectorizer
    from sklearn.naive_bayes import MultinomialNB

    descriptions = [
        "lunch food restaurant dinner groceries",
        "bus train petrol taxi travel",
        "shopping clothes shoes online",
        "school college books tuition",
        "doctor hospital medicine pharmacy",
        "monthly salary job income",
        "electricity water internet bill",
        "movie entertainment game",
        "rent house apartment",
        "freelance business payment"
    ]

    labels = [
        "Food", "Travel", "Shopping", "Education",
        "Medical", "Salary", "Bills", "Entertainment",
        "Rent", "Other"
    ]

    vectorizer = CountVectorizer()
    X = vectorizer.fit_transform(descriptions)
    model = MultinomialNB()
    model.fit(X, labels)
    ML_AVAILABLE = True

except ImportError:
    ML_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates")
)
app.secret_key = os.environ.get(
    "SECRET_KEY", "change-this-secret-key"
)

DB_PATH = os.path.join(BASE_DIR, "database.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            date TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            month TEXT NOT NULL,
            UNIQUE(user_id, category, month)
        )
    """)

    conn.commit()
    conn.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def predict_category(description):
    if not description:
        return "Other"

    if ML_AVAILABLE:
        prediction = model.predict(
            vectorizer.transform([description.lower()])
        )
        return prediction[0]

    keywords = {
        "Food": ["food", "lunch", "dinner", "restaurant", "grocery"],
        "Travel": ["bus", "train", "petrol", "taxi", "travel"],
        "Shopping": ["shop", "clothes", "shoes"],
        "Education": ["book", "college", "school", "course"],
        "Medical": ["doctor", "medicine", "hospital"],
        "Bills": ["electricity", "water", "internet", "bill"],
        "Entertainment": ["movie", "game", "entertainment"],
        "Rent": ["rent", "house"]
    }

    text = description.lower()
    for category, words in keywords.items():
        if any(word in text for word in words):
            return category

    return "Other"


@app.route("/")
def home():
    if "user_id" in session:
        return redirect("/dashboard")
    return redirect("/login")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if not name or not email or len(password) < 6:
            flash("Enter valid details. Password must have 6 characters.")
            return redirect("/register")

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (name,email,password) VALUES (?,?,?)",
                (name, email, generate_password_hash(password))
            )
            conn.commit()
            flash("Registration successful! Please login.")
            return redirect("/login")
        except sqlite3.IntegrityError:
            flash("Email already registered.")
        finally:
            conn.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email=?", (email,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            return redirect("/dashboard")

        flash("Invalid email or password.")

    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    uid = session["user_id"]
    current_month = date.today().strftime("%Y-%m")

    income = conn.execute("""
        SELECT COALESCE(SUM(amount),0) FROM transactions
        WHERE user_id=? AND type='income'
    """, (uid,)).fetchone()[0]

    expenses = conn.execute("""
        SELECT COALESCE(SUM(amount),0) FROM transactions
        WHERE user_id=? AND type='expense'
    """, (uid,)).fetchone()[0]

    transactions = conn.execute("""
        SELECT * FROM transactions
        WHERE user_id=? ORDER BY date DESC, id DESC
    """, (uid,)).fetchall()

    category_data = conn.execute("""
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE user_id=? AND type='expense'
        GROUP BY category
    """, (uid,)).fetchall()

    monthly_data = conn.execute("""
        SELECT substr(date,1,7) AS month, SUM(amount) AS total
        FROM transactions
        WHERE user_id=? AND type='expense'
        GROUP BY substr(date,1,7)
        ORDER BY month DESC LIMIT 6
    """, (uid,)).fetchall()

    budget = conn.execute("""
        SELECT * FROM budgets
        WHERE user_id=? AND month=?
    """, (uid, current_month)).fetchall()

    budget_spending = conn.execute("""
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE user_id=? AND type='expense'
        AND substr(date,1,7)=?
        GROUP BY category
    """, (uid, current_month)).fetchall()

    conn.close()

    spending_map = {
        row["category"]: row["total"] for row in budget_spending
    }

    budget_info = []
    for item in budget:
        spent = spending_map.get(item["category"], 0)
        budget_info.append({
            "category": item["category"],
            "amount": item["amount"],
            "spent": spent,
            "remaining": item["amount"] - spent
        })

    top_category = max(
        category_data,
        key=lambda x: x["total"],
        default=None
    )

    monthly_values = [row["total"] for row in monthly_data]
    forecast = (
        sum(monthly_values[:3]) / len(monthly_values[:3])
        if monthly_values else 0
    )

    insights = []
    if income == 0 and expenses == 0:
        insights.append("Start by adding your income and expenses.")
    elif expenses > income:
        insights.append("Your expenses are higher than your income.")
    else:
        insights.append("Your spending is currently within your income.")

    if top_category:
        insights.append(
            f"Your highest spending category is {top_category['category']}."
        )

    insights.append(
        f"Your estimated monthly spending is ₹{forecast:.2f}."
    )

    return render_template(
        "dashboard.html",
        name=session["name"],
        income=income,
        expenses=expenses,
        balance=income-expenses,
        transactions=transactions,
        category_data=category_data,
        monthly_data=list(reversed(monthly_data)),
        budget_info=budget_info,
        insights=insights,
        forecast=forecast,
        ml_available=ML_AVAILABLE
    )


@app.route("/add_transaction", methods=["POST"])
@login_required
def add_transaction():
    transaction_type = request.form.get("type")
    category = request.form.get("category")
    amount = request.form.get("amount", type=float)
    description = request.form.get("description", "").strip()
    transaction_date = request.form.get("date")

    if transaction_type not in ["income", "expense"]:
        flash("Select a valid transaction type.")
        return redirect("/dashboard")

    if not amount or amount <= 0 or not transaction_date:
        flash("Enter a valid amount and date.")
        return redirect("/dashboard")

    if category == "Auto Detect" or not category:
        category = predict_category(description)

    conn = get_db()
    conn.execute("""
        INSERT INTO transactions
        (user_id,type,category,amount,description,date)
        VALUES (?,?,?,?,?,?)
    """, (
        session["user_id"], transaction_type,
        category, amount, description, transaction_date
    ))
    conn.commit()
    conn.close()

    flash("Transaction added successfully!")
    return redirect("/dashboard")


@app.route("/edit/<int:transaction_id>", methods=["GET", "POST"])
@login_required
def edit_transaction(transaction_id):
    conn = get_db()
    uid = session["user_id"]

    transaction = conn.execute("""
        SELECT * FROM transactions WHERE id=? AND user_id=?
    """, (transaction_id, uid)).fetchone()

    if not transaction:
        conn.close()
        return "Transaction not found", 404

    if request.method == "POST":
        amount = request.form.get("amount", type=float)
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        transaction_date = request.form.get("date")

        if not amount or amount <= 0 or not category or not transaction_date:
            flash("Please enter valid details.")
            conn.close()
            return redirect(f"/edit/{transaction_id}")

        conn.execute("""
            UPDATE transactions
            SET category=?, amount=?, description=?, date=?
            WHERE id=? AND user_id=?
        """, (
            category, amount, description,
            transaction_date, transaction_id, uid
        ))
        conn.commit()
        conn.close()
        flash("Transaction updated!")
        return redirect("/dashboard")

    conn.close()
    return render_template("edit.html", t=transaction)


@app.route("/delete/<int:transaction_id>", methods=["POST"])
@login_required
def delete_transaction(transaction_id):
    conn = get_db()
    conn.execute("""
        DELETE FROM transactions WHERE id=? AND user_id=?
    """, (transaction_id, session["user_id"]))
    conn.commit()
    conn.close()

    flash("Transaction deleted.")
    return redirect("/dashboard")


@app.route("/add_budget", methods=["POST"])
@login_required
def add_budget():
    category = request.form.get("category")
    amount = request.form.get("amount", type=float)
    month = request.form.get("month")

    if not category or not amount or amount <= 0 or not month:
        flash("Enter valid budget details.")
        return redirect("/dashboard")

    conn = get_db()
    conn.execute("""
        INSERT INTO budgets (user_id,category,amount,month)
        VALUES (?,?,?,?)
        ON CONFLICT(user_id,category,month)
        DO UPDATE SET amount=excluded.amount
    """, (session["user_id"], category, amount, month))
    conn.commit()
    conn.close()

    flash("Budget saved successfully!")
    return redirect("/dashboard")


@app.route("/chat", methods=["POST"])
@login_required
def chat():
    question = request.json.get("message", "").lower()

    conn = get_db()
    uid = session["user_id"]

    income = conn.execute("""
        SELECT COALESCE(SUM(amount),0) FROM transactions
        WHERE user_id=? AND type='income'
    """, (uid,)).fetchone()[0]

    expenses = conn.execute("""
        SELECT COALESCE(SUM(amount),0) FROM transactions
        WHERE user_id=? AND type='expense'
    """, (uid,)).fetchone()[0]

    top = conn.execute("""
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE user_id=? AND type='expense'
        GROUP BY category ORDER BY total DESC LIMIT 1
    """, (uid,)).fetchone()

    conn.close()

    if "income" in question:
        answer = f"Your total income is ₹{income:.2f}."
    elif "expense" in question or "spend" in question:
        answer = f"Your total expenses are ₹{expenses:.2f}."
    elif "balance" in question or "save" in question:
        answer = f"Your current balance is ₹{income-expenses:.2f}."
    elif "category" in question or "spending" in question:
        answer = (
            f"Your highest expense category is {top['category']} "
            f"with ₹{top['total']:.2f}."
            if top else "You have not added any expenses yet."
        )
    else:
        answer = (
            "I can help with your income, expenses, balance and "
            "highest spending category. Try asking one of these questions."
        )

    return jsonify({"answer": answer})


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


if __name__ == "__main__":
    init_db()
    app.run(debug=True)