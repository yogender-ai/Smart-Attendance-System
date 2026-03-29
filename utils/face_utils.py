"""
Smart Attendance Face Recognition Engine
=========================================
Uses OpenCV's DNN face detector (deep-learning based) for robust face detection,
combined with enhanced LBPH recognizer with multi-sample training.

The DNN face detector is far more robust than Haar Cascades for:
  - Different lighting conditions (rainy, sunny, indoor, outdoor)
  - Partial occlusion
  - Extreme angles
  
LBPH recognizer is trained with MULTIPLE augmented samples per person for:
  - Better tolerance to beards / no beards
  - Different expressions
  - Slight lighting variations

Philosophy: "Better to NOT detect, than to detect WRONG."
We use a strict confidence threshold so false positives are near-zero.
"""

import cv2
import numpy as np
import base64
import os
import json
import time
from database.db import get_db_connection, get_setting

FACE_DATA_DIR = "face_data"
TRAINER_PATH = os.path.join(FACE_DATA_DIR, "trainer.yml")
if not os.path.exists(FACE_DATA_DIR):
    os.makedirs(FACE_DATA_DIR)

# --- DNN Face Detector (Deep Learning, ships with OpenCV) ---
# Uses a pre-trained Caffe model for face detection (much better than Haar Cascades)
DNN_PROTO = os.path.join(FACE_DATA_DIR, "deploy.prototxt")
DNN_MODEL = os.path.join(FACE_DATA_DIR, "res10_300x300_ssd_iter_140000.caffemodel")
face_net = None

def get_face_detector():
    """Get or initialize the DNN face detector."""
    global face_net
    if face_net is not None:
        return face_net
    
    if os.path.exists(DNN_PROTO) and os.path.exists(DNN_MODEL):
        face_net = cv2.dnn.readNetFromCaffe(DNN_PROTO, DNN_MODEL)
        return face_net
    
    # Fallback to downloadable model — download it first time
    try:
        _download_dnn_model()
        face_net = cv2.dnn.readNetFromCaffe(DNN_PROTO, DNN_MODEL)
        return face_net
    except Exception as e:
        print(f"DNN model not available, falling back to Haar: {e}")
        return None


def _download_dnn_model():
    """Download the SSD face detection model files."""
    import urllib.request
    
    proto_url = "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt"
    model_url = "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"
    
    print("Downloading DNN face detection model (one-time)...")
    
    if not os.path.exists(DNN_PROTO):
        urllib.request.urlretrieve(proto_url, DNN_PROTO)
        print(f"  Downloaded: {DNN_PROTO}")
    
    if not os.path.exists(DNN_MODEL):
        urllib.request.urlretrieve(model_url, DNN_MODEL)
        print(f"  Downloaded: {DNN_MODEL}")
    
    print("DNN model ready!")


# --- LBPH Recognizer (Enhanced with multi-sample) ---
recognizer = cv2.face.LBPHFaceRecognizer_create(
    radius=1,
    neighbors=8,
    grid_x=8,
    grid_y=8
)

# Load existing trainer if available
if os.path.exists(TRAINER_PATH):
    recognizer.read(TRAINER_PATH)

# --- Haar cascades for eye detection (blink liveness) ---
eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml")
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

# --- Anti-Spoofing Globals ---
face_size_history = []
MAX_HISTORY = 10


def data_uri_to_cv2_img(uri):
    """Convert a base64 data URI to an OpenCV image."""
    encoded_data = uri.split(',')[1]
    nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def detect_faces_dnn(img_bgr, confidence_threshold=0.65):
    """
    Detect faces using DNN (deep learning) detector.
    Returns list of (x, y, w, h) face rectangles.
    Falls back to Haar Cascades if DNN model is unavailable.
    """
    net = get_face_detector()
    
    if net is None:
        # Fallback to Haar Cascades
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(80, 80))
        return list(faces)
    
    (h, w) = img_bgr.shape[:2]
    blob = cv2.dnn.blobFromImage(
        cv2.resize(img_bgr, (300, 300)), 
        1.0, (300, 300), 
        (104.0, 177.0, 123.0)
    )
    net.setInput(blob)
    detections = net.forward()
    
    faces = []
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence > confidence_threshold:
            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            (x1, y1, x2, y2) = box.astype("int")
            
            # Clamp to image bounds
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)
            
            face_w = x2 - x1
            face_h = y2 - y1
            
            if face_w > 40 and face_h > 40:
                faces.append((x1, y1, face_w, face_h))
    
    return faces


def augment_face(gray_face, size=(200, 200)):
    """
    Create augmented versions of a face for better recognition training.
    Generates variations in brightness, contrast, and slight rotations.
    """
    augmented = []
    resized = cv2.resize(gray_face, size)
    augmented.append(resized)
    
    # Brightness variations
    for beta in [-25, -10, 10, 25]:
        bright = cv2.convertScaleAbs(resized, alpha=1.0, beta=beta)
        augmented.append(bright)
    
    # Contrast variations
    for alpha in [0.85, 1.15]:
        contrast = cv2.convertScaleAbs(resized, alpha=alpha, beta=0)
        augmented.append(contrast)
    
    # Slight blur (simulates distance/motion)
    blurred = cv2.GaussianBlur(resized, (3, 3), 0)
    augmented.append(blurred)
    
    # Histogram equalization (normalizes lighting)
    equalized = cv2.equalizeHist(resized)
    augmented.append(equalized)
    
    # CLAHE (adaptive histogram) - best for uneven lighting
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    clahe_img = clahe.apply(resized)
    augmented.append(clahe_img)
    
    # Horizontal flip
    flipped = cv2.flip(resized, 1)
    augmented.append(flipped)
    
    return augmented


# ============================================================
#  ANTI-SPOOFING DETECTION ENGINE
# ============================================================

def analyze_texture_lbp(face_roi_gray):
    """LBP texture variance. Real faces have high variance; photos/screens are flat."""
    if face_roi_gray is None or face_roi_gray.size == 0:
        return 0
    
    face_resized = cv2.resize(face_roi_gray, (128, 128))
    
    lbp = np.zeros_like(face_resized, dtype=np.uint8)
    for i in range(1, face_resized.shape[0] - 1):
        for j in range(1, face_resized.shape[1] - 1):
            center = int(face_resized[i, j])
            code = 0
            code |= (int(face_resized[i-1, j-1]) >= center) << 7
            code |= (int(face_resized[i-1, j  ]) >= center) << 6
            code |= (int(face_resized[i-1, j+1]) >= center) << 5
            code |= (int(face_resized[i  , j+1]) >= center) << 4
            code |= (int(face_resized[i+1, j+1]) >= center) << 3
            code |= (int(face_resized[i+1, j  ]) >= center) << 2
            code |= (int(face_resized[i+1, j-1]) >= center) << 1
            code |= (int(face_resized[i  , j-1]) >= center) << 0
            lbp[i, j] = code
    
    variance = np.var(lbp.astype(np.float64))
    score = min(100, int((variance / 3000) * 100))
    return score


def analyze_edge_density(face_roi_gray):
    """Edge density using Canny. Real faces have complex edges."""
    if face_roi_gray is None or face_roi_gray.size == 0:
        return 0
    
    face_resized = cv2.resize(face_roi_gray, (128, 128))
    edges = cv2.Canny(face_resized, 50, 150)
    edge_ratio = np.count_nonzero(edges) / edges.size
    score = min(100, int((edge_ratio / 0.15) * 100))
    return score


def analyze_color_distribution(img_bgr, x, y, w, h):
    """Color analysis for skin tone and channel variance."""
    face_color = img_bgr[y:y+h, x:x+w]
    
    if face_color.size == 0:
        return 0
    
    hsv = cv2.cvtColor(face_color, cv2.COLOR_BGR2HSV)
    lower_skin = np.array([0, 20, 70], dtype=np.uint8)
    upper_skin = np.array([20, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower_skin, upper_skin)
    skin_ratio = np.count_nonzero(mask) / mask.size
    
    b, g, r = cv2.split(face_color)
    channel_vars = [np.var(b.astype(np.float64)), np.var(g.astype(np.float64)), np.var(r.astype(np.float64))]
    avg_var = np.mean(channel_vars)
    
    skin_score = min(50, int(skin_ratio * 100))
    var_score = min(50, int((avg_var / 1500) * 50))
    
    return skin_score + var_score


def analyze_frequency_domain(face_roi_gray):
    """FFT frequency analysis. Screens have moiré patterns."""
    if face_roi_gray is None or face_roi_gray.size == 0:
        return 0
    
    face_resized = cv2.resize(face_roi_gray, (128, 128)).astype(np.float64)
    
    f_transform = np.fft.fft2(face_resized)
    f_shift = np.fft.fftshift(f_transform)
    magnitude = np.abs(f_shift)
    
    h, w = magnitude.shape
    center_h, center_w = h // 2, w // 2
    mask_size = int(min(h, w) * 0.15)
    low_freq = magnitude[center_h - mask_size:center_h + mask_size, 
                          center_w - mask_size:center_w + mask_size]
    
    total_energy = np.sum(magnitude)
    low_energy = np.sum(low_freq)
    
    if total_energy == 0:
        return 50
    
    ratio = low_energy / total_energy
    
    if 0.3 <= ratio <= 0.85:
        score = 80
    else:
        score = 30
    
    return min(100, score)


def check_face_size_consistency(x, y, w, h):
    """Track face for micro-movements. Static = suspicious."""
    global face_size_history
    
    face_size_history.append((w, h, x, y, time.time()))
    
    if len(face_size_history) > MAX_HISTORY:
        face_size_history.pop(0)
    
    if len(face_size_history) < 3:
        return 50
    
    x_positions = [f[2] for f in face_size_history]
    y_positions = [f[3] for f in face_size_history]
    
    x_var = np.var(x_positions)
    y_var = np.var(y_positions)
    
    movement_score = min(50, int(((x_var + y_var) / 20) * 50))
    
    sizes = [f[0] * f[1] for f in face_size_history]
    size_var = np.var(sizes) if len(sizes) > 1 else 0
    size_score = min(50, int((size_var / 500) * 50))
    
    return movement_score + size_score


def compute_anti_spoof_score(face_roi_gray, img_bgr, x, y, w, h, has_eyes):
    """Composite anti-spoofing score (6 checks)."""
    texture_score = analyze_texture_lbp(face_roi_gray)
    edge_score = analyze_edge_density(face_roi_gray)
    color_score = analyze_color_distribution(img_bgr, x, y, w, h)
    frequency_score = analyze_frequency_domain(face_roi_gray)
    consistency_score = check_face_size_consistency(x, y, w, h)
    eye_score = 100 if has_eyes else 0
    
    checks = {
        "texture": texture_score > 35,
        "edge_density": edge_score > 25,
        "color_analysis": color_score > 30,
        "frequency": frequency_score > 40,
        "face_consistency": consistency_score > 20,
        "eye_presence": has_eyes
    }
    
    composite = (
        texture_score * 0.20 +
        edge_score * 0.15 +
        color_score * 0.15 +
        frequency_score * 0.15 +
        consistency_score * 0.15 +
        eye_score * 0.20
    )
    
    return int(composite), checks


# ============================================================
#  FACE REGISTRATION (Enhanced Multi-Sample LBPH)
# ============================================================

def register_face(user_id, base64_img):
    """
    Register a face with augmented multi-sample training.
    Each call captures one frame, augments it into ~12 training samples,
    and retrains the LBPH model.
    """
    img_bgr = data_uri_to_cv2_img(base64_img)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    
    # Use DNN detector for robust face detection
    faces = detect_faces_dnn(img_bgr)
    
    if len(faces) == 0:
        return False
    
    # Pick largest face
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    (x, y, w, h) = faces[0]
    
    face_roi = gray[y:y+h, x:x+w]
    
    if face_roi.size == 0:
        return False
    
    # CLAHE normalize for consistent training across lighting
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    face_roi = clahe.apply(face_roi)
    
    # Generate augmented samples
    augmented_faces = augment_face(face_roi, size=(200, 200))
    
    # Collect all training data (existing + new)
    all_faces = []
    all_labels = []
    
    # Load existing training data from saved images
    for filename in os.listdir(FACE_DATA_DIR):
        if filename.endswith('.jpg') or filename.endswith('.png'):
            parts = filename.split('_')
            if len(parts) >= 2:
                try:
                    label = int(parts[1])
                    filepath = os.path.join(FACE_DATA_DIR, filename)
                    img = cv2.imread(filepath, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        img_resized = cv2.resize(img, (200, 200))
                        all_faces.append(img_resized)
                        all_labels.append(label)
                except (ValueError, IndexError):
                    continue
    
    # Save new augmented samples
    existing_count = len([f for f in os.listdir(FACE_DATA_DIR) 
                          if f.startswith(f'face_{user_id}_') and (f.endswith('.jpg') or f.endswith('.png'))])
    
    for i, aug_face in enumerate(augmented_faces):
        filename = f"face_{user_id}_{existing_count + i}.jpg"
        filepath = os.path.join(FACE_DATA_DIR, filename)
        cv2.imwrite(filepath, aug_face)
        all_faces.append(aug_face)
        all_labels.append(user_id)
    
    # Train LBPH recognizer with all data
    if len(all_faces) > 0:
        recognizer.train(all_faces, np.array(all_labels))
        recognizer.save(TRAINER_PATH)
    
    # Mark face as registered
    conn = get_db_connection()
    conn.execute("UPDATE users SET face_registered = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    return True


# ============================================================
#  FACE RECOGNITION (Enhanced)
# ============================================================

def recognize_face_with_liveness(base64_img):
    """
    Enhanced recognition with DNN face detection + LBPH matching + anti-spoofing.
    
    Returns: (user_id, has_eyes, confidence, anti_spoof_score, spoof_checks)
    confidence: 0-100 (higher = better match)
    """
    img_bgr = data_uri_to_cv2_img(base64_img)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    
    # Use DNN detector for robust detection
    faces = detect_faces_dnn(img_bgr)
    
    if len(faces) == 0:
        return None, False, 0, 0, {}
    
    # Reject if multiple faces detected
    if len(faces) > 1:
        return None, False, 0, 0, {"multi_face": True}
    
    (x, y, w, h) = faces[0]
    
    # Eye detection for blink-based liveness
    face_upper_half = gray[y:y + h // 2, x:x + w]
    eyes = eye_cascade.detectMultiScale(face_upper_half, 1.1, 3) if face_upper_half.size > 0 else []
    has_eyes = len(eyes) > 0
    
    # Anti-spoofing analysis
    face_roi_gray = gray[y:y+h, x:x+w]
    anti_spoof_score, spoof_checks = compute_anti_spoof_score(
        face_roi_gray, img_bgr, x, y, w, h, has_eyes
    )
    
    # Check if trainer exists
    if not os.path.exists(TRAINER_PATH):
        return None, has_eyes, 0, anti_spoof_score, spoof_checks
    
    # Normalize face for recognition
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    face_normalized = clahe.apply(face_roi_gray)
    face_resized = cv2.resize(face_normalized, (200, 200))
    
    # LBPH recognition
    try:
        label, distance = recognizer.predict(face_resized)
    except Exception:
        return None, has_eyes, 0, anti_spoof_score, spoof_checks
    
    # Convert LBPH distance to confidence (0-100)
    # LBPH distance: 0 = perfect match, >100 = no match
    # Strict threshold: distance < 65 for positive match
    threshold = 65
    
    if distance < threshold:
        confidence = max(0, min(100, int((1 - distance / threshold) * 100)))
        return label, has_eyes, confidence, anti_spoof_score, spoof_checks
    
    # Not recognized — report rough confidence
    rough_confidence = max(0, int((1 - distance / 200) * 50))
    return None, has_eyes, rough_confidence, anti_spoof_score, spoof_checks
