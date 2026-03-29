import os
import cv2
import numpy as np

FACE_DATA_DIR = "face_data"
TRAINER_PATH = os.path.join(FACE_DATA_DIR, "trainer.yml")

recognizer = cv2.face.LBPHFaceRecognizer_create(
    radius=1,
    neighbors=8,
    grid_x=8,
    grid_y=8
)

all_faces = []
all_labels = []

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

if len(all_faces) > 0:
    print(f"Training on {len(all_faces)} images...")
    recognizer.train(all_faces, np.array(all_labels))
    recognizer.save(TRAINER_PATH)
    print("Training complete. File saved to", TRAINER_PATH)
    print("File size:", os.path.getsize(TRAINER_PATH), "bytes")
else:
    print("No images found to train.")
