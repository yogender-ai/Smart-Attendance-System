"""
SmartFace Input Validation Engine
==================================
Centralized, bulletproof validation for every form field.
All validation returns (cleaned_value, error_message).
If error_message is None, validation passed.
"""

import re

# Allowed departments — prevents freetext injection
ALLOWED_DEPARTMENTS = [
    'IT', 'HR', 'Sales', 'Operations', 'Finance',
    'Marketing', 'Design', 'Management', 'Engineering',
    'Support', 'Legal', 'Research'
]

ALLOWED_STATUSES = ['Present', 'Absent', 'Late', 'Leave']


def validate_name(name):
    """
    Name validation:
    - Strip leading/trailing whitespace
    - Collapse multiple spaces to single space
    - Only letters, spaces, hyphens, apostrophes allowed
    - 2-50 characters
    - Auto-capitalize each word (title case)
    """
    if not name or not name.strip():
        return None, "Name is required."

    # Strip and collapse multiple spaces
    name = re.sub(r'\s+', ' ', name.strip())

    if len(name) < 2:
        return None, "Name must be at least 2 characters."
    if len(name) > 50:
        return None, "Name must be less than 50 characters."

    # Only letters, spaces, hyphens, apostrophes
    if not re.match(r"^[A-Za-z\s\-']+$", name):
        return None, "Name can only contain letters, spaces, hyphens, and apostrophes."

    # Must contain at least one letter
    if not any(c.isalpha() for c in name):
        return None, "Name must contain at least one letter."

    # Title case
    name = name.title()

    return name, None


def validate_employee_id(emp_id):
    """
    Employee ID validation:
    - 3-15 characters
    - Alphanumeric + hyphens only
    - Auto-uppercase
    - No spaces
    """
    if not emp_id or not emp_id.strip():
        return None, "Employee ID is required."

    emp_id = emp_id.strip().upper()

    if len(emp_id) < 3:
        return None, "Employee ID must be at least 3 characters."
    if len(emp_id) > 15:
        return None, "Employee ID must be less than 15 characters."

    if not re.match(r'^[A-Z0-9\-]+$', emp_id):
        return None, "Employee ID can only contain letters, numbers, and hyphens."

    if emp_id.startswith('-') or emp_id.endswith('-'):
        return None, "Employee ID cannot start or end with a hyphen."

    return emp_id, None


def validate_email(email):
    """
    Email validation:
    - Valid format
    - Lowercase conversion
    - No spaces
    - Max 100 chars
    """
    if not email or not email.strip():
        return None, "Email is required."

    email = email.strip().lower()

    if len(email) > 100:
        return None, "Email must be less than 100 characters."

    # RFC-compliant email pattern (simplified but thorough)
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return None, "Please enter a valid email address."

    # No consecutive dots
    if '..' in email:
        return None, "Email cannot contain consecutive dots."

    return email, None


def validate_phone(phone):
    """
    Phone validation:
    - Strip +, spaces, dashes, parentheses
    - Must be 10-15 digits only
    """
    if not phone or not phone.strip():
        return None, "Phone number is required."

    # Strip common formatting characters
    cleaned = re.sub(r'[\s\-\+\(\)]', '', phone.strip())

    if not cleaned.isdigit():
        return None, "Phone number must contain only digits."

    if len(cleaned) < 10:
        return None, "Phone number must be at least 10 digits."
    if len(cleaned) > 15:
        return None, "Phone number must be less than 15 digits."

    return cleaned, None


def validate_password(password):
    """
    Password validation:
    - Minimum 8 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 digit
    - At least 1 special character
    - Max 128 chars
    """
    if not password:
        return None, "Password is required."

    if len(password) < 8:
        return None, "Password must be at least 8 characters."
    if len(password) > 128:
        return None, "Password must be less than 128 characters."

    if not re.search(r'[A-Z]', password):
        return None, "Password must contain at least one uppercase letter."
    if not re.search(r'[a-z]', password):
        return None, "Password must contain at least one lowercase letter."
    if not re.search(r'[0-9]', password):
        return None, "Password must contain at least one digit."
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'",.<>?/\\|`~]', password):
        return None, "Password must contain at least one special character (!@#$%^&* etc)."

    return password, None


def validate_department(department):
    """
    Department validation:
    - Must be from allowed list
    """
    if not department or not department.strip():
        return None, "Department is required."

    department = department.strip()

    if department not in ALLOWED_DEPARTMENTS:
        return None, f"Invalid department. Allowed: {', '.join(ALLOWED_DEPARTMENTS)}"

    return department, None


def validate_status(status):
    """Attendance status validation."""
    if not status or status not in ALLOWED_STATUSES:
        return None, f"Invalid status. Allowed: {', '.join(ALLOWED_STATUSES)}"
    return status, None


def validate_date(date_str):
    """Date validation (YYYY-MM-DD format)."""
    if not date_str:
        return None, "Date is required."

    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return None, "Invalid date format. Use YYYY-MM-DD."

    try:
        from datetime import datetime
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return None, "Invalid date."

    return date_str, None


def validate_time(time_str):
    """Time validation (HH:MM:SS format)."""
    if not time_str:
        return None, "Time is required."

    # Accept HH:MM or HH:MM:SS
    if re.match(r'^\d{2}:\d{2}$', time_str):
        time_str += ':00'

    if not re.match(r'^\d{2}:\d{2}:\d{2}$', time_str):
        return None, "Invalid time format. Use HH:MM:SS."

    parts = time_str.split(':')
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        return None, "Invalid time value."

    return time_str, None


def validate_profile_photo(file):
    """
    Profile photo validation:
    - Max 2MB
    - Only jpg, jpeg, png
    - Returns (is_valid, error_message)
    """
    if not file or file.filename == '':
        return False, "No file selected."

    allowed_extensions = {'jpg', 'jpeg', 'png'}
    filename = file.filename.lower()
    ext = filename.rsplit('.', 1)[1] if '.' in filename else ''

    if ext not in allowed_extensions:
        return False, "Only JPG and PNG files are allowed."

    # Check file size (2MB max) — read and seek back
    file.seek(0, 2)  # Seek to end
    size = file.tell()
    file.seek(0)  # Seek back to start

    if size > 2 * 1024 * 1024:
        return False, "Photo must be less than 2MB."

    return True, None


def validate_registration(form_data):
    """
    Validate entire registration form.
    Returns (cleaned_data, errors_dict).
    If errors_dict is empty, validation passed.
    """
    errors = {}
    cleaned = {}

    emp_id, err = validate_employee_id(form_data.get('employee_id', ''))
    if err:
        errors['employee_id'] = err
    else:
        cleaned['employee_id'] = emp_id

    name, err = validate_name(form_data.get('name', ''))
    if err:
        errors['name'] = err
    else:
        cleaned['name'] = name

    email, err = validate_email(form_data.get('email', ''))
    if err:
        errors['email'] = err
    else:
        cleaned['email'] = email

    phone, err = validate_phone(form_data.get('phone', ''))
    if err:
        errors['phone'] = err
    else:
        cleaned['phone'] = phone

    dept, err = validate_department(form_data.get('department', ''))
    if err:
        errors['department'] = err
    else:
        cleaned['department'] = dept

    password, err = validate_password(form_data.get('password', ''))
    if err:
        errors['password'] = err
    else:
        cleaned['password'] = password

    return cleaned, errors


def validate_profile_update(form_data):
    """
    Validate profile update form.
    Returns (cleaned_data, errors_dict).
    """
    errors = {}
    cleaned = {}

    name, err = validate_name(form_data.get('name', ''))
    if err:
        errors['name'] = err
    else:
        cleaned['name'] = name

    phone, err = validate_phone(form_data.get('phone', ''))
    if err:
        errors['phone'] = err
    else:
        cleaned['phone'] = phone

    dept, err = validate_department(form_data.get('department', ''))
    if err:
        errors['department'] = err
    else:
        cleaned['department'] = dept

    return cleaned, errors


def generate_secure_password(length=12):
    """Generate a secure random password meeting all requirements."""
    import random
    import string

    uppercase = random.choice(string.ascii_uppercase)
    lowercase = random.choice(string.ascii_lowercase)
    digit = random.choice(string.digits)
    special = random.choice('!@#$%^&*')

    remaining_length = length - 4
    all_chars = string.ascii_letters + string.digits + '!@#$%^&*'
    remaining = ''.join(random.choices(all_chars, k=remaining_length))

    password = list(uppercase + lowercase + digit + special + remaining)
    random.shuffle(password)

    return ''.join(password)
