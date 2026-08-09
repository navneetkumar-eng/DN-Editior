"""
DN PDF Editor Pro - Authentication
Simple password protection for the app.
Add users here: USERNAME: PASSWORD
"""

# ── Add your users here ────────────────────────────────────────────────────────
# Format: "username": "password"
USERS = {
    "shivam":   "shivam@2024",
    "soumendra":  "Welcome@2024",
    "admin":    "dneditor@2024",
}

def check_login(username: str, password: str) -> bool:
    """Return True if username/password is valid."""
    return USERS.get(username.strip().lower()) == password.strip()
