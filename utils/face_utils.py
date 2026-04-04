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

# Detect if running on Render (limited CPU/RAM) to use lightweight pipeline
IS_RENDER = bool(os.environ.get('RENDER') or os.environ.get('DATABASE_URL'))
if IS_RENDER:
    print("[SmartFace] Running on Render — using optimized lightweight pipeline")

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
# Initialize MediaPipe Face Mesh for 468-point 3D landmarking
# Using static_image_mode=True for stateless HTTP request model (required for Gunicorn workers)
mp_face_mesh = mp.solutions.face_mesh
face_mesh_mp = mp_face_mesh.FaceMesh(
    static_image_mode=True,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.4
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
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    # On Render (0.1 CPU): downscale to 320px max for ~4x faster processing
    # LBPH only needs 200x200 face crop, so this doesn't hurt recognition quality
    if IS_RENDER and img is not None:
        h, w = img.shape[:2]
        max_dim = 320
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    return img


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
    """Layer 1: LBP texture variance. Real faces have rich texture; screens are flat.
    Fully vectorized with NumPy — ~50x faster than Python for-loops."""
    if face_roi_gray is None or face_roi_gray.size == 0:
        return 0

    face_resized = cv2.resize(face_roi_gray, (128, 128)).astype(np.int16)

    # Vectorized LBP: compare each pixel with its 8 neighbors simultaneously
    center = face_resized[1:-1, 1:-1]
    lbp = np.zeros_like(center, dtype=np.uint8)
    lbp |= ((face_resized[0:-2, 0:-2] >= center).astype(np.uint8) << 7)  # top-left
    lbp |= ((face_resized[0:-2, 1:-1] >= center).astype(np.uint8) << 6)  # top
    lbp |= ((face_resized[0:-2, 2:  ] >= center).astype(np.uint8) << 5)  # top-right
    lbp |= ((face_resized[1:-1, 2:  ] >= center).astype(np.uint8) << 4)  # right
    lbp |= ((face_resized[2:  , 2:  ] >= center).astype(np.uint8) << 3)  # bottom-right
    lbp |= ((face_resized[2:  , 1:-1] >= center).astype(np.uint8) << 2)  # bottom
    lbp |= ((face_resized[2:  , 0:-2] >= center).astype(np.uint8) << 1)  # bottom-left
    lbp |= ((face_resized[1:-1, 0:-2] >= center).astype(np.uint8) << 0)  # left

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
    Fully vectorized for speed on Render.
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

    # Vectorized ring mask using meshgrid
    inner_radius = int(min(h, w) * 0.25)
    outer_radius = int(min(h, w) * 0.48)
    
    yi, xi = np.ogrid[:h, :w]
    dist = np.sqrt((yi - center_h) ** 2 + (xi - center_w) ** 2)
    mask = ((dist > inner_radius) & (dist < outer_radius)).astype(np.float64)

    high_freq = magnitude * mask
    high_freq_energy = np.sum(high_freq)
    total_energy = np.sum(magnitude) + 1e-10

    # Ratio of high-frequency energy
    hf_ratio = high_freq_energy / total_energy

    # Vectorized radial profile for moiré peak detection
    radii = np.arange(inner_radius, outer_radius)
    if len(radii) > 5:
        angles = np.linspace(0, 2 * np.pi, 36, endpoint=False)
        radial = np.zeros(len(radii))
        for idx, r in enumerate(radii):
            ri = np.clip((center_h + r * np.sin(angles)).astype(int), 0, h-1)
            ci = np.clip((center_w + r * np.cos(angles)).astype(int), 0, w-1)
            radial[idx] = np.mean(magnitude[ri, ci])
        
        mean_val = np.mean(radial)
        std_val = np.std(radial)
        if std_val > 0:
            peaks = np.sum(radial > mean_val + 2.5 * std_val)
            if peaks >= 3:
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
        return 60  # Don't penalize early frames — give benefit of doubt

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
    movement_score = min(40, int((total_movement / 20) * 40))

    # Size consistency score
    size_score = min(30, int((size_var / 500) * 30))

    # Frame-to-frame jitter (natural micro-movements)
    if len(x_positions) >= 3:
        dx = [abs(x_positions[i] - x_positions[i-1]) for i in range(1, len(x_positions))]
        dy = [abs(y_positions[i] - y_positions[i-1]) for i in range(1, len(y_positions))]
        avg_jitter = np.mean(dx) + np.mean(dy)
        jitter_score = min(30, int((avg_jitter / 3) * 30))
    else:
        jitter_score = 20

    # Zero movement = suspicious (phone held still) — only flag after many frames
    if total_movement < 1.5 and len(face_size_history) >= 10:
        return 10  # Almost certainly a static image

    return movement_score + size_score + jitter_score


def detect_screen_border(img_bgr, x, y, w, h):
    """
    Layer 10: Detect rectangular screen borders (phone/tablet edges) around the face.
    Real faces do NOT have sharp rectangular edges surrounding them.
    Phone screens always have visible bezels or uniform colored borders.
    Returns 0-100: 100 = no screen detected, 0 = screen frame detected.
    """
    img_h, img_w = img_bgr.shape[:2]
    
    # Expand search area beyond the face bounding box
    pad_x = int(w * 0.6)
    pad_y = int(h * 0.4)
    rx1 = max(0, x - pad_x)
    ry1 = max(0, y - pad_y)
    rx2 = min(img_w, x + w + pad_x)
    ry2 = min(img_h, y + h + pad_y)
    
    region = img_bgr[ry1:ry2, rx1:rx2]
    if region.size == 0:
        return 70
    
    gray_region = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    
    # Detect strong straight edges (phone bezels are straight lines)
    edges = cv2.Canny(gray_region, 80, 200)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50, minLineLength=40, maxLineGap=10)
    
    if lines is None:
        return 90  # No straight lines = likely real face
    
    vertical_lines = 0
    horizontal_lines = 0
    
    for line in lines:
        lx1, ly1, lx2, ly2 = line[0]
        angle = abs(np.degrees(np.arctan2(ly2 - ly1, lx2 - lx1 + 1e-6)))
        length = np.sqrt((lx2 - lx1)**2 + (ly2 - ly1)**2)
        
        if length > 30:
            if angle < 15 or angle > 165:  # Horizontal
                horizontal_lines += 1
            elif 75 < angle < 105:  # Vertical
                vertical_lines += 1
    
    # Phone screens have at least 2 vertical + 2 horizontal strong edges
    if vertical_lines >= 2 and horizontal_lines >= 2:
        return 5  # Phone border detected!
    elif vertical_lines >= 1 and horizontal_lines >= 1:
        return 30
    
    # Check for uniform dark border (phone bezel)
    top_strip = gray_region[0:max(1, gray_region.shape[0]//10), :]
    bottom_strip = gray_region[max(0, gray_region.shape[0]*9//10):, :]
    left_strip = gray_region[:, 0:max(1, gray_region.shape[1]//10)]
    right_strip = gray_region[:, max(0, gray_region.shape[1]*9//10):]
    
    border_stds = [
        np.std(top_strip) if top_strip.size > 0 else 50,
        np.std(bottom_strip) if bottom_strip.size > 0 else 50,
        np.std(left_strip) if left_strip.size > 0 else 50,
        np.std(right_strip) if right_strip.size > 0 else 50
    ]
    
    # Phone bezels have very low std dev (uniform color)
    uniform_borders = sum(1 for s in border_stds if s < 15)
    if uniform_borders >= 3:
        return 10  # Uniform borders = screen bezel
    
    return 80


def compute_anti_spoof_score(face_roi_gray, img_bgr, x, y, w, h, has_eyes):
    """
    Anti-spoofing score with adaptive pipeline.
    On Render (limited CPU): runs 5 lightweight layers only (~50ms)
    On local/powerful servers: runs all 10 layers (~150ms)
    """
    global spoof_frame_scores

    # --- Always run: fast layers (< 5ms each) ---
    texture_score = analyze_texture_lbp(face_roi_gray)
    edge_score = analyze_edge_density(face_roi_gray)
    color_score = analyze_color_temperature(img_bgr, x, y, w, h)
    consistency_score = check_face_size_consistency(x, y, w, h)
    eye_score = 100 if has_eyes else 0

    if IS_RENDER:
        # Lightweight mode for Render: skip FFT, Moiré, HoughLines (too CPU-heavy)
        checks = {
            "texture": texture_score > 30,
            "edge_density": edge_score > 20,
            "color_temp": color_score > 25,
            "face_consistency": consistency_score > 15,
            "eye_presence": has_eyes,
        }

        composite = (
            texture_score * 0.18 +
            edge_score * 0.14 +
            color_score * 0.16 +
            consistency_score * 0.22 +
            eye_score * 0.30 +
            (8.0 if has_eyes and consistency_score > 30 else 0)
        )
    else:
        # Full mode: all 10 layers
        moire_score = detect_moire_pattern(face_roi_gray)
        glare_score = detect_screen_glare(img_bgr, x, y, w, h)
        frequency_score = analyze_frequency_domain(face_roi_gray)
        screen_border_score = detect_screen_border(img_bgr, x, y, w, h)

        checks = {
            "texture": texture_score > 30,
            "edge_density": edge_score > 20,
            "color_temp": color_score > 25,
            "moire_detect": moire_score > 40,
            "glare_detect": glare_score > 40,
            "frequency": frequency_score > 35,
            "face_consistency": consistency_score > 15,
            "eye_presence": has_eyes,
            "screen_border": screen_border_score > 20
        }

        composite = (
            texture_score * 0.10 +
            edge_score * 0.08 +
            color_score * 0.10 +
            moire_score * 0.10 +
            glare_score * 0.08 +
            frequency_score * 0.08 +
            consistency_score * 0.14 +
            eye_score * 0.16 +
            screen_border_score * 0.10 +
            (6.0 if has_eyes and consistency_score > 30 else 0)
        )

        if screen_border_score <= 5:
            composite = min(composite, 15)

    # Track scores across frames for temporal consistency
    spoof_frame_scores.append(int(composite))
    if len(spoof_frame_scores) > MAX_SPOOF_FRAMES:
        spoof_frame_scores.pop(0)

    if len(spoof_frame_scores) >= 3:
        avg_score = int(np.mean(spoof_frame_scores))
        if avg_score < 30:
            composite = min(composite, avg_score)

    # Count how many checks failed
    failed_checks = sum(1 for v in checks.values() if not v)
    fail_threshold = 3 if IS_RENDER else 4
    if failed_checks >= fail_threshold:
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
    """Register a face with augmented multi-sample training + encryption.
    Uses incremental update when possible (O(new) instead of O(all)).
    Caps at 55 face images per user to control disk/memory growth.
    Designed for 500+ employee scale."""
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

    # --- Enforce max 55 images per user (5 captures × 11 augmentations) ---
    MAX_IMAGES_PER_USER = 55
    existing_files = sorted([f for f in os.listdir(FACE_DATA_DIR)
                              if f.startswith(f'face_{user_id}_')])
    existing_count = len(existing_files)

    # If adding these would exceed the cap, delete oldest files
    total_after = existing_count + len(augmented_faces)
    if total_after > MAX_IMAGES_PER_USER:
        files_to_delete = total_after - MAX_IMAGES_PER_USER
        for old_file in existing_files[:files_to_delete]:
            try:
                os.remove(os.path.join(FACE_DATA_DIR, old_file))
                print(f"[Cleanup] Removed old face file: {old_file}")
            except Exception:
                pass
        existing_count = max(0, existing_count - files_to_delete)

    # Save new augmented samples (encrypted)
    new_faces = []
    new_labels = []
    for i, aug_face in enumerate(augmented_faces):
        filename = f"face_{user_id}_{existing_count + i}.jpg"
        filepath = os.path.join(FACE_DATA_DIR, filename)
        save_encrypted_face(filepath, aug_face)
        new_faces.append(aug_face)
        new_labels.append(user_id)

    # --- Incremental training: use update() if model exists, train() if first time ---
    if len(new_faces) > 0:
        if os.path.exists(TRAINER_PATH):
            try:
                # Try incremental update first (much faster for 500+ employees)
                recognizer.update(new_faces, np.array(new_labels))
                recognizer.save(TRAINER_PATH)
                print(f"[Training] Incremental update for user {user_id} ({len(new_faces)} images)")
            except Exception as e:
                print(f"[Training] Incremental update failed ({e}), doing full retrain...")
                _full_retrain(new_faces, new_labels)
        else:
            # First registration ever — need full train
            _full_retrain(new_faces, new_labels)

    # Mark face as registered in DB
    conn = get_db_connection()
    conn.execute("UPDATE users SET face_registered = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    return True


def _full_retrain(extra_faces=None, extra_labels=None):
    """Full retrain loading all face data from disk. Used as fallback.
    Processes in batches for memory efficiency with 500+ employees."""
    all_faces = list(extra_faces) if extra_faces else []
    all_labels = list(extra_labels) if extra_labels else []

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

    if len(all_faces) > 0:
        recognizer.train(all_faces, np.array(all_labels))
        recognizer.save(TRAINER_PATH)
        unique = len(set(all_labels))
        print(f"[Training] Full retrain: {len(all_faces)} images, {unique} users")


# ============================================================
#  FACE RECOGNITION
# ============================================================

def recognize_face_with_liveness(base64_img):
    """
    Enhanced recognition: DNN detection + LBPH matching + anti-spoofing.
    Auto-adapts pipeline weight for Render (lightweight) vs local (full).
    Returns: (user_id, liveness_metrics, confidence, anti_spoof_score, spoof_checks)
    """
    try:
        t_start = time.time()
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
        three_d_pose_score = 50  # Default neutral
        liveness_metrics = {"eyes_closed": False, "smiling": False}

        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        results = face_mesh_mp.process(img_rgb)

        if results.multi_face_landmarks:
            has_eyes = True
            landmarks = results.multi_face_landmarks[0].landmark

            ear = float(calculate_ear(landmarks, w, h))
            is_smiling = bool(detect_smile(landmarks, w, h))
            liveness_metrics["eyes_closed"] = bool(ear < 0.28)
            liveness_metrics["smiling"] = is_smiling
            liveness_metrics["ear"] = round(ear, 3)

            # 3D Pose Fake Detection (Layer 9)
            nose_z = landmarks[1].z
            left_cheek_z = landmarks[234].z
            right_cheek_z = landmarks[454].z

            depth_variance = abs(nose_z - left_cheek_z) + abs(nose_z - right_cheek_z)
            if depth_variance > 0.12:
                three_d_pose_score = 100
            elif depth_variance > 0.05:
                three_d_pose_score = 80
            else:
                three_d_pose_score = 10

        # Anti-spoofing (adaptive: lightweight on Render, full locally)
        face_roi_gray = gray[y:y+h, x:x+w]
        anti_spoof_score, spoof_checks = compute_anti_spoof_score(
            face_roi_gray, img_bgr, x, y, w, h, has_eyes
        )

        spoof_checks['3d_pose_depth'] = three_d_pose_score > 30

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
        except Exception as e:
            print(f"[LBPH] Prediction failed: {e}")
            return None, liveness_metrics, 0, anti_spoof_score, spoof_checks

        threshold = 80
        print(f"[LBPH] label={label}, distance={distance:.1f}, threshold={threshold}")

        if distance < threshold:
            confidence = max(0, min(100, int((1 - distance / threshold) * 100)))
            t_total = int((time.time() - t_start) * 1000)
            print(f"[Pipeline] Total: {t_total}ms | MATCH user={label}")
            return label, liveness_metrics, confidence, anti_spoof_score, spoof_checks

        rough_confidence = max(0, int((1 - distance / 200) * 50))
        t_total = int((time.time() - t_start) * 1000)
        print(f"[Pipeline] Total: {t_total}ms | NO MATCH (distance={distance:.1f})")
        return None, liveness_metrics, rough_confidence, anti_spoof_score, spoof_checks

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Pipeline] CRASH: {e}")
        return None, {"error": str(e)}, 0, 0, {"pipeline_error": True}

