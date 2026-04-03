"""
SmartFace Retrain Script
========================
Retrains the LBPH face recognition model from all face data.
Supports both encrypted (.enc) and legacy unencrypted (.jpg) face files.

Usage: python retrain.py
"""

import os
import cv2
import numpy as np

FACE_DATA_DIR = "face_data"
TRAINER_PATH = os.path.join(FACE_DATA_DIR, "trainer.yml")

recognizer = cv2.face.LBPHFaceRecognizer_create(
    radius=1, neighbors=8, grid_x=8, grid_y=8
)

all_faces = []
all_labels = []

# Load encrypted face files
try:
    from utils.face_utils import load_encrypted_face
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
                except Exception as e:
                    print(f"  Skipping {filename}: {e}")
except ImportError:
    print("Warning: Cannot load encrypted faces (missing dependencies)")

# Load legacy unencrypted files
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
    unique_labels = set(all_labels)
    print(f"Training on {len(all_faces)} images from {len(unique_labels)} user(s)...")
    recognizer.train(all_faces, np.array(all_labels))
    recognizer.save(TRAINER_PATH)
    print(f"Training complete! Model saved to {TRAINER_PATH}")
    print(f"File size: {os.path.getsize(TRAINER_PATH):,} bytes")
else:
    print("No face data found. Register employees first via the web app.")
