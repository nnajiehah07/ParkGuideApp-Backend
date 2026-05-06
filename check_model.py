from monitoring.services import load_detection_model
m = load_detection_model()
if m is None:
    print("Model not found or ultralytics not available.")
else:
    print("Loaded model. Class names:", getattr(m,"names", None))
