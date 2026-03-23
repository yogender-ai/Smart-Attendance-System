import cv2
import numpy as np
import base64
import os
import time

FACE_DATA_DIR = "face_data"
if not os.path.exists(FACE_DATA_DIR):
    os.makedirs(FACE_DATA_DIR)

face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml")
profile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")

recognizer = cv2.face.LBPHFaceRecognizer_create()

# --- Anti-Spoofing Globals ---
face_size_history = []
MAX_HISTORY = 10
SPOOF_CHECKS = {
    "texture": False,
    "edge_density": False,
    "face_consistency": False,
    "size_variation": False,
    "eye_presence": False
}

def data_uri_to_cv2_img(uri):
    encoded_data = uri.split(',')[1]
    nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)

def detect_face(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    
    faces = face_cascade.detectMultiScale(
        gray, 
        scaleFactor=1.1, 
        minNeighbors=5, 
        minSize=(100, 100)
    )
    if len(faces) == 0:
        return None, None
        
    faces = sorted(faces, key=lambda x: x[2]*x[3], reverse=True)
    (x, y, w, h) = faces[0]
    face_roi_gray = gray[y:y+h, x:x+w]
    return face_roi_gray, (x, y, w, h)


# ============================================================
#  ANTI-SPOOFING DETECTION ENGINE
# ============================================================

def analyze_texture_lbp(face_roi):
    """
    LBP (Local Binary Pattern) variance analysis.
    Real faces have high texture variance; printed photos/screens are flatter.
    Returns score 0-100 (higher = more likely real).
    """
    if face_roi is None or face_roi.size == 0:
        return 0
    
    # Resize for consistent analysis
    face_resized = cv2.resize(face_roi, (128, 128))
    
    # Compute LBP manually
    lbp = np.zeros_like(face_resized, dtype=np.uint8)
    for i in range(1, face_resized.shape[0] - 1):
        for j in range(1, face_resized.shape[1] - 1):
            center = face_resized[i, j]
            code = 0
            code |= (face_resized[i-1, j-1] >= center) << 7
            code |= (face_resized[i-1, j  ] >= center) << 6
            code |= (face_resized[i-1, j+1] >= center) << 5
            code |= (face_resized[i  , j+1] >= center) << 4
            code |= (face_resized[i+1, j+1] >= center) << 3
            code |= (face_resized[i+1, j  ] >= center) << 2
            code |= (face_resized[i+1, j-1] >= center) << 1
            code |= (face_resized[i  , j-1] >= center) << 0
            lbp[i, j] = code
    
    variance = np.var(lbp)
    # Real faces typically have variance > 2000
    score = min(100, int((variance / 3000) * 100))
    return score


def analyze_edge_density(face_roi):
    """
    Edge density analysis using Canny edge detection.
    Real faces produce complex edge patterns; flat reproductions have fewer edges.
    Returns score 0-100.
    """
    if face_roi is None or face_roi.size == 0:
        return 0
    
    face_resized = cv2.resize(face_roi, (128, 128))
    edges = cv2.Canny(face_resized, 50, 150)
    edge_ratio = np.count_nonzero(edges) / edges.size
    
    # Real faces: ~8-20% edge density
    score = min(100, int((edge_ratio / 0.15) * 100))
    return score


def analyze_color_distribution(img, coords):
    """
    Analyze color channel distribution in the face region.
    Real faces have natural skin tone distributions; screens/photos differ.
    Returns score 0-100.
    """
    if coords is None:
        return 0
    
    (x, y, w, h) = coords
    face_color = img[y:y+h, x:x+w]
    
    if face_color.size == 0:
        return 0
    
    # Convert to HSV for skin analysis
    hsv = cv2.cvtColor(face_color, cv2.COLOR_BGR2HSV)
    
    # Skin tone detection in HSV space
    lower_skin = np.array([0, 20, 70], dtype=np.uint8)
    upper_skin = np.array([20, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower_skin, upper_skin)
    
    skin_ratio = np.count_nonzero(mask) / mask.size
    
    # Check color channel variance (real faces have more variance)
    b, g, r = cv2.split(face_color)
    channel_vars = [np.var(b), np.var(g), np.var(r)]
    avg_var = np.mean(channel_vars)
    
    skin_score = min(50, int(skin_ratio * 100))
    var_score = min(50, int((avg_var / 1500) * 50))
    
    return skin_score + var_score


def check_face_size_consistency(coords):
    """
    Track face bounding box across frames.
    Real faces have natural micro-movements; static = suspicious.
    Returns score 0-100.
    """
    global face_size_history
    
    if coords is None:
        face_size_history.clear()
        return 0
    
    (x, y, w, h) = coords
    face_size_history.append((w, h, x, y, time.time()))
    
    if len(face_size_history) > MAX_HISTORY:
        face_size_history.pop(0)
    
    if len(face_size_history) < 3:
        return 50  # Not enough data yet
    
    # Check for micro-movements (real faces are never perfectly still)
    x_positions = [f[2] for f in face_size_history]
    y_positions = [f[3] for f in face_size_history]
    
    x_var = np.var(x_positions)
    y_var = np.var(y_positions)
    
    # Real people have position variance > 2 pixels
    movement_score = min(50, int(((x_var + y_var) / 20) * 50))
    
    # Check size consistency (shouldn't be perfectly identical)
    sizes = [f[0] * f[1] for f in face_size_history]
    size_var = np.var(sizes) if len(sizes) > 1 else 0
    size_score = min(50, int((size_var / 500) * 50))
    
    return movement_score + size_score


def compute_anti_spoof_score(face_roi, img, coords, has_eyes):
    """
    Composite anti-spoofing score combining all detection methods.
    Returns (score 0-100, individual check results dict).
    """
    global SPOOF_CHECKS
    
    texture_score = analyze_texture_lbp(face_roi)
    edge_score = analyze_edge_density(face_roi)
    color_score = analyze_color_distribution(img, coords)
    consistency_score = check_face_size_consistency(coords)
    eye_score = 100 if has_eyes else 0
    
    SPOOF_CHECKS = {
        "texture": texture_score > 35,
        "edge_density": edge_score > 30,
        "color_analysis": color_score > 30,
        "face_consistency": consistency_score > 25,
        "eye_presence": has_eyes
    }
    
    # Weighted composite
    composite = (
        texture_score * 0.25 +
        edge_score * 0.20 +
        color_score * 0.20 +
        consistency_score * 0.15 +
        eye_score * 0.20
    )
    
    return int(composite), SPOOF_CHECKS


TRAINER_FILE = os.path.join(FACE_DATA_DIR, "trainer.yml")

def register_face(user_id, base64_img):
    img = data_uri_to_cv2_img(base64_img)
    face_roi, _ = detect_face(img)
    
    if face_roi is None:
        return False
        
    faces = [face_roi]
    ids = np.array([user_id])
    
    if os.path.exists(TRAINER_FILE):
        try:
            recognizer.read(TRAINER_FILE)
            recognizer.update(faces, ids)
        except Exception as e:
            recognizer.train(faces, ids)
    else:
        recognizer.train(faces, ids)
        
    recognizer.write(TRAINER_FILE)
    
    for filename in os.listdir(FACE_DATA_DIR):
        if filename.endswith(".jpg") or filename.endswith(".jpeg"):
            try:
                os.remove(os.path.join(FACE_DATA_DIR, filename))
            except:
                pass
                
    return True

try:
    if os.path.exists(TRAINER_FILE):
        recognizer.read(TRAINER_FILE)
except Exception as e:
    pass

def recognize_face_with_liveness(base64_img):
    """
    Enhanced recognition with full anti-spoofing pipeline.
    Returns: (user_id, has_eyes, confidence, anti_spoof_score, spoof_checks)
    """
    if not os.path.exists(os.path.join(FACE_DATA_DIR, "trainer.yml")):
        return None, False, 0, 0, {}
        
    img = data_uri_to_cv2_img(base64_img)
    face_roi, coords = detect_face(img)
    
    if face_roi is None:
        return None, False, 0, 0, {}
    
    # Eye detection for blink-based liveness
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    (x, y, w, h) = coords
    roi_color_eyes = gray[y:y + int(h/2), x:x+w]
    eyes = eye_cascade.detectMultiScale(roi_color_eyes, 1.1, 3)
    has_eyes = len(eyes) > 0
    
    # Full anti-spoofing analysis
    anti_spoof_score, spoof_checks = compute_anti_spoof_score(face_roi, img, coords, has_eyes)
    
    label, confidence = recognizer.predict(face_roi)
    
    # Tighter threshold: 75 instead of 85
    if confidence < 75:
        return label, has_eyes, confidence, anti_spoof_score, spoof_checks
    return None, has_eyes, confidence, anti_spoof_score, spoof_checks
