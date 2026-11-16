# FusionStitch 程式（app\*.py）會用到的每個模組都列在這裡，PyInstaller 才會包進 EXE。
# 由 app\*.py 的 import 自動整理，再多列一些標準庫：以後只換程式（不換 EXE）也能用到。
import array, ast, atexit, base64, bisect, calendar, collections, colorsys, concurrent.futures, contextlib  # noqa
import copy, csv, ctypes, ctypes.wintypes, dataclasses, datetime, decimal, email, enum, fnmatch, fractions  # noqa
import functools, glob, gzip, hashlib, heapq, html, http.client, http.server, importlib, importlib.util, io  # noqa
import itertools, json, locale, math, mimetypes, os, pathlib, platform, pprint, py_compile, queue, random, re  # noqa
import runpy, secrets, selectors, shlex, shutil, signal, socket, socketserver, sqlite3, ssl, statistics  # noqa
import string, struct, subprocess, sys, tempfile, textwrap, threading, time, traceback, typing, unicodedata  # noqa
import urllib.error, urllib.parse, urllib.request, uuid, wave, weakref, xml.etree.ElementTree, zipfile  # noqa
import PySide6.QtCore  # noqa
import PySide6.QtGui  # noqa
import PySide6.QtMultimedia  # noqa
import PySide6.QtNetwork  # noqa
import PySide6.QtOpenGL  # noqa
import PySide6.QtOpenGLWidgets  # noqa
import PySide6.QtWidgets  # noqa
import certifi  # noqa
import cv2  # noqa
import glcontext  # noqa
import glcontext.wgl  # noqa
import moderngl  # noqa
import numpy  # noqa
import psutil  # noqa
import scipy.interpolate  # noqa
import scipy.optimize  # noqa
