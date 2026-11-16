"""Unicode-safe image read/write (cv2.imread cannot open non-ASCII Windows paths)."""
import cv2
import numpy as np


def imread(path):
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)


def imwrite_jpg(path, img, quality=95):
    ok, data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG 編碼失敗")
    data.tofile(path)
