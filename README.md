# 🚀 Smart Attendance System

![Smart Attendance Banner](static/readme/banner.png)

<div align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-2.0.1-000000?style=for-the-badge&logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/OpenCV-4.5.3-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white" />
  <img src="https://img.shields.io/badge/SQLite-3-003B57?style=for-the-badge&logo=sqlite&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge" />
</div>

---

### 🌟 Overview
**Smart Attendance System** is a next-generation biometric solution that leverages **Artificial Intelligence** and **Computer Vision** to automate employee tracking. Built with a focus on speed, security, and a premium "glassmorphism" aesthetic, it provides a seamless experience for both administrators and employees.

> [!IMPORTANT]
> This system features **Liveness Detection** to prevent spoofing attempts using photos or videos, ensuring that only physical presence is recorded.

---

### ✨ Key Features

- 👤 **Face Recognition Engine**: High-accuracy detection and matching using OpenCV LBPH.
- 🛡️ **Anti-Spoofing & Liveness**: Real-time blink detection and depth verification to ensure high-security access.
- 📊 **Advanced Analytics**: Interactive dashboards with real-time attendance trends, heatmaps, and department-wise stats.
- 📱 **Responsive Dashboards**: Separate, tailored interfaces for **Administrators** (management & reports) and **Employees** (personal logs & status).
- 📥 **One-Click Export**: Export attendance data to CSV for easy integration with payroll and HR systems.
- 📧 **Automated Notifications**: Transactional emails for registrations and attendance alerts.
- ⚙️ **Customizable Settings**: Fine-tune face tolerance, late-cutoff times, and company branding via the admin panel.

---

### 🛠️ Tech Stack

| Category | Technology |
| :--- | :--- |
| **Backend** | Python / Flask |
| **Frontend** | HTML5 / CSS3 (Vanilla) / JavaScript |
| **Computer Vision** | OpenCV / NumPy |
| **Database** | SQLite3 |
| **Security** | Werkzeug Hashing / Flask-Login |
| **Reporting** | Pandas / CSV Service |

---

### 📸 UI Previews

![Scanner Mockup](static/readme/scanner_mockup.png)
*Figure 1: Futuristic Scanner Interface with real-time biometric locking.*

---

### 🚀 Getting Started

#### 1. Clone the Repository
```bash
git clone https://github.com/Sofzenix/Smart-Attendance-System.git
cd Smart-Attendance-System
```

#### 2. Environment Setup
Create a virtual environment and install dependencies:
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

#### 3. Configuration
Create or update `config.py` with your environment variables (e.g., Email credentials).

#### 4. Run the Application
```bash
python app.py
```
> [!TIP]
> After launching, navigate to `http://127.0.0.1:5000` to access the Scanner.

---

### 📂 Project Structure

```text
├── database/           # SQLite databases & init scripts
├── face_data/          # Registered biometric templates
├── static/             # CSS, JS, and UI assets
├── templates/          # Jinja2 HTML templates
├── utils/              # Face utilities & recognition logic
├── app.py              # Main Flask application
├── config.py           # Application configurations
└── retrain.py          # Script to update face model
```

---

### 🔒 Security & Privacy
We take biometric data seriously. All face templates are processed locally and stored securely on your server. Data is never shared with third-party cloud services.

---

### 🤝 Contributing
Contributions are welcome! Please feel free to submit a Pull Request.

---

### 📜 License
This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

<div align="center">
  <sub>Developed with ❤️ by <b>Sofzenix Technologies</b></sub>
</div>
