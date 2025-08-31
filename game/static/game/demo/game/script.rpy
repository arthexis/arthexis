init python:
    import os
    import base64
    import renpy

    b64_path = renpy.file("silhouette.png.b64")
    img_path = renpy.loader.transfn("silhouette.png")
    if not os.path.exists(img_path):
        with open(img_path, "wb") as img:
            img.write(base64.b64decode(b64_path.read()))

image silhouette = "silhouette.png"

label start:
    show silhouette
    "Hello World!"
    return
