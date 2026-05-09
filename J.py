import zipfile
import os

with zipfile.ZipFile("best.pt", "w") as zf:
    for root, dirs, files in os.walk("best"):
        for file in files:
            filepath = os.path.join(root, file)
            arcname = os.path.relpath(filepath, os.path.dirname("best"))
            zf.write(filepath, arcname)

print("Done! best.pt created.")
