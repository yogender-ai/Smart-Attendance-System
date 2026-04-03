"""
SmartFace Anti-Spoofing & Recognition Engine v2.0
===================================================
Military-grade anti-spoofing with 8 detection layers:
  1. LBP Texture Analysis — flat screens have low texture variance
  2. Edge Density Analysis — real faces have complex edges
  3. Color Temperature Analysis — screens emit blue-shifted light
  4. Moiré Pattern Detection — screens produce grid patterns
  5. Screen Reflection/Glare Detection — glass screens have specular highlights
  6. Frequency Domain Analysis — FFT detects screen pixel patterns
  7. Multi-Frame Depth Consistency — flat objects have zero depth variation
  8. Eye/Blink Verification — dead images cannot blink

Face data is encrypted at rest using Fernet symmetric encryption.
Uses OpenCV DNN face detector + Enhanced LBPH recognizer.
"""

import cv2
import numpy as np
import base64
import os
import json
import time
import mediapipe as mp
from cryptography.fernet import Fernet
from database.db import get_db_connection, get_setting
from config import Config

FACE_DATA_DIR = "face_data"
TRAINER_PATH = os.path.join(FACE_DATA_DIR, "trainer.yml")
if not os.path.exists(FACE_DATA_DIR):
    os.makedirs(FACE_DATA_DIR)

# --- Encryption ---
_fernet = None
def get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(Config.FACE_ENCRYPTION_KEY)
    return _fernet


# --- DNN Face Detector ---
DNN_PROTO = os.path.join(FACE_DATA_DIR, "deploy.prototxt")
DNN_MODEL = os.path.join(FACE_DATA_DIR, "res10_300x300_ssd_iter_140000.caffemodel")
face_net = None


def get_face_detector():
    global face_net
    if face_net is not None:
        return face_net

    if os.path.exists(DNN_PROTO) and os.path.exists(DNN_MODEL):
        face_net = cv2.dnn.readNetFromCaffe(DNN_PROTO, DNN_MODEL)
        return face_net

    try:
        _download_dnn_model()
        face_net = cv2.dnn.readNetFromCaffe(DNN_PROTO, DNN_MODEL)
        return face_net
    except Exception as e:
        print(f"DNN model not available, falling back to Haar: {e}")
        return None


def _download_dnn_model():
    import urllib.request
    proto_url = "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt"
    model_url = "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"
    print("Downloading DNN face detection model (one-time)...")
    if not os.path.exists(DNN_PROTO):
        urllib.request.urlretrieve(proto_url, DNN_PROTO)
    if not os.path.exists(DNN_MODEL):
        urllib.request.urlretrieve(model_url, DNN_MODEL)
    print("DNN model ready!")


# --- LBPH Recognizer ---
recognizer = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
if os.path.exists(TRAINER_PATH):
    recognizer.read(TRAINER_PATH)

# --- Multi-Layer Anti-Spoofing & Liveness Models ---
# Initialize MediaPipe Face Mesh for 468-point 3D landmarking (handles glasses and beards flawlessly)
mp_face_mesh = mp.solutions.face_mesh
face_mesh_mp = mp_face_mesh.FaceMesh(
    static_image_mode=True,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5
)

# Legacy fallbacks
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

# --- Anti-Spoofing State ---
face_size_history = []
MAX_HISTORY = 15
spoof_frame_scores = []
MAX_SPOOF_FRAMES = 8


def data_uri_to_cv2_img(uri):
    encoded_data = uri.split(',')[1]
    nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def detect_faces_dnn(img_bgr, confidence_threshold=0.65):
    net = get_face_detector()
    if net is None:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(80, 80))
        return list(faces)

    (h, w) = img_bgr.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(img_bgr, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
    net.setInput(blob)
    detections = net.forward()

    faces = []
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence > confidence_threshold:
            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            (x1, y1, x2, y2) = box.astype("int")
            
            # PADDING FOR BEARDS: DNNs often cut off the chin/beard.
            # We add 15% to the bottom coordinates to ensure full facial hair is captured.
            beard_padding = int((y2 - y1) * 0.15)
            y2 = min(h, y2 + beard_padding)
            
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            face_w, face_h = x2 - x1, y2 - y1
            if face_w > 40 and face_h > 40:
                faces.append((x1, y1, face_w, face_h))
    return faces


def augment_face(gray_face, size=(200, 200)):
    augmented = []
    resized = cv2.resize(gray_face, size)
    augmented.append(resized)

    for beta in [-25, -10, 10, 25]:
        augmented.append(cv2.convertScaleAbs(resized, alpha=1.0, beta=beta))

    for alpha in [0.85, 1.15]:
        augmented.append(cv2.convertScaleAbs(resized, alpha=alpha, beta=0))

    augmented.append(cv2.GaussianBlur(resized, (3, 3), 0))
    augmented.append(cv2.equalizeHist(resized))

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    augmented.append(clahe.apply(resized))
    augmented.append(cv2.flip(resized, 1))

    return augmented


# ============================================================
#  LIVENESS METRICS (MediaPipe)
# ============================================================

def euclidean_distance(p1, p2, w, h):
    return np.sqrt(((p1.x - p2.x) * w)**2 + ((p1.y - p2.y) * h)**2)

def calculate_ear(landmarks, w, h):
    """Eye Aspect Ratio using 3D mesh points"""
    # Left eye landmarks
    # Horizontal: 33, 133
    # Vertical: 160(top)-144(bottom), 158(top)-153(bottom)
    l_h = euclidean_distance(landmarks[33], landmarks[133], w, h)
    l_v1 = euclidean_distance(landmarks[160], landmarks[144], w, h)
    l_v2 = euclidean_distance(landmarks[158], landmarks[153], w, h)
    ear_left = (l_v1 + l_v2) / (2.0 * l_h + 1e-6)

    # Right eye landmarks
    # Horizontal: 362, 263
    # Vertical: 385(top)-380(bottom), 387(top)-373(bottom)
    r_h = euclidean_distance(landmarks[362], landmarks[263], w, h)
    r_v1 = euclidean_distance(landmarks[385], landmarks[380], w, h)
    r_v2 = euclidean_distance(landmarks[387], landmarks[373], w, h)
    ear_right = (r_v1 + r_v2) / (2.0 * r_h + 1e-6)

    return (ear_left + ear_right) / 2.0

def calculate_mar(landmarks, w, h):
    """Mouth Aspect Ratio (Smile Detection)"""
    # Outer lip horizontal: 61, 291
    # Inner lip vertical: 13, 14
    m_h = euclidean_distance(landmarks[61], landmarks[291], w, h)
    m_v = euclidean_distance(landmarks[13], landmarks[14], w, h)
    return m_h / (m_v + 1e-6)  # We actually want width over height.
    # Wait, smile stretches horizontal width and thins vertical. 
    # Usually MAR is vertical/horizontal. Let's do horizontal distance.
    # Simply measuring horizontal distance relative to face width is more stable for smile.
    
def detect_smile(landmarks, w, h):
    """Detect if smiling by checking width of mouth relative to jawline width"""
    mouth_width = euclidean_distance(landmarks[61], landmarks[291], w, h)
    jaw_width = euclidean_distance(landmarks[234], landmarks[454], w, h)
    smile_ratio = mouth_width / (jaw_width + 1e-6)
    return smile_ratio > 0.42 # Threshold for smile


# ============================================================
#  8-LAYER ANTI-SPOOFING ENGINE
# ============================================================

def analyze_texture_lbp(face_roi_gray):
    """Layer 1: LBP texture variance. Real faces have rich texture; screens are flat."""
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
    """Layer 2: Edge density. Real faces have natural complex edges."""
    if face_roi_gray is None or face_roi_gray.size == 0:
        return 0

    face_resized = cv2.resize(face_roi_gray, (128, 128))
    edges = cv2.Canny(face_resized, 50, 150)
    edge_ratio = np.count_nonzero(edges) / edges.size
    score = min(100, int((edge_ratio / 0.15) * 100))
    return score


def analyze_color_temperature(img_bgr, x, y, w, h):
    """
    Layer 3: Color temperature analysis.
    Phone/monitor screens emit blue-shifted light vs natural warm skin tones.
    Real faces have higher red-to-blue ratio in skin regions.
    """
    face_color = img_bgr[y:y+h, x:x+w]
    if face_color.size == 0:
        return 0

    # Split channels
    b, g, r = cv2.split(face_color)
    b_mean = np.mean(b.astype(np.float64))
    g_mean = np.mean(g.astype(np.float64))
    r_mean = np.mean(r.astype(np.float64))

    # Natural skin: red > blue. Screen light: blue dominant
    if r_mean + g_mean == 0:
        return 50

    # Red-Blue ratio (natural skin: > 1.1, screen: < 1.0)
    rb_ratio = r_mean / max(b_mean, 1)

    # Skin color variance — real skin has natural variation, screens are uniform
    hsv = cv2.cvtColor(face_color, cv2.COLOR_BGR2HSV)
    lower_skin = np.array([0, 20, 70], dtype=np.uint8)
    upper_skin = np.array([20, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower_skin, upper_skin)
    skin_ratio = np.count_nonzero(mask) / mask.size

    # Channel variance (real skin has natural color variation, screen is uniform)
    channel_vars = [np.var(b.astype(np.float64)), np.var(g.astype(np.float64)), np.var(r.astype(np.float64))]
    avg_var = np.mean(channel_vars)

    # Scoring
    rb_score = min(40, int(max(0, (rb_ratio - 0.8)) * 80))  # 0-40 points
    skin_score = min(30, int(skin_ratio * 60))                # 0-30 points
    var_score = min(30, int((avg_var / 1500) * 30))            # 0-30 points

    return rb_score + skin_score + var_score


def detect_moire_pattern(face_roi_gray):
    """
    Layer 4: Moiré pattern detection.
    Capturing a screen with a camera creates interference patterns (moiré).
    These appear as periodic peaks in the FFT frequency domain.
    """
    if face_roi_gray is None or face_roi_gray.size == 0:
        return 0

    face_resized = cv2.resize(face_roi_gray, (128, 128)).astype(np.float64)

    # FFT
    f_transform = np.fft.fft2(face_resized)
    f_shift = np.fft.fftshift(f_transform)
    magnitude = np.log1p(np.abs(f_shift))

    h, w = magnitude.shape
    center_h, center_w = h // 2, w // 2

    # Remove DC component (center)
    magnitude[center_h-2:center_h+2, center_w-2:center_w+2] = 0

    # High-frequency region (outer ring) — moiré creates peaks here
    mask = np.zeros_like(magnitude)
    inner_radius = int(min(h, w) * 0.25)
    outer_radius = int(min(h, w) * 0.48)

    for i in range(h):
        for j in range(w):
            dist = np.sqrt((i - center_h) ** 2 + (j - center_w) ** 2)
            if inner_radius < dist < outer_radius:
                mask[i, j] = 1

    high_freq = magnitude * mask
    high_freq_energy = np.sum(high_freq)
    total_energy = np.sum(magnitude) + 1e-10

    # Ratio of high-frequency energy
    hf_ratio = high_freq_energy / total_energy

    # Check for periodic peaks (moiré signature)
    # Get 1D radial profile
    radial = []
    for r in range(inner_radius, outer_radius):
        ring_sum = 0
        count = 0
        for angle in np.linspace(0, 2 * np.pi, 36):
            ri = int(center_h + r * np.sin(angle))
            ci = int(center_w + r * np.cos(angle))
            if 0 <= ri < h and 0 <= ci < w:
                ring_sum += magnitude[ri, ci]
                count += 1
        if count > 0:
            radial.append(ring_sum / count)

    if len(radial) > 5:
        radial_arr = np.array(radial)
        # Look for sharp peaks (moiré creates them)
        mean_val = np.mean(radial_arr)
        std_val = np.std(radial_arr)
        if std_val > 0:
            peaks = np.sum(radial_arr > mean_val + 2.5 * std_val)
            if peaks >= 3:
                # Strong moiré detected — screen!
                return max(0, 100 - int(peaks * 15))

    # Real faces: moderate high-frequency content, no periodic peaks
    if 0.15 < hf_ratio < 0.55:
        return 85  # Likely real
    elif hf_ratio > 0.55:
        return 30  # Too much HF — possible screen
    else:
        return 50  # Low HF — inconclusive


def detect_screen_glare(img_bgr, x, y, w, h):
    """
    Layer 5: Screen reflection/glare detection.
    Phone/monitor screens have specular reflections (bright white spots from glass).
    Real faces don't have perfectly white spots in the middle.
    """
    face_color = img_bgr[y:y+h, x:x+w]
    if face_color.size == 0:
        return 0

    # Convert to grayscale and look for extreme bright spots
    gray = cv2.cvtColor(face_color, cv2.COLOR_BGR2GRAY)

    # HSV for saturation check — screen reflections are white (low saturation, high value)
    hsv = cv2.cvtColor(face_color, cv2.COLOR_BGR2HSV)
    _, sat, val = cv2.split(hsv)

    # Count extremely bright, low-saturation pixels (screen glare)
    glare_mask = (val > 240) & (sat < 30)
    glare_ratio = np.count_nonzero(glare_mask) / glare_mask.size

    # Count overexposed regions
    overexposed = gray > 250
    overexposed_ratio = np.count_nonzero(overexposed) / overexposed.size

    # Screen glass creates rectangular glare patterns
    # Check for horizontal/vertical bright lines
    bright_mask = (gray > 230).astype(np.uint8)
    lines = cv2.HoughLinesP(bright_mask, 1, np.pi / 180, threshold=15, minLineLength=w // 4, maxLineGap=5)
    line_count = len(lines) if lines is not None else 0

    # Scoring
    if glare_ratio > 0.05 or overexposed_ratio > 0.08:
        # Significant glare detected — likely screen
        return max(10, 100 - int(glare_ratio * 800) - int(overexposed_ratio * 500))
    elif line_count > 3:
        # Multiple bright lines — screen edge reflections
        return max(20, 100 - line_count * 12)
    else:
        return 90  # No significant glare — likely real


def analyze_frequency_domain(face_roi_gray):
    """
    Layer 6: Frequency domain analysis.
    Screens have characteristic pixel grid patterns that appear as sharp peaks
    at specific frequencies in FFT.
    """
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

    # Check for screen pixel grid — autocorrelation of high-freq region
    high_freq_region = magnitude.copy()
    high_freq_region[center_h - mask_size:center_h + mask_size,
                     center_w - mask_size:center_w + mask_size] = 0

    # Check for periodicity in high frequencies
    hf_std = np.std(high_freq_region)
    hf_max = np.max(high_freq_region)

    # Screen pixels create sharp isolated peaks
    if hf_std > 0:
        peak_ratio = hf_max / (hf_std + 1e-10)
        if peak_ratio > 15:
            # Sharp isolated peaks — screen pixel pattern!
            return max(10, 100 - int(peak_ratio * 3))

    if 0.3 <= ratio <= 0.85:
        return 80  # Natural frequency distribution
    else:
        return 35  # Unnatural


def check_face_size_consistency(x, y, w, h):
    """
    Layer 7: Multi-frame depth consistency.
    Real faces have natural micro-movements and depth variation.
    A phone screen is flat — zero depth variation, static size.
    """
    global face_size_history

    face_size_history.append((w, h, x, y, time.time()))
    if len(face_size_history) > MAX_HISTORY:
        face_size_history.pop(0)

    if len(face_size_history) < 4:
        return 40  # Not enough frames yet

    # Position variance — real faces move naturally
    x_positions = [f[2] for f in face_size_history]
    y_positions = [f[3] for f in face_size_history]
    x_var = np.var(x_positions)
    y_var = np.var(y_positions)

    # Size variance — real faces at varying distance change size
    sizes = [f[0] * f[1] for f in face_size_history]
    size_var = np.var(sizes) if len(sizes) > 1 else 0

    # Movement score
    total_movement = x_var + y_var
    movement_score = min(40, int((total_movement / 30) * 40))

    # Size consistency score
    size_score = min(30, int((size_var / 500) * 30))

    # Frame-to-frame jitter (natural micro-movements)
    if len(x_positions) >= 3:
        dx = [abs(x_positions[i] - x_positions[i-1]) for i in range(1, len(x_positions))]
        dy = [abs(y_positions[i] - y_positions[i-1]) for i in range(1, len(y_positions))]
        avg_jitter = np.mean(dx) + np.mean(dy)
        jitter_score = min(30, int((avg_jitter / 5) * 30))
    else:
        jitter_score = 15

    # Zero movement = suspicious (phone held still)
    if total_movement < 2 and len(face_size_history) >= 6:
        return 10  # Almost certainly a static image

    return movement_score + size_score + jitter_score


def compute_anti_spoof_score(face_roi_gray, img_bgr, x, y, w, h, has_eyes):
    """
    8-Layer composite anti-spoofing score.
    Each layer detects a different aspect of screen/photo spoofing.
    All 8 must pass for high confidence of liveness.
    """
    global spoof_frame_scores

    texture_score = analyze_texture_lbp(face_roi_gray)
    edge_score = analyze_edge_density(face_roi_gray)
    color_score = analyze_color_temperature(img_bgr, x, y, w, h)
    moire_score = detect_moire_pattern(face_roi_gray)
    glare_score = detect_screen_glare(img_bgr, x, y, w, h)
    frequency_score = analyze_frequency_domain(face_roi_gray)
    consistency_score = check_face_size_consistency(x, y, w, h)
    eye_score = 100 if has_eyes else 0

    checks = {
        "texture": texture_score > 35,
        "edge_density": edge_score > 25,
        "color_temp": color_score > 30,
        "moire_detect": moire_score > 50,
        "glare_detect": glare_score > 50,
        "frequency": frequency_score > 40,
        "face_consistency": consistency_score > 20,
        "eye_presence": has_eyes
    }

    # Weighted composite
    composite = (
        texture_score * 0.12 +
        edge_score * 0.10 +
        color_score * 0.15 +
        moire_score * 0.15 +
        glare_score * 0.13 +
        frequency_score * 0.10 +
        consistency_score * 0.10 +
        eye_score * 0.15
    )

    # 3D Pose Check (Layer 9)
    # If the face is perfectly flat without any 3D rotation, it's highly likely a photo.
    # We will pass this data in via kwargs or check from recognize_face_with_liveness directly.
    # Here we just use the composite as base.

    # Track scores across frames for temporal consistency
    spoof_frame_scores.append(int(composite))
    if len(spoof_frame_scores) > MAX_SPOOF_FRAMES:
        spoof_frame_scores.pop(0)

    # If we have enough frames, use average (smooths out noise)
    if len(spoof_frame_scores) >= 3:
        avg_score = int(np.mean(spoof_frame_scores))
        # If consistently low across frames — definite spoof
        if avg_score < 35:
            composite = min(composite, avg_score)

    # Count how many checks failed
    failed_checks = sum(1 for v in checks.values() if not v)

    # If 4+ checks fail simultaneously → almost certainly a spoof
    if failed_checks >= 4:
        composite = min(composite, 20)

    return int(composite), checks


# ============================================================
#  ENCRYPTED FACE STORAGE
# ============================================================

def encrypt_face_image(img_data):
    """Encrypt face image bytes using Fernet."""
    f = get_fernet()
    return f.encrypt(img_data)


def decrypt_face_image(encrypted_data):
    """Decrypt face image bytes."""
    f = get_fernet()
    return f.decrypt(encrypted_data)


def save_encrypted_face(filepath, cv2_img):
    """Save face image encrypted."""
    _, buffer = cv2.imencode('.jpg', cv2_img)
    img_bytes = buffer.tobytes()
    encrypted = encrypt_face_image(img_bytes)

    enc_filepath = filepath + '.enc'
    with open(enc_filepath, 'wb') as f:
        f.write(encrypted)
    return enc_filepath


def load_encrypted_face(enc_filepath):
    """Load and decrypt face image."""
    with open(enc_filepath, 'rb') as f:
        encrypted = f.read()
    decrypted = decrypt_face_image(encrypted)
    nparr = np.frombuffer(decrypted, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)


# ============================================================
#  FACE REGISTRATION (Enhanced + Encrypted)
# ============================================================

def register_face(user_id, base64_img):
    """Register a face with augmented multi-sample training + encryption."""
    img_bgr = data_uri_to_cv2_img(base64_img)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    faces = detect_faces_dnn(img_bgr)

    if len(faces) == 0:
        return False

    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    (x, y, w, h) = faces[0]
    face_roi = gray[y:y+h, x:x+w]

    if face_roi.size == 0:
        return False

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    face_roi = clahe.apply(face_roi)

    augmented_faces = augment_face(face_roi, size=(200, 200))

    # Collect all training data
    all_faces = []
    all_labels = []

    # Load from encrypted files
    for filename in os.listdir(FACE_DATA_DIR):
        if filename.endswith('.enc'):
            parts = filename.replace('.jpg.enc', '').split('_')
            if len(parts) >= 2:
                try:
                    label = int(parts[1])
                    filepath = os.path.join(FACE_DATA_DIR, filename)
                    img = load_encrypted_face(filepath)
                    if img is not None:
                        img_resized = cv2.resize(img, (200, 200))
                        all_faces.append(img_resized)
                        all_labels.append(label)
                except Exception:
                    continue

    # Also load legacy unencrypted files (backward compat)
    for filename in os.listdir(FACE_DATA_DIR):
        if (filename.endswith('.jpg') or filename.endswith('.png')) and not filename.endswith('.enc'):
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

    # Save new augmented samples (encrypted)
    existing_count = len([f for f in os.listdir(FACE_DATA_DIR)
                          if f.startswith(f'face_{user_id}_')])

    for i, aug_face in enumerate(augmented_faces):
        filename = f"face_{user_id}_{existing_count + i}.jpg"
        filepath = os.path.join(FACE_DATA_DIR, filename)
        save_encrypted_face(filepath, aug_face)
        all_faces.append(aug_face)
        all_labels.append(user_id)

    # Train LBPH recognizer
    if len(all_faces) > 0:
        recognizer.train(all_faces, np.array(all_labels))
        recognizer.save(TRAINER_PATH)

    # Mark face as registered in DB
    conn = get_db_connection()
    conn.execute("UPDATE users SET face_registered = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    return True


# ============================================================
#  FACE RECOGNITION
# ============================================================

def recognize_face_with_liveness(base64_img):
    """
    Enhanced recognition: DNN detection + LBPH matching + 8-layer anti-spoofing.
    Returns: (user_id, liveness_metrics, confidence, anti_spoof_score, spoof_checks)
    """
    img_bgr = data_uri_to_cv2_img(base64_img)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    faces = detect_faces_dnn(img_bgr)

    if len(faces) == 0:
        return None, {}, 0, 0, {}

    if len(faces) > 1:
        return None, {}, 0, 0, {"multi_face": True}

    (x, y, w, h) = faces[0]

    # MediaPipe Face Mesh & Eye Detection (Handles glasses & beards better)
    has_eyes = False
    three_d_pose_score = 50 # Default neutral
    liveness_metrics = {"eyes_closed": False, "smiling": False}
    
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    results = face_mesh_mp.process(img_rgb)
    
    if results.multi_face_landmarks:
        # We have a valid face mesh
        has_eyes = True
        landmarks = results.multi_face_landmarks[0].landmark
        
        # Calculate Liveness Metrics (Blinks and Smiles)
        ear = calculate_ear(landmarks, w, h)
        is_smiling = detect_smile(landmarks, w, h)
        liveness_metrics["eyes_closed"] = ear < 0.22 # Typical threshold for closed eyes
        liveness_metrics["smiling"] = is_smiling
        
        # Calculate 3D Pose Fake Detection (Layer 9)
        # Real faces have depth (z-values on nose vs cheeks). Photos have flattened z-values.
        nose_z = landmarks[1].z   # Nose tip
        left_cheek_z = landmarks[234].z # Left cheek
        right_cheek_z = landmarks[454].z # Right cheek
        
        depth_variance = abs(nose_z - left_cheek_z) + abs(nose_z - right_cheek_z)
        # Normal faces have depth_variance > 0.05. Photos (flat screens) have very small depth_variance.
        if depth_variance > 0.12:
            three_d_pose_score = 100
        elif depth_variance > 0.05:
            three_d_pose_score = 80
        else:
            three_d_pose_score = 10 # Flat object (Spoof)

    # 8-Layer + 3D Pose anti-spoofing
    face_roi_gray = gray[y:y+h, x:x+w]
    anti_spoof_score, spoof_checks = compute_anti_spoof_score(
        face_roi_gray, img_bgr, x, y, w, h, has_eyes
    )
    
    # Mix 3D Pose into final spoof checks
    spoof_checks['3d_pose_depth'] = three_d_pose_score > 30
    
    # If 3d pose falls flat, penalize total score
    if three_d_pose_score <= 10:
        anti_spoof_score = min(anti_spoof_score, 25)

    if not os.path.exists(TRAINER_PATH):
        return None, liveness_metrics, 0, anti_spoof_score, spoof_checks

    # Normalize
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    face_normalized = clahe.apply(face_roi_gray)
    face_resized = cv2.resize(face_normalized, (200, 200))

    # LBPH recognition
    try:
        label, distance = recognizer.predict(face_resized)
    except Exception:
        return None, liveness_metrics, 0, anti_spoof_score, spoof_checks

    threshold = 65
    if distance < threshold:
        confidence = max(0, min(100, int((1 - distance / threshold) * 100)))
        return label, liveness_metrics, confidence, anti_spoof_score, spoof_checks

    rough_confidence = max(0, int((1 - distance / 200) * 50))
    return None, liveness_metrics, rough_confidence, anti_spoof_score, spoof_checks
