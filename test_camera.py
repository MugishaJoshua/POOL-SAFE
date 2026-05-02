import cv2

cap = cv2.VideoCapture(0)

if cap.isOpened():
    print("✅ Camera opened successfully")
    ret, frame = cap.read()
    if ret:
        print("✅ Frame captured successfully")
        cv2.imwrite("test_frame.jpg", frame)
        print("✅ Saved test_frame.jpg — check your project folder")
    else:
        print("❌ Camera opened but couldn't read frame")
    cap.release()
else:
    print("❌ Cannot open camera at index 0")
    print("Trying index 1...")
    cap = cv2.VideoCapture(1)
    if cap.isOpened():
        print("✅ Camera found at index 1 — use CAMERA_SOURCE = 1")
        cap.release()
    else:
        print("❌ No camera found at index 0 or 1")